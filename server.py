"""
CAD Studio — Unified API server for AI + webui CAD modeling.
Backends: OpenSCAD (CSG text) + CadQuery/PythonOCC (B-Rep solid).
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Node-based CAD engine (pure imports; build123d only used in the subprocess)
from cad_nodes import catalog
from cad_nodes.graph import Graph, ValidationError
from cad_nodes.transpiler import transpile
from cad_nodes.executor import execute_graph, export_graph, extract_subshapes_for_node

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECTS_DIR = Path("/app/projects")
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="CAD Studio", version="0.1.0")

# Serve webui static files
app.mount("/static", StaticFiles(directory="/app/webui"), name="static")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class CodePayload(BaseModel):
    code: str
    backend: str = "openscad"  # "openscad" | "cadquery"


class ParamsPayload(BaseModel):
    params: dict


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


def render_openscad(scad_path: Path, output_stl: Path) -> str:
    """Render .scad → .stl via OpenSCAD CLI."""
    result = subprocess.run(
        ["openscad", "-o", str(output_stl), str(scad_path)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise HTTPException(400, f"OpenSCAD error:\n{result.stderr}")
    return result.stderr  # warnings


def render_cadquery(script_path: Path, output_stl: Path) -> str:
    """Run a CadQuery script that must call export_stl(path)."""
    import importlib.util

    output_stl.parent.mkdir(parents=True, exist_ok=True)

    # Inject export helper
    wrapper = f"""
import cadquery as cq
__output_stl__ = "{output_stl}"

# --- user code ---
exec(open("{script_path}").read())
# --- end user code ---
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(wrapper)
        wrapper_path = f.name

    result = subprocess.run(
        ["python3", wrapper_path],
        capture_output=True, text=True, timeout=120,
    )
    Path(wrapper_path).unlink(missing_ok=True)

    if result.returncode != 0:
        raise HTTPException(400, f"CadQuery error:\n{result.stderr}")
    if not output_stl.exists():
        raise HTTPException(400, "CadQuery script did not produce output STL. "
                                 "Make sure to call cq.exporters.export(shape, __output_stl__)")
    return result.stderr


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
@app.get("/")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# WebUI
# ---------------------------------------------------------------------------
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
                "backend": meta.get("backend", "openscad"),
                "description": meta.get("description", ""),
            })
    return projects


@app.post("/api/projects/{name}")
async def create_project(name: str, payload: CodePayload):
    d = project_dir(name)
    if d.exists():
        raise HTTPException(409, f"Project '{name}' already exists")
    d.mkdir(parents=True)
    ext = "scad" if payload.backend == "openscad" else "py"
    (d / f"main.{ext}").write_text(payload.code)
    (d / "meta.json").write_text(json.dumps({
        "backend": payload.backend,
        "description": "",
    }, indent=2))
    return {"status": "created", "name": name}


@app.get("/api/projects/{name}")
async def read_project(name: str):
    d = require_project(name)
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    ext = "scad" if meta.get("backend") == "openscad" else "py"
    code_path = d / f"main.{ext}"
    code = code_path.read_text() if code_path.exists() else ""
    return {
        "name": name,
        "backend": meta.get("backend", "openscad"),
        "description": meta.get("description", ""),
        "code": code,
    }


@app.put("/api/projects/{name}")
async def update_project(name: str, payload: CodePayload):
    d = require_project(name)
    ext = "scad" if payload.backend == "openscad" else "py"
    (d / f"main.{ext}").write_text(payload.code)
    meta = {}
    if (d / "meta.json").exists():
        meta = json.loads((d / "meta.json").read_text())
    meta["backend"] = payload.backend
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return {"status": "updated"}


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
    d = require_project(name)
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    backend = meta.get("backend", "openscad")

    # Node graph: transpile + execute build123d, producing output.stl.
    if backend == "nodegraph":
        result = execute_graph(_load_graph(name), d)
        if not result["success"]:
            raise HTTPException(400, f"Graph execution failed:\n{result.get('errors')}")
        return {
            "status": "rendered",
            "stl": f"/api/projects/{name}/download",
            "warnings": None,
            "view": result["view"],
        }

    ext = "scad" if backend == "openscad" else "py"
    code_path = d / f"main.{ext}"
    if not code_path.exists():
        raise HTTPException(400, f"No main.{ext} found")

    output_stl = d / "output.stl"

    if backend == "openscad":
        warnings = render_openscad(code_path, output_stl)
    else:
        warnings = render_cadquery(code_path, output_stl)

    return {
        "status": "rendered",
        "stl": f"/api/projects/{name}/download",
        "warnings": warnings or None,
    }


@app.get("/api/projects/{name}/download")
async def download_stl(name: str):
    d = require_project(name)
    stl = d / "output.stl"
    if not stl.exists():
        raise HTTPException(404, "No STL rendered yet. Call /render first.")
    return FileResponse(stl, media_type="model/stl", filename=f"{name}.stl")


# ---------------------------------------------------------------------------
# Params (OpenSCAD only — extracts // Param: name = value)
# ---------------------------------------------------------------------------
import re

PARAM_RE = re.compile(r"^//\s*Param:\s*(\w+)\s*=\s*(.+)$", re.MULTILINE)


@app.get("/api/projects/{name}/params")
async def get_params(name: str):
    d = require_project(name)
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    if meta.get("backend") != "openscad":
        raise HTTPException(400, "Params only supported for OpenSCAD backend")
    code = (d / "main.scad").read_text() if (d / "main.scad").exists() else ""
    params = {}
    for m in PARAM_RE.finditer(code):
        key, val = m.group(1), m.group(2).strip()
        # Try to parse as number
        try:
            val = float(val)
            if val == int(val):
                val = int(val)
        except ValueError:
            pass
        params[key] = val
    return params


@app.patch("/api/projects/{name}/params")
async def set_params(name: str, payload: ParamsPayload):
    d = require_project(name)
    meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
    if meta.get("backend") != "openscad":
        raise HTTPException(400, "Params only supported for OpenSCAD backend")
    code_path = d / "main.scad"
    code = code_path.read_text() if code_path.exists() else ""
    for key, val in payload.params.items():
        # Replace // Param: key = old with new value
        code = re.sub(
            rf"(//\s*Param:\s*{key}\s*=\s*).+$",
            rf"\g<1>{val}",
            code,
            flags=re.MULTILINE,
        )
    code_path.write_text(code)
    return {"status": "updated", "params": payload.params}


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
async def get_graph_code(name: str):
    try:
        return {"code": transpile(_load_graph(name))}
    except ValidationError as e:
        raise HTTPException(400, str(e))


@app.post("/api/graph/{name}/execute")
async def execute_graph_project(name: str):
    d = require_project(name)
    graph = _load_graph(name)
    try:
        result = execute_graph(graph, d)
    except ValidationError as e:
        raise HTTPException(400, str(e))
    if not result["success"]:
        raise HTTPException(400, {
            "message": "Graph execution failed",
            "errors": result.get("errors"),
            "error_detail": result.get("error_detail"),
            "code": result.get("code"),
        })
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
        {"id": "openscad", "name": "OpenSCAD", "type": "CSG text (.scad)"},
        {"id": "nodegraph", "name": "Node CAD (build123d)", "type": "Visual graph -> build123d"},
    ]
