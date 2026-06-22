"""
CLI for manual testing of the node engine.

    python -m cad_nodes.cli transpile <graph.json>
        Print the generated build123d code.

    python -m cad_nodes.cli execute  <graph.json> [--workdir DIR]
        Transpile + execute (requires build123d) and print the view JSON.

    python -m cad_nodes.cli catalog
        Print the node catalog as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from . import catalog
from .graph import Graph
from .transpiler import transpile


def _load_graph(path: str) -> Graph:
    data = json.loads(Path(path).read_text())
    return Graph.from_dict(data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cad_nodes")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_t = sub.add_parser("transpile", help="graph.json -> build123d code")
    p_t.add_argument("graph")

    p_e = sub.add_parser("execute", help="graph.json -> run + view JSON")
    p_e.add_argument("graph")
    p_e.add_argument("--workdir", default=None)

    sub.add_parser("catalog", help="dump node catalog JSON")

    args = parser.parse_args(argv)

    if args.cmd == "catalog":
        print(json.dumps(catalog.as_json(), indent=2))
        return 0

    if args.cmd == "transpile":
        graph = _load_graph(args.graph)
        print(transpile(graph))
        return 0

    if args.cmd == "execute":
        from .executor import execute_graph  # local import: needs build123d
        graph = _load_graph(args.graph)
        workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="cadrun_"))
        result = execute_graph(graph, workdir)
        printable = {k: v for k, v in result.items() if k != "view"}
        print(json.dumps(printable, indent=2))
        print("--- view ---")
        print(json.dumps(result.get("view"), indent=2))
        return 0 if result["success"] else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
