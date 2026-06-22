"""
CAD Studio — MCP server (Fase 2).

Exposes the node-graph engine to AI agents via the Model Context Protocol.
Thin wrappers over `cad_nodes.api`; state is shared with the REST server through
the same projects directory ($CAD_PROJECTS_DIR, default /app/projects).

Run standalone over stdio (for MCP Inspector / clients):
    python mcp_server.py

Or mount the SSE app into another ASGI server via `mcp.sse_app()`.
"""

import json

from mcp.server.fastmcp import FastMCP

from cad_nodes import api
from cad_nodes.store import GraphStore

mcp = FastMCP("CAD Studio")
STORE = GraphStore()


def _safe(fn, *args, **kwargs):
    """Run an api call, converting exceptions to a structured error dict."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - surface message to the agent
        return {"error": f"{type(e).__name__}: {e}"}


# ===========================================================================
# Tools — graph lifecycle
# ===========================================================================
@mcp.tool()
def cad_create_graph(name: str, description: str = "") -> str:
    """Create a new empty graph. Returns the graph_id."""
    return _safe(api.create_graph, STORE, name, description)


@mcp.tool()
def cad_list_graphs() -> list:
    """List existing graph ids."""
    return api.list_graphs(STORE)


@mcp.tool()
def cad_delete_graph(graph_id: str) -> bool:
    """Delete a graph and all its files."""
    return _safe(api.delete_graph, STORE, graph_id)


# ===========================================================================
# Tools — node / connection editing
# ===========================================================================
@mcp.tool()
def cad_add_node(graph_id: str, node_type: str, params: dict = None,
                 position: list = None, parent: str = "") -> str:
    """Add a node. params = {param: value}; position = [x, y]. Returns node_id."""
    return _safe(api.add_node, STORE, graph_id, node_type,
                 params or {}, tuple(position or (0, 0)), parent or None)


@mcp.tool()
def cad_connect(graph_id: str, from_node_id: str, from_socket: str,
                to_node_id: str, to_socket: str) -> str:
    """Connect an output socket to an input socket. Returns connection_id."""
    return _safe(api.connect, STORE, graph_id, from_node_id, from_socket,
                 to_node_id, to_socket)


@mcp.tool()
def cad_set_param(graph_id: str, node_id: str, params: dict) -> bool:
    """Update parameters of an existing node."""
    return _safe(api.set_param, STORE, graph_id, node_id, params)


@mcp.tool()
def cad_set_code(graph_id: str, node_id: str, code: str) -> bool:
    """Set the Python code of a CodeBlock node."""
    return _safe(api.set_code, STORE, graph_id, node_id, code)


@mcp.tool()
def cad_delete_node(graph_id: str, node_id: str) -> bool:
    """Remove a node and its connections."""
    return _safe(api.delete_node, STORE, graph_id, node_id)


@mcp.tool()
def cad_delete_connection(graph_id: str, connection_id: str) -> bool:
    """Remove a specific connection."""
    return _safe(api.delete_connection, STORE, graph_id, connection_id)


# ===========================================================================
# Tools — execution / inspection / export
# ===========================================================================
@mcp.tool()
def cad_execute(graph_id: str) -> dict:
    """Execute the graph. Returns success, errors, code and a view summary
    (bbox/volume/area/counts; the heavy mesh is omitted — use cad_get_view)."""
    result = _safe(api.execute, STORE, graph_id)
    if isinstance(result, dict) and result.get("view"):
        view = dict(result["view"])
        view.pop("mesh", None)
        result = {**result, "view": view}
    return result


@mcp.tool()
def cad_get_view(graph_id: str, fmt: str = "json") -> dict:
    """Read the last execution's view. fmt='mesh' includes the tessellated
    mesh; fmt='json' (default) omits it."""
    view = api.get_view(STORE, graph_id)
    if view is None:
        return {"error": "No view yet; call cad_execute first."}
    if fmt != "mesh":
        view = {k: v for k, v in view.items() if k != "mesh"}
    return view


@mcp.tool()
def cad_get_code(graph_id: str) -> dict:
    """Return the build123d Python code the graph transpiles to."""
    return _safe(lambda: {"code": api.get_code(STORE, graph_id)})


@mcp.tool()
def cad_get_panels(graph_id: str) -> dict:
    """Read the values of all Panel nodes from the last execution."""
    return _safe(api.get_panels, STORE, graph_id)


@mcp.tool()
def cad_export(graph_id: str, fmt: str = "step") -> str:
    """Export the model. fmt: step | stl | gltf. Returns the file path."""
    return _safe(api.export, STORE, graph_id, fmt)


@mcp.tool()
def cad_get_node_catalog(filter_category: str = "") -> list:
    """List available node types, optionally filtered by category."""
    return api.list_catalog(filter_category)


# ===========================================================================
# Resources
# ===========================================================================
@mcp.resource("cad://nodes")
def res_nodes() -> str:
    return json.dumps(api.list_catalog(), indent=2)


@mcp.resource("cad://nodes/{node_type}")
def res_node(node_type: str) -> str:
    return json.dumps(api.get_node_def(node_type), indent=2)


@mcp.resource("cad://graph/{graph_id}")
def res_graph(graph_id: str) -> str:
    return json.dumps(api.get_graph(STORE, graph_id), indent=2)


@mcp.resource("cad://graph/{graph_id}/code")
def res_graph_code(graph_id: str) -> str:
    return api.get_code(STORE, graph_id)


@mcp.resource("cad://graph/{graph_id}/view")
def res_graph_view(graph_id: str) -> str:
    return json.dumps(api.get_view(STORE, graph_id), indent=2)


# ===========================================================================
# Prompts
# ===========================================================================
@mcp.prompt()
def cad_design(descrizione: str) -> str:
    return f"""Progetta un pezzo meccanico per: "{descrizione}"

1. Crea un grafo con cad_create_graph
2. Aggiungi primitive (Box, Cylinder, Circle...) con cad_add_node
3. Collega con Booleane (Union/Subtract/Intersect) via cad_connect
4. Applica modificatori (Fillet, Chamfer)
5. Esegui con cad_execute e leggi il view (volume, bbox)
6. Itera con cad_set_param finché le misure sono corrette
7. Esporta con cad_export (step)
Mostra il view dopo ogni esecuzione per verificare le dimensioni."""


@mcp.prompt()
def cad_modify(graph_id: str, istruzioni: str) -> str:
    return f"""Modifica il grafo {graph_id} seguendo: "{istruzioni}"

1. Leggi cad_get_view({graph_id}) e cad_get_code({graph_id})
2. Regola parametri (cad_set_param) o aggiungi nodi (cad_add_node)
3. Esegui (cad_execute) e verifica
4. Esporta solo se soddisfacente."""


@mcp.prompt()
def cad_analyze(graph_id: str) -> str:
    return f"""Analizza il grafo {graph_id}:
1. cad_execute se non ancora eseguito
2. cad_get_view: volume, area, bbox, centro, conteggi (facce/edge/solidi)
3. cad_get_panels per i nodi Panel
4. Riassunto strutturale del modello."""


if __name__ == "__main__":
    mcp.run()
