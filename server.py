"""
CAD Studio — Unified API server for AI + webui CAD modeling.
Engine: node graphs transpiled to build123d and run in an isolated worker.
"""

import collections
import itertools
import json
import logging
import os
import shutil
import signal
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Node-based CAD engine (pure imports; build123d only used in the subprocess)
from cad_nodes import api, catalog
from cad_nodes.graph import Graph, ValidationError
from cad_nodes.transpiler import transpile, transpile_with_map
from cad_nodes.executor import execute_graph, export_graph, extract_subshapes_for_node
from cad_nodes.store import GraphStore
from cad_nodes.copilot import run_chat, copilot_status

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECTS_DIR = Path("/app/projects")
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

# User feedback/report drops (see docs/FEEDBACK_FIX_GUIDE.md). A dedicated rw
# volume kept out of projects/ so a coding agent can find them at the repo root.
FEEDBACK_DIR = Path("/app/feedback")
FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

APP_VERSION = "0.1.0"

app = FastAPI(title="CAD Studio", version="0.1.0")

# Serve webui static files
app.mount("/static", StaticFiles(directory="/app/webui"), name="static")


# ---------------------------------------------------------------------------
# System: backend log capture (ring buffer) + health/uptime
# ---------------------------------------------------------------------------
# A small in-memory ring buffer that the UI polls via /api/system/logs so the
# uvicorn + app logs are visible in-app (no terminal needed). Capped so it can't
# grow unbounded; each entry carries a monotonic seq for incremental polling.
_BOOT_TIME = time.time()
_LOG_BUFFER: "collections.deque[dict]" = collections.deque(maxlen=2000)
_LOG_SEQ = itertools.count(1)
_LOG_LOCK = threading.Lock()

logger = logging.getLogger("cad_studio")


class _RingBufferHandler(logging.Handler):
    """Logging handler that appends formatted records into _LOG_BUFFER."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        # Don't let the UI's own health/log pollers spam the console buffer.
        if record.name == "uvicorn.access" and ("/api/system/logs" in msg or "/api/system/health" in msg):
            return
        with _LOG_LOCK:
            _LOG_BUFFER.append({
                "seq": next(_LOG_SEQ),
                "ts": record.created,
                "level": record.levelname.lower(),
                "source": "backend",
                "logger": record.name,
                "msg": msg,
            })


_RING_HANDLER = _RingBufferHandler()
_RING_HANDLER.setLevel(logging.INFO)


def _install_log_capture() -> None:
    """Attach the ring-buffer handler to the root + uvicorn loggers.

    Called at import AND on startup: uvicorn reconfigures logging when it boots,
    so re-attaching after startup guarantees access/error logs are captured.
    """
    root = logging.getLogger()
    if _RING_HANDLER not in root.handlers:
        root.addHandler(_RING_HANDLER)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    # Capture only at the root and let uvicorn's loggers propagate up, so each
    # record lands in the buffer exactly once (no double-capture).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.propagate = True
        if _RING_HANDLER in lg.handlers:
            lg.removeHandler(_RING_HANDLER)


_install_log_capture()


@app.on_event("startup")
async def _on_startup() -> None:
    _install_log_capture()
    logger.info("CAD Studio backend ready (pid %s)", os.getpid())


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class CopilotPayload(BaseModel):
    graph: str
    messages: list   # [{role: "user"|"assistant", content: str}, ...]


class ParamPatch(BaseModel):
    node_id: str
    param: str       # built-in param name, or "_cb.<name>" for a CodeBlock override
    value: object    # number / bool / str — coerced + clamped server-side


class FeedbackPayload(BaseModel):
    project: Optional[str] = None       # current project/graph name, if any
    message: str                        # free-text feedback (required)
    severity: str = "bug"               # "bug" | "idea" | "question"
    context: dict = {}                   # client-collected, non-sensitive context
    graph: Optional[dict] = None         # current graph snapshot (opt-in)
    logs: list = []                      # recent backend log entries (opt-in)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def project_dir(name: str) -> Path:
    return PROJECTS_DIR / name


def require_project(name: str) -> Path:
    d = project_dir(name)
    if not d.is_dir():
        raise HTTPException(404, f"Project '{name}' not found")
    return d


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}


# ---------------------------------------------------------------------------
# System controls (single-user/local app): health, logs, restart
# ---------------------------------------------------------------------------
@app.get("/api/system/health")
async def system_health():
    """Always-on backend health used by the top-bar status dot."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "uptime_s": round(time.time() - _BOOT_TIME, 1),
        "pid": os.getpid(),
    }


@app.get("/api/system/logs")
async def system_logs(since: int = 0, limit: int = 500):
    """Incremental backend log stream for the in-app console.

    Pass the previously returned `last` as `since` to fetch only new lines.
    """
    with _LOG_LOCK:
        items = [e for e in _LOG_BUFFER if e["seq"] > since]
    if limit and len(items) > limit:
        items = items[-limit:]
    last = items[-1]["seq"] if items else since
    return {"entries": items, "last": last}


@app.post("/api/system/restart")
async def system_restart():
    """Restart the backend process.

    Acceptable because the app is local/single-user. uvicorn runs as PID 1 under
    `restart: unless-stopped`, so exiting hands control back to Docker, which
    brings the process straight back up. The UI then polls /api/system/health
    until it answers again.
    """
    logger.warning("restart requested via /api/system/restart — exiting for supervisor restart")

    def _die() -> None:
        time.sleep(0.4)  # let the HTTP response flush first
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_die, daemon=True).start()
    return {"status": "restarting"}


# ---------------------------------------------------------------------------
# Feedback / report drops
# ---------------------------------------------------------------------------
# A local, non-sensitive feedback channel: the UI saves a report here so an
# external coding agent (Claude Code via MCP, see AGENTS.md) can pick it up,
# reproduce the issue, fix it safely, and open a PR. The end-to-end workflow —
# git safety, repo rules, and the *mandatory AI-use disclosure* in the PR — lives
# in .claude/skills/feedback-fix/SKILL.md → docs/FEEDBACK_FIX_GUIDE.md.

def _slugify(text: str, default: str = "report") -> str:
    keep = "".join(c if c.isalnum() else "-" for c in (text or "").lower())
    slug = "-".join(p for p in keep.split("-") if p)[:40]
    return slug or default


def _git_commit() -> Optional[str]:
    """Best-effort short commit hash; None if .git isn't available in the image."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd="/app", capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:
        pass
    return None


def _render_report_md(fid: str, fb: "FeedbackPayload", stamp: dict) -> str:
    ctx = fb.context or {}
    errs = ctx.get("node_errors") or {}
    err_lines = "\n".join(f"  - `{k}`: {v}" for k, v in errs.items()) or "  - (nessuno)"
    lines = [
        f"# Feedback report — {stamp['severity']}",
        "",
        f"- **id**: `{fid}`",
        f"- **created (UTC)**: {stamp['created_utc']}",
        f"- **project**: {fb.project or '(nessuno)'}",
        f"- **app version**: {stamp['version']}"
        + (f" · commit `{stamp['commit']}`" if stamp.get("commit") else ""),
        f"- **client**: {ctx.get('user_agent', '?')}",
        f"- **url**: {ctx.get('url', '?')}",
        "",
        "## Messaggio",
        "",
        fb.message.strip() or "(vuoto)",
        "",
        "## Contesto tecnico",
        "",
        f"- ultimi errori per-nodo:",
        err_lines,
        f"- snapshot grafo allegato: {'sì (`graph.snapshot.json`)' if fb.graph else 'no'}",
        f"- log backend allegati: {'sì (`backend.log`)' if fb.logs else 'no'}",
        "",
        "## Come riprodurre",
        "",
        "1. Apri il progetto indicato (o carica `graph.snapshot.json`).",
        "2. Esegui il grafo e osserva il comportamento descritto sopra.",
        "",
        "---",
        "",
        "## Per l'agente di coding",
        "",
        "Prima di modificare codice, leggi **`.claude/skills/feedback-fix/SKILL.md`**",
        "e la guida **`docs/FEEDBACK_FIX_GUIDE.md`**: regole git per tornare a uno",
        "stato sicuro, regole di reload della repo, e la **disclosure obbligatoria**",
        "(agenti/modelli usati) da includere nella PR.",
    ]
    return "\n".join(lines) + "\n"


@app.post("/api/feedback")
async def save_feedback(fb: FeedbackPayload):
    """Persist a user feedback/report drop under feedback/<ts>-<slug>/."""
    if not (fb.message or "").strip():
        raise HTTPException(400, "message is required")

    now = time.gmtime()
    ts = time.strftime("%Y%m%d-%H%M%S", now)
    fid = f"{ts}-{_slugify(fb.project or fb.message)}"
    stamp = {
        "id": fid,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", now),
        "severity": fb.severity,
        "version": APP_VERSION,
        "commit": _git_commit(),
    }

    d = FEEDBACK_DIR / fid
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.json").write_text(json.dumps({
        **stamp,
        "project": fb.project,
        "message": fb.message,
        "context": fb.context,
        "has_graph": bool(fb.graph),
        "has_logs": bool(fb.logs),
    }, indent=2))
    (d / "report.md").write_text(_render_report_md(fid, fb, stamp))
    if fb.graph:
        (d / "graph.snapshot.json").write_text(json.dumps(fb.graph, indent=2))
    if fb.logs:
        lines = []
        for e in fb.logs:
            if isinstance(e, dict):
                lines.append(f"{e.get('ts','')} {e.get('level','')} {e.get('logger','')} {e.get('msg','')}")
            else:
                lines.append(str(e))
        (d / "backend.log").write_text("\n".join(lines) + "\n")

    logger.info("feedback saved: %s", fid)
    return {"status": "saved", "id": fid, "dir": f"feedback/{fid}", "path": str(d)}


@app.get("/api/feedback")
async def list_feedback():
    """List saved feedback reports (newest first) for tooling / triage."""
    items = []
    for d in sorted(FEEDBACK_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        rep = d / "report.json"
        if not rep.exists():
            continue
        try:
            data = json.loads(rep.read_text())
        except Exception:
            continue
        msg = (data.get("message") or "").strip().splitlines()
        items.append({
            "id": data.get("id", d.name),
            "created_utc": data.get("created_utc"),
            "project": data.get("project"),
            "severity": data.get("severity"),
            "summary": msg[0] if msg else "",
        })
    return {"reports": items}


# ---------------------------------------------------------------------------
# WebUI
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def home():
    page = Path("/app/webui/home.html")
    if page.exists():
        return page.read_text()
    # Fall back to the editor so a fresh checkout without home.html still lands somewhere usable.
    return HTMLResponse('<h1>noodle</h1><p><a href="/nodes">Open the node editor</a></p>')


@app.get("/ui", response_class=HTMLResponse)
async def webui():
    index = Path("/app/webui/index.html")
    if index.exists():
        return index.read_text()
    return HTMLResponse("<h1>CAD Studio</h1><p>WebUI not found</p>", status_code=404)


@app.get("/nodes", response_class=HTMLResponse)
async def webui_nodes():
    page = Path("/app/webui/nodes.html")
    if page.exists():
        return page.read_text()
    return HTMLResponse("<h1>CAD Studio</h1><p>Node editor not found</p>", status_code=404)


# ---------------------------------------------------------------------------
# Projects CRUD
# ---------------------------------------------------------------------------
@app.get("/api/projects")
async def list_projects():
    projects = []
    for d in sorted(PROJECTS_DIR.iterdir()):
        if d.is_dir():
            meta = {}
            meta_path = d / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
            projects.append({
                "name": d.name,
                "backend": meta.get("backend", "nodegraph"),
                "description": meta.get("description", ""),
            })
    return projects


@app.delete("/api/projects/{name}")
async def delete_project(name: str):
    d = require_project(name)
    shutil.rmtree(d)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Render & Export
# ---------------------------------------------------------------------------
@app.post("/api/projects/{name}/render")
async def render_project(name: str):
    """Transpile + execute a node graph to build123d, producing output.stl."""
    d = require_project(name)
    result = execute_graph(_load_graph(name), d)
    if not result["success"]:
        raise HTTPException(400, f"Graph execution failed:\n{result.get('errors')}")
    return {
        "status": "rendered",
        "stl": f"/api/projects/{name}/download",
        "warnings": None,
        "view": result["view"],
    }


@app.get("/api/projects/{name}/download")
async def download_stl(name: str):
    d = require_project(name)
    stl = d / "output.stl"
    if not stl.exists():
        raise HTTPException(404, "No STL rendered yet. Call /render first.")
    return FileResponse(stl, media_type="model/stl", filename=f"{name}.stl")


# ---------------------------------------------------------------------------
# Node-based CAD (graph engine)
# ---------------------------------------------------------------------------
def _load_graph(name: str) -> Graph:
    d = require_project(name)
    gpath = d / "graph.json"
    if not gpath.exists():
        raise HTTPException(404, f"Project '{name}' has no graph.json")
    return Graph.from_dict(json.loads(gpath.read_text()))


@app.get("/api/nodes")
async def node_catalog(category: str = ""):
    """Full node catalog (optionally filtered by category)."""
    nodes = catalog.as_json()
    if category:
        nodes = [n for n in nodes if n.get("category") == category]
    return nodes


@app.get("/api/wiretypes")
async def wire_types():
    """Wire-type compatibility, derived from the cast registry (cad_nodes/casts.py)
    — the single source of truth. The node editor fetches `input_accepts` instead
    of hard-coding it, so the two tables can't drift. See PLAN_DATA_PROTOCOL.md."""
    return {"types": catalog.WIRE_TYPES,
            "input_accepts": catalog.build_input_accepts()}


@app.post("/api/graph/{name}")
async def save_graph(name: str, graph: dict):
    """Create/overwrite a node graph project."""
    graph.setdefault("name", name)
    try:
        Graph.from_dict(graph).validate()
    except ValidationError as e:
        raise HTTPException(400, f"Invalid graph: {e}")

    d = project_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph.json").write_text(json.dumps(graph, indent=2))
    (d / "meta.json").write_text(json.dumps({
        "backend": "nodegraph",
        "description": graph.get("description", ""),
    }, indent=2))
    return {"status": "saved", "name": name,
            "nodes": len(graph.get("nodes", [])),
            "connections": len(graph.get("connections", []))}


@app.get("/api/graph/{name}")
async def get_graph(name: str):
    return _load_graph(name).to_dict()


@app.get("/api/graph/{name}/code")
async def get_graph_code(name: str, map: int = 0):
    """Generated build123d source. With `?map=1`, also returns `params`: a
    source map of editable parameter spans (row/col + type/min/max/options) so
    the code view can highlight and inline-edit each value non-destructively."""
    try:
        if map:
            code, params = transpile_with_map(_load_graph(name))
            return {"code": code, "params": params}
        return {"code": transpile(_load_graph(name))}
    except ValidationError as e:
        raise HTTPException(400, str(e))


@app.patch("/api/graph/{name}/param")
async def patch_graph_param(name: str, payload: ParamPatch):
    """Edit one parameter value from the code view (built-in param, or a CodeBlock
    `#@param` override when `param` is prefixed `_cb.`). Validates + clamps, then
    re-transpiles — non-destructive, round-trips with the node editor."""
    require_project(name)
    store = GraphStore(PROJECTS_DIR)
    try:
        value = api.patch_param(store, name, payload.node_id, payload.param, payload.value)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", "value": value}


@app.post("/api/graph/{name}/codeblock/{node_id}/scan")
async def scan_codeblock_params(name: str, node_id: str):
    """The `#@param` schema a CodeBlock declares (with effective values), for the
    node editor to render dynamic widgets/sockets."""
    require_project(name)
    store = GraphStore(PROJECTS_DIR)
    try:
        return {"params": api.scan_codeblock(store, name, node_id)}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


# Map an imported file's extension to the Import node that reads it.
_IMPORT_NODE_BY_EXT = {
    ".step": "ImportSTEP", ".stp": "ImportSTEP",
    ".stl": "ImportSTL",
    ".svg": "ImportSVG",
    ".dxf": "ImportDXF",
}


async def _store_asset(d: Path, file: UploadFile) -> dict:
    """Save an uploaded model file into the project's assets/ library and return
    its metadata. The stored path is *project-relative* (e.g. "assets/part.step")
    so it resolves at run time (the worker's cwd is the project dir) and reads
    cleanly in the asset picker. Raises HTTPException(400) on an unsupported type."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _IMPORT_NODE_BY_EXT:
        raise HTTPException(
            400, f"Unsupported file type '{ext or '?'}'. Supported: STEP, STL, SVG, DXF.")
    assets = d / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    stem = _slugify(Path(file.filename or "model").stem, "model")
    dest = assets / f"{stem}{ext}"
    n = 1
    while dest.exists():            # never clobber an existing asset
        dest = assets / f"{stem}-{n}{ext}"
        n += 1
    dest.write_bytes(await file.read())
    rel = f"assets/{dest.name}"
    return {"name": dest.name, "path": rel, "ext": ext,
            "node_type": _IMPORT_NODE_BY_EXT[ext]}


@app.get("/api/graph/{name}/assets")
async def list_assets(name: str):
    """List the model files already imported into this project's library."""
    d = require_project(name)
    assets = d / "assets"
    out = []
    if assets.is_dir():
        for f in sorted(assets.iterdir()):
            ext = f.suffix.lower()
            if f.is_file() and ext in _IMPORT_NODE_BY_EXT:
                out.append({"name": f.name, "path": f"assets/{f.name}", "ext": ext})
    return {"assets": out}


@app.post("/api/graph/{name}/asset")
async def upload_asset(name: str, file: UploadFile = File(...)):
    """Upload a file into the project library WITHOUT adding a node (the asset
    picker on an Import node uses this, then points itself at the new file)."""
    d = require_project(name)
    meta = await _store_asset(d, file)
    logger.info("asset stored for '%s': %s", name, meta["path"])
    return {"status": "stored", **meta}


@app.post("/api/graph/{name}/import")
async def import_model(name: str, file: UploadFile = File(...)):
    """Upload a STEP/STL/SVG/DXF file into the project AND add an Import node
    wired to read it. The UI then reloads the graph to show the new node."""
    d = require_project(name)
    meta = await _store_asset(d, file)
    store = GraphStore(PROJECTS_DIR)
    node_id = api.add_node(store, name, meta["node_type"],
                           params={"path": meta["path"]}, position=(80.0, 80.0))
    logger.info("imported %s as %s node %s", meta["name"], meta["node_type"], node_id)
    return {"status": "imported", "node_id": node_id, **meta, "file": meta["name"]}


@app.post("/api/graph/{name}/execute")
async def execute_graph_project(name: str):
    d = require_project(name)
    graph = _load_graph(name)
    logger.info("execute graph '%s' (%d nodes)", name, len(graph.nodes))
    try:
        result = execute_graph(graph, d)
    except ValidationError as e:
        logger.error("execute '%s' invalid graph: %s", name, e)
        raise HTTPException(400, str(e))
    if not result["success"]:
        logger.error("execute '%s' failed: %s", name, result.get("errors") or result.get("error_detail"))
        raise HTTPException(400, {
            "message": "Graph execution failed",
            "errors": result.get("errors"),
            "error_detail": result.get("error_detail"),
            "code": result.get("code"),
        })
    node_errors = result.get("node_errors", {})
    if node_errors:
        for nid, err in node_errors.items():
            logger.error("execute '%s' node %s: %s", name, nid, err)
    return {
        "status": "executed",
        "view": result["view"],
        "code": result["code"],
        "warnings": result.get("warnings", []),
        "node_errors": result.get("node_errors", {}),
        "stl": f"/api/projects/{name}/download" if result.get("stl") else None,
    }


@app.post("/api/graph/{name}/subshapes/{node_id}")
async def graph_subshapes(name: str, node_id: str, kind: str = "edge"):
    """Pickable sub-shapes (edges/faces/vertices) of a node's output shape,
    for the interactive selection picker."""
    if kind not in ("edge", "face", "vertex"):
        raise HTTPException(400, f"Unknown kind {kind!r}")
    d = require_project(name)
    graph = _load_graph(name)
    try:
        data = extract_subshapes_for_node(graph, node_id, kind, d)
    except ValidationError as e:
        raise HTTPException(400, str(e))
    if not data.get("success"):
        raise HTTPException(400, {"message": "Sub-shape extraction failed",
                                  "error": data.get("error")})
    return data


@app.get("/api/copilot/status")
async def copilot_status_route():
    """Which LLM backend the copilot will use (provider/model/keyed)."""
    return copilot_status()


@app.post("/api/copilot/chat")
async def copilot_chat(payload: CopilotPayload):
    """Drive the natural-language copilot: it edits payload.graph via the api
    tools and returns a reply plus whether the graph changed (UI should reload)."""
    require_project(payload.graph)
    result = run_chat(payload.graph, payload.messages, GraphStore(PROJECTS_DIR))
    return result


@app.get("/api/graph/{name}/view")
async def get_graph_view(name: str):
    d = require_project(name)
    vpath = d / "view.json"
    if not vpath.exists():
        raise HTTPException(404, "No view yet. Call /execute first.")
    return json.loads(vpath.read_text())


# Real geometry export: transpile + execute + write the file in the requested
# format, then stream it back. (The /download route only serves the STL.)
_EXPORT_MEDIA = {
    "step": ("model/step", "step"),
    "stl": ("model/stl", "stl"),
    "gltf": ("model/gltf+json", "gltf"),
}


@app.get("/api/graph/{name}/export/{fmt}")
async def export_graph_project(name: str, fmt: str):
    fmt = fmt.lower()
    if fmt not in _EXPORT_MEDIA:
        raise HTTPException(400, f"Unsupported format {fmt!r}; "
                                 f"choose from {sorted(_EXPORT_MEDIA)}")
    d = require_project(name)
    graph = _load_graph(name)
    media, ext = _EXPORT_MEDIA[fmt]
    try:
        out_path = export_graph(graph, d, fmt)
    except ValidationError as e:
        raise HTTPException(400, str(e))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, f"Export failed: {e}")
    return FileResponse(out_path, media_type=media, filename=f"{name}.{ext}")


# ---------------------------------------------------------------------------
# Backends list
# ---------------------------------------------------------------------------
@app.get("/api/backends")
async def list_backends():
    return [
        {"id": "nodegraph", "name": "Node CAD (build123d)", "type": "Visual graph -> build123d"},
    ]
