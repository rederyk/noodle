"""
Executor — runs transpiled build123d code in a sandboxed subprocess and
collects the STL + view JSON via mesh_extractor.

Mirrors the subprocess pattern already used in backends/pythonocc.py: write a
wrapper script, run `python3` with a timeout, capture stdout/stderr.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from .graph import Graph
from .transpiler import transpile

# Marker the transpiler appends to each statement (see transpiler._annot).
_NODE_MARK = re.compile(r"# @node:(\S+) \(([^)]*)\)")
_TB_FRAME = re.compile(r'File "[^"]*_run\.py", line (\d+)')


def _humanize(exc_line: str) -> tuple[str, str]:
    """Map a raw exception line to (message, hint) in plain language."""
    low = exc_line.lower()
    if "failed creating a fillet" in low or "failed creating a chamfer" in low:
        return ("Raggio/lunghezza troppo grande per questa geometria.",
                "Riduci il valore: deve stare entro lo spigolo più piccolo del "
                "pezzo. (build123d offre max_fillet() per il massimo valido.)")
    if any(k in low for k in ("command not done", "stdfail_notdone", "brep_api")):
        return ("L'operazione geometrica non è riuscita: il kernel non ha potuto "
                "costruire la forma.",
                "Di solito un parametro è fuori scala. Riduci il raggio di "
                "Fillet/Chamfer, assicurati che lo spessore di Shell sia minore "
                "del pezzo, e che il profilo di Revolve non attraversi l'asse.")
    if "constructionerror" in low or "standard_constructionerror" in low:
        return ("Geometria di costruzione non valida.",
                "Controlla che le dimensioni siano positive e non nulle.")
    if "list index out of range" in low or "indexerror" in low:
        return ("Indice fuori dai limiti della lista.",
                "Un nodo ListItem/ListSlice punta oltre la lunghezza della lista.")
    if "zerodivision" in low:
        return ("Divisione per zero.",
                "Controlla i nodi Divide / Expression a monte.")
    if "is not closed" in low or "wire is not closed" in low:
        return ("Il contorno non è chiuso.",
                "MakeFace/Extrude richiedono uno sketch o un wire chiuso.")
    if "nameerror" in low:
        return ("Nome non definito nel codice generato.",
                "Probabile errore in un nodo CodeBlock o Expression.")
    # Fallback: surface the raw exception.
    return (exc_line or "Errore di esecuzione.", "")


def _diagnose(stderr: str, script_text: str) -> dict:
    """Correlate a traceback to the culprit node and translate the error."""
    src_lines = script_text.splitlines()
    frame_nums = [int(m.group(1)) for m in _TB_FRAME.finditer(stderr or "")]

    node_id = node_type = culprit = None
    # Walk frames innermost-first; the deepest line carrying a @node marker wins
    # (a CodeBlock's inner line has none, so we fall back to its call site).
    for ln in reversed(frame_nums):
        if 1 <= ln <= len(src_lines):
            mm = _NODE_MARK.search(src_lines[ln - 1])
            if mm:
                node_id, node_type = mm.group(1), mm.group(2)
                culprit = src_lines[ln - 1].split("# @node:")[0].strip()
                break

    exc_line = ""
    for line in reversed((stderr or "").strip().splitlines()):
        if line.strip():
            exc_line = line.strip()
            break
    message, hint = _humanize(exc_line)
    return {
        "node_id": node_id,
        "node_type": node_type,
        "culprit": culprit,
        "exception": exc_line,
        "message": message,
        "hint": hint,
    }


def _degenerate_warning(view: dict) -> str | None:
    """Flag a non-error result that is empty (e.g. a boolean that removed all
    geometry). Skip 2D/section results, which legitimately have zero volume."""
    if not view or not view.get("success"):
        return None
    kind = (view.get("kind") or "").lower()
    if "sketch" in kind or "face" in kind or "wire" in kind:
        return None
    counts = view.get("counts") or {}
    vol = view.get("volume")
    if counts.get("solids") == 0 and (vol in (None, 0) or (isinstance(vol, (int, float)) and abs(vol) < 1e-9)):
        return ("Il risultato è vuoto (volume nullo, nessun solido): "
                "un'operazione booleana potrebbe aver eliminato tutta la geometria.")
    return None

# Repo root = parent of the cad_nodes package, so the subprocess can do
# `from cad_nodes.mesh_extractor import extract_and_write`.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)

_EPILOGUE = """

# --- view extraction (injected by executor) ---
import sys as _sys
_sys.path.insert(0, {repo_root!r})
from cad_nodes.mesh_extractor import extract_and_write
extract_and_write(__result__, {stl!r}, {view!r}, __panels__, __previews__, {lin}, {ang})
"""

# Tessellation level-of-detail: (linear_frac of bbox diagonal, angular tol rad).
# Live preview is coarse (~10-30x fewer triangles, ~10x smaller payload); the
# STEP/STL bake path (export_graph) is unaffected and stays exact/fine.
_QUALITY = {
    "live": (0.02, 0.4),
    "fine": (0.004, 0.15),
}


def build_script(code: str, stl_path: Path, view_path: Path,
                 quality: str = "live") -> str:
    lin, ang = _QUALITY.get(quality, _QUALITY["live"])
    return code + _EPILOGUE.format(
        repo_root=_REPO_ROOT, stl=str(stl_path), view=str(view_path),
        lin=lin, ang=ang,
    )


# ---------------------------------------------------------------------------
# Warm worker — keeps build123d imported across runs (the cold import is ~2.7s,
# the actual build+tessellate is 5-165ms). A separate process keeps the web
# server build123d-free and isolated from OCCT crashes; it runs one job at a
# time (serialised by a lock) and is respawned if it dies or times out.
# ---------------------------------------------------------------------------
_WORKER_PATH = str(Path(__file__).resolve().parent / "worker.py")
_SENTINEL = "@@CADWORKER@@"
_USE_WARM = os.environ.get("CAD_WARM_WORKER", "1") != "0"


class WarmWorker:
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None

    def _read_sentinel(self, timeout: float):
        """Read worker stdout until a SENTINEL line; ignore other noise.
        Returns the parsed dict, or None on timeout."""
        box: dict = {}
        proc = self._proc

        def reader():
            for line in proc.stdout:
                if line.startswith(_SENTINEL):
                    try:
                        box["v"] = json.loads(line[len(_SENTINEL):])
                    except Exception:
                        box["v"] = None
                    return

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            return None
        return box.get("v")

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-u", _WORKER_PATH],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, cwd=_REPO_ROOT,
        )
        ready = self._read_sentinel(timeout=120)
        if not ready or not ready.get("ready"):
            self._kill()
            raise RuntimeError("warm worker failed to import build123d")

    def run(self, script_path: Path, cwd: Path, timeout: float) -> dict:
        with self._lock:
            if not self._alive():
                self._spawn()
            job = json.dumps({"cmd": "run", "script_path": str(script_path),
                              "cwd": str(cwd)})
            try:
                self._proc.stdin.write(job + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self._kill()
                raise
            res = self._read_sentinel(timeout)
            if res is None:                 # timed out or crashed mid-job
                self._kill()
                return {"timeout": True}
            return res


_WORKER = WarmWorker()


def _timeout_result(code: str, timeout: float) -> dict:
    return {"success": False, "code": code, "warnings": [],
            "errors": f"Execution timed out after {timeout}s", "view": None,
            "error_detail": {
                "node_id": None, "node_type": None, "culprit": None,
                "exception": "Timeout",
                "message": f"Esecuzione interrotta dopo {timeout}s.",
                "hint": "Il grafo è troppo pesante o un'operazione si è bloccata."}}


def _finalize(code: str, script_text: str, stdout: str, stderr,
              view_path: Path, stl_path: Path) -> dict:
    """Build the result dict from a finished run (warm or cold)."""
    view = None
    if view_path.exists():
        try:
            view = json.loads(view_path.read_text())
        except Exception:
            view = None

    success = stderr is None and view is not None and view.get("success")
    result = {
        "success": bool(success),
        "code": code,
        "stdout": stdout,
        "errors": stderr,
        "view": view,
        "warnings": [],
        "stl": str(stl_path) if stl_path.exists() else None,
    }

    if not success:
        detail = _diagnose(stderr or "", script_text)
        # Ran cleanly but produced no shape: not a Python traceback.
        if not detail["exception"]:
            err = (view or {}).get("error", "Il grafo non produce geometria.")
            detail["message"] = (
                "Nessun risultato da visualizzare: collega un nodo che produce "
                "geometria (o un Export) all'uscita finale.")
            detail["exception"] = err
        result["error_detail"] = detail
        if not result["errors"]:
            result["errors"] = detail["message"]
    else:
        warn = _degenerate_warning(view)
        if warn:
            result["warnings"].append(warn)

    return result


def execute_code(code: str, workdir: Path, timeout: int = 120,
                 quality: str = "live") -> dict:
    """Execute already-transpiled code. Uses the warm worker (build123d kept
    loaded) with a fallback to a cold subprocess. Returns a result dict."""
    workdir.mkdir(parents=True, exist_ok=True)
    stl_path = workdir / "output.stl"
    view_path = workdir / "view.json"
    script_path = workdir / "_run.py"

    if view_path.exists():
        view_path.unlink()

    script_text = build_script(code, stl_path, view_path, quality)
    script_path.write_text(script_text)

    # --- warm path -------------------------------------------------------
    if _USE_WARM:
        try:
            res = _WORKER.run(script_path, workdir, timeout)
            if res.get("timeout"):
                return _timeout_result(code, timeout)
            stderr = (res.get("error") or "").strip() or None
            return _finalize(code, script_text, res.get("stdout", ""),
                             stderr, view_path, stl_path)
        except Exception:
            pass  # worker unavailable → fall back to a cold subprocess

    # --- cold fallback ---------------------------------------------------
    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=timeout, cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        return _timeout_result(code, timeout)

    stderr = proc.stderr if proc.returncode != 0 else None
    return _finalize(code, script_text, proc.stdout, stderr, view_path, stl_path)


def execute_graph(graph: Graph, workdir: Path, timeout: int = 120,
                  quality: str = "live") -> dict:
    """Transpile + execute a graph end-to-end."""
    code = transpile(graph)
    return execute_code(code, workdir, timeout=timeout, quality=quality)


_EXPORTERS = {
    "step": ("export_step", "step"),
    "stl": ("export_stl", "stl"),
    "gltf": ("export_gltf", "gltf"),
}


def export_graph(graph: Graph, workdir: Path, fmt: str = "step",
                 timeout: int = 120) -> Path:
    """Transpile, execute and write `__result__` to a file in `fmt`."""
    fmt = fmt.lower()
    if fmt not in _EXPORTERS:
        raise ValueError(f"Unsupported export format {fmt!r}; "
                         f"choose from {sorted(_EXPORTERS)}")
    func, ext = _EXPORTERS[fmt]
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = workdir / f"output.{ext}"
    script_path = workdir / "_export.py"

    code = transpile(graph)
    script = code + (
        f"\n# --- export (injected) ---\n"
        f"from build123d import {func}\n"
        f"{func}(__result__, {str(out_path)!r})\n"
    )
    script_path.write_text(script)

    proc = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True, text=True, timeout=timeout, cwd=str(workdir),
    )
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Export failed:\n{proc.stderr}")
    return out_path
