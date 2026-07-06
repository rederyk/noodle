"""
GraphStore — filesystem persistence for node graphs.

Layout (one directory per graph, shared with the REST server's projects dir):
    <root>/<graph_id>/graph.json   # the graph
    <root>/<graph_id>/meta.json    # {"backend": "nodegraph", ...}
    <root>/<graph_id>/output.stl   # last execution
    <root>/<graph_id>/view.json    # last execution view

The default root is taken from $CAD_PROJECTS_DIR, falling back to /app/projects
(the Docker volume) so REST and MCP operate on the same graphs.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
from pathlib import Path

from .graph import Graph

DEFAULT_ROOT = os.environ.get("CAD_PROJECTS_DIR", "/app/projects")

# Curated example graphs bundled in the repo (tracked in git; projects/ is not,
# so they reach a fresh install through seed_examples() below).
EXAMPLES_DIR = Path(__file__).parent / "examples"

_EXAMPLE_DESCRIPTIONS = {
    "rounded-box": "Hello-world: a box with every edge filleted (primitive → modifier).",
    "flange": "Parametric plate with a bore, exported to STEP (booleans + export).",
    "bolt-flange": "Bolt-circle flange: one hole polar-arrayed and subtracted "
                   "wholesale — Grasshopper-style list fan-out.",
    "csg-boolean": "CSG basics: a box minus a sphere (Subtract boolean).",
    "parametric-gear": "Custom node from scratch — a spur gear written in one "
                       "CodeBlock, driven by #@param knobs.",
    "gear-row-fanout": "Grasshopper-style fan-out — a Range feeds the gear "
                       "CodeBlock so it produces a whole row of gears.",
    "scatter-surface": "DivideSurface + fan-out — a stud scattered on every point "
                       "of a U×V grid over a sphere.",
    "voronoi-panel": "Voronoi2D — scattered points become cells, extruded and "
                     "subtracted from a plate into a perforated panel.",
    "voronoi-vase": "Advanced combo — Voronoi cells mapped onto a revolved "
                    "surface, shelled into a thin-walled vase.",
    "parametric-curves": "Parametric curves — a Spline through points, an "
                         "ArcCenter and a Line as building blocks for wire geometry.",
}

# A graph id is a single directory name under the store root. Rejecting
# anything else (path separators, "..", hidden names) closes path traversal
# for every surface that resolves ids to paths (REST, MCP, copilot).
_GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")


def validate_graph_id(graph_id: str) -> str:
    """Return graph_id if it is a safe directory name, else raise ValueError."""
    if not isinstance(graph_id, str) or not _GRAPH_ID_RE.fullmatch(graph_id):
        raise ValueError(
            f"Invalid project name {graph_id!r}: use 1-64 chars of letters, "
            "digits, '.', '_', '-' or spaces, starting with a letter or digit"
        )
    return graph_id


def stamp_agent_tags(nodes) -> None:
    """Fill the empty `date` param of ToAgent tag nodes at save time — the
    provenance date the agent searches by ('il pezzo messo lì ieri'). Accepts
    Node objects or plain node dicts, so both the GraphStore and the REST
    save route can stamp."""
    today = datetime.date.today().isoformat()
    for n in nodes:
        is_dict = isinstance(n, dict)
        ntype = n["type"] if is_dict else n.type
        if ntype != "ToAgent":
            continue
        params = n.setdefault("params", {}) if is_dict else n.params
        if not params.get("date"):
            params["date"] = today


class GraphStore:
    def __init__(self, root: str | Path = DEFAULT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, graph_id: str) -> Path:
        return self.root / validate_graph_id(graph_id)

    def exists(self, graph_id: str) -> bool:
        return (self.dir(graph_id) / "graph.json").exists()

    def list(self) -> list[str]:
        return sorted(
            d.name for d in self.root.iterdir()
            if d.is_dir() and (d / "graph.json").exists()
        )

    def load(self, graph_id: str) -> Graph:
        gpath = self.dir(graph_id) / "graph.json"
        if not gpath.exists():
            raise KeyError(f"No graph {graph_id!r}")
        return Graph.from_dict(json.loads(gpath.read_text()))

    def save(self, graph_id: str, graph: Graph, description: str = "") -> None:
        stamp_agent_tags(graph.nodes)
        d = self.dir(graph_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "graph.json").write_text(json.dumps(graph.to_dict(), indent=2))
        meta = {}
        mpath = d / "meta.json"
        if mpath.exists():
            try:
                meta = json.loads(mpath.read_text())
            except Exception:
                meta = {}
        meta["backend"] = "nodegraph"
        if description:
            meta["description"] = description
        mpath.write_text(json.dumps(meta, indent=2))

    def delete(self, graph_id: str) -> None:
        d = self.dir(graph_id)
        if d.is_dir():
            shutil.rmtree(d)

    def seed_examples(self) -> list[str]:
        """On a fresh store (no projects yet, never seeded), copy the bundled
        example graphs in so a new user lands on real graphs instead of a blank
        canvas. Idempotent and non-destructive: a `.seeded` marker means we
        never re-add examples the user has since deleted, and an existing
        project short-circuits it entirely. Returns the names seeded."""
        marker = self.root / ".seeded"
        if marker.exists() or self.list():
            return []
        seeded: list[str] = []
        for jp in sorted(EXAMPLES_DIR.glob("*.json")):
            name = jp.stem
            try:
                validate_graph_id(name)
                graph = Graph.from_dict(json.loads(jp.read_text()))
                self.save(name, graph, _EXAMPLE_DESCRIPTIONS.get(name, ""))
                seeded.append(name)
            except Exception:
                continue  # a broken example must never break startup
        try:
            marker.write_text("")
        except OSError:
            pass
        return seeded

    def view(self, graph_id: str) -> dict | None:
        vpath = self.dir(graph_id) / "view.json"
        if not vpath.exists():
            return None
        try:
            return json.loads(vpath.read_text())
        except Exception:
            return None
