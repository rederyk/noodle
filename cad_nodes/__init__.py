"""
cad_nodes — Node-based CAD engine for CAD Studio v2.

Pipeline: graph JSON -> validate -> toposort -> transpile to build123d code
-> execute in subprocess -> extract mesh/view JSON.

The pure parts (catalog, graph, toposort, transpiler) depend only on the
standard library and can run anywhere. Only the executor / mesh_extractor
require `build123d` (available inside the Docker image).
"""

from .catalog import REGISTRY, NodeDef, get, as_json  # noqa: F401
from .graph import Graph, Node, Connection  # noqa: F401

__all__ = ["REGISTRY", "NodeDef", "get", "as_json", "Graph", "Node", "Connection"]
