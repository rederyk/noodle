# cad-studio — agent guide

A **node-based parametric CAD app** built on [build123d](https://build123d.readthedocs.io).
You wire nodes in a web editor; the backend **transpiles the graph to build123d
Python**, runs it in an isolated worker, and returns an STL + a mesh "view" for
the 3D viewport. It ships with an **in-app AI copilot** (natural language → graph)
and an **MCP server** exposing the same operations.

This file is the orientation doc for an AI agent picking up the project. It
covers what it is, how to run it, how it's laid out, and how to change it safely.

---

## 1. Run it

The app is containerized (`cad-studio` service). The image is **build123d-only**
on purpose — cadquery 2.7.0 pins an OCP/OCCT build that conflicts with build123d
(see `PLAN_NODE_CAD.md` and the memory note "build123d/cadquery OCP conflict").

```bash
docker compose up -d --build      # build + start
docker restart cad-studio         # after backend code changes (see §6)
docker logs -f cad-studio         # tail logs
```

- UI: <http://localhost:8090/ui>   ·   node editor: `/nodes`   ·   health: `/health`
- The container runs as **root**, Python 3.10, serving `uvicorn server:app` on 8090.
- Volumes (see `docker-compose.yml`): `./projects` is read-write; `cad_nodes/`,
  `webui/`, `server.py`, `mcp_server.py`, `backends/` are mounted **read-only**, so
  host edits are visible to the container but the running process must be
  restarted to re-import them.

**Copilot LLM backend** (env in `docker-compose.yml`): defaults to a free local
**Ollama** at `host.docker.internal:11434` (`COPILOT_MODEL`, e.g. `qwen3.5:cloud`).
For a keyed OpenAI-compatible provider instead, set `COPILOT_BASE_URL` +
`COPILOT_API_KEY` + `COPILOT_MODEL` (Groq / OpenRouter / Gemini's OpenAI endpoint…).

## 2. Develop & verify without Docker

There is a host venv `.venv-b123d/` with build123d installed. Use it to transpile
and execute a graph directly — the fastest way to verify an engine change:

```bash
.venv-b123d/bin/python - <<'PY' 2>/dev/null   # 2>/dev/null hides fontconfig noise
import json, pathlib
from cad_nodes.graph import Graph
from cad_nodes.transpiler import transpile
from cad_nodes.executor import execute_graph
g = Graph.from_dict(json.loads(pathlib.Path("projects/<name>/graph.json").read_text()))
print(transpile(g))                                   # inspect generated build123d source
view = execute_graph(g, pathlib.Path("/tmp/work"), timeout=60).get("view")
print(view["success"], view.get("node_errors"))       # per-node errors if any
PY
```

Or drive the live server: `curl -s -X POST localhost:8090/api/graph/<name>/execute`.

## 3. Architecture & file map

```
server.py            FastAPI HTTP API (port 8090). Routes under /api/* :
                       projects CRUD, /api/graph/{name}/execute, /api/nodes
                       (serves the catalog), /api/copilot/chat|status.
mcp_server.py        MCP server exposing the same cad_nodes.api operations.
webui/
  index.html         landing / project list
  nodes.html         the node editor + 3D viewer (litegraph-style). Holds
                       WIRE_COLORS and INPUT_ACCEPTS — a MIRROR of the backend
                       wire-compatibility table that MUST be kept in sync (§5).
cad_nodes/
  catalog.py         ★ the node registry. Declarative NodeDef per node type:
                       sockets (typed wires), params (widgets+defaults), and a
                       code_template that the transpiler fills in. Also defines
                       the wire types and WIRE_COMPATIBLE table. ADD NODES HERE.
  transpiler.py      ★ Graph -> build123d source. Flat "algebra" assignments in
                       topo order; group nodes (BuildPart/BuildSketch) emit
                       nested `with` blocks. PREAMBLE injects runtime helpers
                       (_at, _pushpull, _section, _bbox_plane, _rotate,
                       _select_subshapes). Each node is wrapped in try/except so
                       one failing node is recorded in __errors__, not fatal.
  executor.py        Runs the generated script in a worker subprocess; captures
                       STL + view JSON + per-node errors. execute_graph(graph, workdir).
  worker.py / mesh_extractor.py   the subprocess + meshing.
  graph.py           Graph/Node/Connection dataclasses; from_dict/to_dict;
                       validate() (raises on incompatible wires / unknown sockets).
  api.py             High-level ops over a GraphStore: add_node, connect,
                       set_param, delete_node, execute, transpile. Shared by the
                       MCP server AND the copilot — the single source of truth.
  store.py           GraphStore: load/save projects/<name>/{graph,meta,view}.json
                       and output.stl.
  copilot.py         ★ in-app NL copilot (§7). OpenAI-compatible tool loop.
  toposort.py        topological sort + cycle detection.
  catalog … examples/  sample graphs used by tests.
backends/            openscad.py, pythonocc.py — alternate/legacy backends.
projects/            saved graphs (root-owned — see §6 gotcha).
tests/               test_engine.py, test_api.py — pure-Python (no build123d).
PLAN_NODE_CAD.md     the full design doc + node roadmap (~150 planned nodes).
```

## 4. Data model

A project is `projects/<name>/graph.json`:

```jsonc
{
  "name": "demo",
  "nodes": [
    { "id": "n1", "type": "Sphere", "params": {"radius": 3},
      "position": [120,80], "preview": true }      // preview: per-node eye override
  ],
  "connections": [
    { "id": "l1", "from_node": "n1", "from_socket": "result",
      "to_node": "n2", "to_socket": "shape" }
  ]
}
```

Execution writes `output.stl` + `view.json` (meshes) alongside it.

## 5. Wire types

Typed wires gate which output may feed which input. The table lives in TWO places
that **must stay in sync**:

- backend: `cad_nodes/catalog.py` → `WIRE_COMPATIBLE` (`output_type -> {accepted input types}`),
  enforced hard by `Graph.validate()`.
- frontend: `webui/nodes.html` → `INPUT_ACCEPTS` (`input_type -> [accepted output types]`,
  the inverse), which decides whether a wire can be dragged.

Types: `geometry` (solids), `sketch` (2D), `curve`, `plane`, `vector` (points),
`selection` (picked sub-shapes), `data`, `tree`. `data` is the permissive
fallback. Note `geometry` and `plane` are intentionally interchangeable so the
transform nodes treat a plane like any geometry.

## 6. How to change things

**Add or edit a node** — almost always pure data in `catalog.py`:

```python
register(NodeDef("MyNode", "category", "My Node",
    inputs=[Socket("shape", WIRE_GEOMETRY), Socket("plane", WIRE_PLANE, required=False)],
    params=[_f("amount", 1.0, 0.0, 100)],          # _f/_i = float/int slider helpers
    outputs=_geo(),                                 # _geo/_sk = geometry/sketch output
    code_template={"algebra": "my_op({shape}, {amount})"},
    description="..."))
```

`{socket}` → the upstream variable; `{param}` → the formatted value. If the node
needs runtime logic that doesn't fit one expression, add a helper to the
transpiler **PREAMBLE** and call it from the template (e.g. `_bbox_plane`,
`_rotate`). If you change wire types, update `INPUT_ACCEPTS` in `nodes.html` too.

**Group nodes** (BuildPart/BuildSketch) use `is_group=True` + a `builder`
template and emit nested `with` blocks — see existing examples.

**Apply / reload rules:**
- Backend Python change → `docker restart cad-studio` (process caches imports;
  the read-only mount alone isn't enough).
- Frontend (`webui/*.html`) change → hard-refresh the browser (Ctrl+Shift+R);
  the file is static and cached.
- Verify engine logic fast on the host with `.venv-b123d` (§2) before restarting.

**Gotchas:**
- `projects/` is **root-owned** (the container writes as root). To edit a graph
  file from the host, use `sudo cp` / `sudo tee`; the canonical writer is the
  server API. Prefer changing graphs via `/api/graph/{name}/...` or the UI.
- Running build123d on the host prints noisy fontconfig warnings to **stderr** —
  redirect `2>/dev/null` and read stdout.
- The copilot/MCP both go through `cad_nodes.api`; new capabilities belong there
  so all three surfaces (UI, MCP, copilot) get them.

## 7. The AI copilot — scope & guardrails

`cad_nodes/copilot.py` drives an OpenAI-compatible tool loop bound to ONE graph.
Tools: `get_graph`, `get_node_def`, `add_node`, `copy_node`, `connect`,
`set_param`, `delete_node`, `execute`. It has **no tool that edits app code or
node definitions** — by construction it can only manipulate a graph.

Enforced policy (system prompt + tool layer):
- It **assembles workflows** from existing catalog nodes and **creates new custom
  nodes from scratch** (a custom node is a `CodeBlock` — arbitrary build123d code
  in its `code` param).
- It must **never** modify the app or any built-in node's behaviour.
- It may **not edit the `code` of a custom node that pre-existed the conversation**.
  `set_param` refuses an in-place code edit of such a node; the model is told to
  warn the user and use `copy_node` to work on a duplicate, leaving the original
  intact. (Nodes created in the current session are freely editable — they're
  tracked in `state["created"]`.)

If you extend the copilot, preserve these invariants.

## 8. Tests

`tests/` are pure-Python (no build123d needed): toposort, graph validation,
transpiler output, api ops.

```bash
python -m pytest tests/ -v        # pytest may need installing in your env
```
