# Driving noodle from an AI agent

noodle exposes the **same** graph engine three ways, all backed by `cad_nodes.api`:

1. **MCP server** — for agents that speak the Model Context Protocol
   (Claude Code, openclaw, Claude Desktop, Cursor, MCP Inspector…).
2. **HTTP API** — plain `POST`/`GET`, works from any language or `curl`.
3. **In-app copilot** — the chat box in the web UI (`POST /api/copilot/chat`).

This file covers (1) and (2). Start the app first (`./start.sh`, `start.bat`, or
`docker compose up -d --build`) so the container `cad-studio` is running.

---

## 1. MCP (recommended for agents)

The MCP server (`mcp_server.py`) needs the full build123d environment **and** the
shared `projects/` directory. The cleanest, OS-independent way to get both is to
run it **inside the already-running container** over stdio — no extra install,
identical on Windows and Linux:

```json
{
  "mcpServers": {
    "noodle": {
      "command": "docker",
      "args": ["exec", "-i", "cad-studio", "python", "mcp_server.py"]
    }
  }
}
```

- **Claude Code** — add it with the CLI (no manual JSON editing):
  ```bash
  claude mcp add noodle -- docker exec -i cad-studio python mcp_server.py
  ```
- **Claude Desktop** — paste the JSON above into
  `claude_desktop_config.json` (Settings → Developer → Edit Config) and restart.
- **openclaw / other MCP clients** — point them at the same `command`/`args`.

The container shares `projects/` with the web UI, so a graph an agent builds is
**immediately visible in the browser** at <http://localhost:8090/nodes> (reload),
and vice-versa.

### Tools exposed

| Tool | Purpose |
|---|---|
| `cad_list_graphs` / `cad_create_graph` / `cad_delete_graph` | graph lifecycle |
| `cad_get_node_catalog` | list node types (optionally by category) |
| `cad_add_node` / `cad_connect` / `cad_set_param` / `cad_set_code` | build the graph |
| `cad_delete_node` / `cad_delete_connection` | edit the graph |
| `cad_execute` | run it → success, per-node errors, code, view **summary** |
| `cad_get_view` | last view; `fmt="mesh"` to include triangles (heavy) |
| `cad_get_code` | the build123d Python the graph transpiles to |
| `cad_get_panels` | read Panel node values |
| `cad_export` | write `step` / `stl` / `gltf`, returns the path |

Resources: `cad://nodes`, `cad://nodes/{type}`, `cad://graph/{id}`,
`cad://graph/{id}/code`, `cad://graph/{id}/view`.
Prompts: `cad_design`, `cad_modify`, `cad_analyze`.

### Recommended loop

1. `cad_get_node_catalog` to discover available node types and their sockets.
2. `cad_create_graph` → `cad_add_node` (primitives) → `cad_connect` (booleans,
   modifiers).
3. `cad_execute`; if it returns `node_errors` (a `node_id → message` map), fix the
   offending node and re-run.
4. Inspect the **view summary** (bbox / volume / area / counts) to verify
   dimensions — don't pull the full mesh unless you need geometry.
5. Iterate with `cad_set_param`, then `cad_export` when satisfied.

---

## 2. HTTP API (any language)

Base URL: `http://localhost:8090`. No auth (run locally/trusted only — see the
security note below).

```bash
# Discover node types
curl -s localhost:8090/api/nodes

# Create / overwrite a whole graph in one shot
curl -s -X POST localhost:8090/api/graph/demo \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo",
       "nodes":[{"id":"n1","type":"Box","params":{"length":20,"width":20,"height":10},"position":[100,100]}],
       "connections":[]}'

# Execute → view + per-node errors
curl -s -X POST localhost:8090/api/graph/demo/execute

# See the generated build123d code
curl -s localhost:8090/api/graph/demo/code

# Export STEP and download the STL
curl -s localhost:8090/api/graph/demo/export/step
curl -s -X POST localhost:8090/api/projects/demo/render
curl -s localhost:8090/api/projects/demo/download -o demo.stl
```

A graph is `{nodes, connections}` (see `CLAUDE.md` §4 for the schema). Each node
has `id`, `type` (from `/api/nodes`), `params`, `position`; each connection links
`from_node/from_socket → to_node/to_socket` over a typed wire.

---

## Security

The engine runs graph code — including `CodeBlock` / `Expression` nodes — as
**arbitrary Python in a subprocess, not yet sandboxed**. The HTTP API and MCP
server are **unauthenticated**. Run noodle **single-user, locally, trusted input
only**; never expose port 8090 to an untrusted network. Sandboxing is tracked as
**D3** in `PLAN_NODE_CAD.md`.
