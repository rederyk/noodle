"""
Executor — runs transpiled build123d code in a sandboxed subprocess and
collects the STL + view JSON via mesh_extractor.

Mirrors the subprocess pattern already used in backends/pythonocc.py: write a
wrapper script, run `python3` with a timeout, capture stdout/stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .graph import Graph
from .transpiler import transpile

# Repo root = parent of the cad_nodes package, so the subprocess can do
# `from cad_nodes.mesh_extractor import extract_and_write`.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)

_EPILOGUE = """

# --- view extraction (injected by executor) ---
import sys as _sys
_sys.path.insert(0, {repo_root!r})
from cad_nodes.mesh_extractor import extract_and_write
extract_and_write(__result__, {stl!r}, {view!r}, __panels__)
"""


def build_script(code: str, stl_path: Path, view_path: Path) -> str:
    return code + _EPILOGUE.format(
        repo_root=_REPO_ROOT, stl=str(stl_path), view=str(view_path)
    )


def execute_code(code: str, workdir: Path, timeout: int = 120) -> dict:
    """Execute already-transpiled code. Returns a result dict."""
    workdir.mkdir(parents=True, exist_ok=True)
    stl_path = workdir / "output.stl"
    view_path = workdir / "view.json"
    script_path = workdir / "_run.py"

    if view_path.exists():
        view_path.unlink()

    script_path.write_text(build_script(code, stl_path, view_path))

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=timeout, cwd=str(workdir),
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "code": code,
                "errors": f"Execution timed out after {timeout}s", "view": None}

    view = None
    if view_path.exists():
        try:
            view = json.loads(view_path.read_text())
        except Exception:
            view = None

    success = proc.returncode == 0 and view is not None and view.get("success")
    return {
        "success": bool(success),
        "code": code,
        "stdout": proc.stdout,
        "errors": proc.stderr if proc.returncode != 0 else None,
        "view": view,
        "stl": str(stl_path) if stl_path.exists() else None,
    }


def execute_graph(graph: Graph, workdir: Path, timeout: int = 120) -> dict:
    """Transpile + execute a graph end-to-end."""
    code = transpile(graph)
    return execute_code(code, workdir, timeout=timeout)
