"""
Application layer — high-level graph operations shared by the REST server and
the MCP server. Pure Python (no `mcp`, no FastAPI); build123d only enters via
the executor subprocess.

Every function takes a `GraphStore` so callers control where graphs live.
Errors are raised as ValueError/KeyError; transport layers translate them.
"""

from __future__ import annotations

from typing import Any, Optional

from . import catalog
from .executor import execute_graph, export_graph
from .graph import Connection, Graph, Node
from .store import GraphStore
from .transpiler import parse_codeblock_params, transpile, transpile_with_map


# --- catalog --------------------------------------------------------------
def list_catalog(category: str = "") -> list[dict]:
    nodes = catalog.as_json()
    if category:
        nodes = [n for n in nodes if n.get("category") == category]
    return nodes


def get_node_def(node_type: str) -> dict:
    from dataclasses import asdict
    return asdict(catalog.get(node_type))


# --- graph lifecycle ------------------------------------------------------
def create_graph(store: GraphStore, name: str, description: str = "") -> str:
    if store.exists(name):
        raise ValueError(f"Graph {name!r} already exists")
    store.save(name, Graph(name=name), description)
    return name


def get_graph(store: GraphStore, graph_id: str) -> dict:
    return store.load(graph_id).to_dict()


def list_graphs(store: GraphStore) -> list[str]:
    return store.list()


def delete_graph(store: GraphStore, graph_id: str) -> bool:
    store.delete(graph_id)
    return True


# --- node / connection editing -------------------------------------------
def _unique_node_id(graph: Graph, node_type: str) -> str:
    base = node_type.lower()
    existing = {n.id for n in graph.nodes}
    i = 1
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def add_node(store: GraphStore, graph_id: str, node_type: str,
             params: Optional[dict] = None,
             position: tuple[float, float] = (0.0, 0.0),
             parent: Optional[str] = None) -> str:
    if node_type not in catalog.REGISTRY:
        raise ValueError(f"Unknown node type {node_type!r}")
    graph = store.load(graph_id)
    node_id = _unique_node_id(graph, node_type)
    graph.nodes.append(Node(id=node_id, type=node_type,
                            params=dict(params or {}),
                            position=tuple(position), parent=parent))
    store.save(graph_id, graph)
    return node_id


def connect(store: GraphStore, graph_id: str, from_node: str, from_socket: str,
            to_node: str, to_socket: str) -> str:
    graph = store.load(graph_id)
    cid = f"c{len(graph.connections) + 1}"
    while any(c.id == cid for c in graph.connections):
        cid += "_"
    graph.connections.append(Connection(cid, from_node, from_socket, to_node, to_socket))
    graph.validate()  # raises ValidationError on bad wiring
    store.save(graph_id, graph)
    return cid


def set_param(store: GraphStore, graph_id: str, node_id: str, params: dict) -> bool:
    graph = store.load(graph_id)
    node = graph.node(node_id)
    node.params.update(params)
    store.save(graph_id, graph)
    return True


def _coerce_clamp(kind: str, value, *, lo=None, hi=None, options=None):
    """Coerce a UI value to its declared type and clamp numerics to [lo, hi].
    Raises ValueError on an out-of-set select. Shared by built-in params and
    CodeBlock `#@param` overrides."""
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if kind == "select":
        value = str(value)
        if options and value not in options:
            raise ValueError(f"{value!r} is not one of {options}")
        return value
    if kind in ("int", "float"):
        num = float(value)
        if lo is not None:
            num = max(num, float(lo))
        if hi is not None:
            num = min(num, float(hi))
        return int(round(num)) if kind == "int" else num
    return str(value)


def patch_param(store: GraphStore, graph_id: str, node_id: str,
                param: str, value) -> Any:
    """Structured single-param edit from the code view. Validates/clamps against
    the catalog Param (built-ins) or the `#@param` annotation (CodeBlock, when
    `param` is prefixed `_cb.`). Returns the stored value. Non-destructive: a
    CodeBlock override lives in a `_cb` namespace, never touching its source."""
    graph = store.load(graph_id)
    node = graph.node(node_id)

    if param.startswith("_cb."):
        if node.type != "CodeBlock":
            raise ValueError(f"Node {node_id} is {node.type}, not a CodeBlock")
        name = param[4:]
        decl = next((d for d in parse_codeblock_params(node.params.get("code", ""))
                     if d["name"] == name), None)
        if decl is None:
            raise ValueError(f"CodeBlock {node_id} declares no #@param {name!r}")
        value = _coerce_clamp(decl["type"], value, lo=decl["min"], hi=decl["max"],
                              options=decl["options"])
        overrides = dict(node.params.get("_cb") or {})
        overrides[name] = value
        node.params["_cb"] = overrides
    else:
        pdef = next((p for p in catalog.get(node.type).params if p.name == param), None)
        if pdef is None:
            raise ValueError(f"Node {node.type} has no param {param!r}")
        value = _coerce_clamp(pdef.type, value, lo=pdef.min, hi=pdef.max,
                              options=pdef.options or None)
        node.params[param] = value

    store.save(graph_id, graph)
    return value


def scan_codeblock(store: GraphStore, graph_id: str, node_id: str) -> list[dict]:
    """The `#@param` schema declared by a CodeBlock, merged with current
    overrides (so each entry reports its effective `value`)."""
    node = store.load(graph_id).node(node_id)
    if node.type != "CodeBlock":
        raise ValueError(f"Node {node_id} is {node.type}, not a CodeBlock")
    overrides = node.params.get("_cb") or {}
    schema = parse_codeblock_params(node.params.get("code", ""))
    for d in schema:
        d["value"] = overrides.get(d["name"], d["default"])
    return schema


def set_code(store: GraphStore, graph_id: str, node_id: str, code: str) -> bool:
    graph = store.load(graph_id)
    node = graph.node(node_id)
    if node.type != "CodeBlock":
        raise ValueError(f"Node {node_id} is {node.type}, not a CodeBlock")
    node.params["code"] = code
    store.save(graph_id, graph)
    return True


def delete_node(store: GraphStore, graph_id: str, node_id: str) -> bool:
    graph = store.load(graph_id)
    graph.nodes = [n for n in graph.nodes if n.id != node_id]
    graph.connections = [c for c in graph.connections
                         if c.from_node != node_id and c.to_node != node_id]
    store.save(graph_id, graph)
    return True


def delete_connection(store: GraphStore, graph_id: str, connection_id: str) -> bool:
    graph = store.load(graph_id)
    graph.connections = [c for c in graph.connections if c.id != connection_id]
    store.save(graph_id, graph)
    return True


# --- code / execution / inspection ---------------------------------------
def get_code(store: GraphStore, graph_id: str) -> str:
    return transpile(store.load(graph_id))


def get_code_map(store: GraphStore, graph_id: str) -> dict:
    """Generated source + a param<->code source map for the editable code view."""
    code, params = transpile_with_map(store.load(graph_id))
    return {"code": code, "params": params}


def execute(store: GraphStore, graph_id: str, timeout: int = 120) -> dict:
    graph = store.load(graph_id)
    return execute_graph(graph, store.dir(graph_id), timeout=timeout)


def get_view(store: GraphStore, graph_id: str) -> dict | None:
    return store.view(graph_id)


def get_panels(store: GraphStore, graph_id: str) -> dict:
    view = store.view(graph_id) or {}
    return view.get("panels", {})


def export(store: GraphStore, graph_id: str, fmt: str = "step") -> str:
    """Export the graph to a file; returns the path."""
    graph = store.load(graph_id)
    out = export_graph(graph, store.dir(graph_id), fmt)
    return str(out)
