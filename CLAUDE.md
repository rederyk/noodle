# noodle — agent guide

A **node-based parametric CAD app** built on [build123d](https://build123d.readthedocs.io).
You wire nodes in a web editor; the backend **transpiles the graph to build123d
Python**, runs it in an isolated worker, and returns an STL + a mesh "view" for
the 3D viewport. It ships with an **in-app AI copilot** (natural language → graph)
and an **MCP server** exposing the same operations.

This file is the orientation doc for an AI agent picking up the project. It
covers what it is, how to run it, how it's laid out, and how to change it safely.

---

## 1. Run it

The app is containerized (`noodle` service). The image is **build123d-only**
on purpose — cadquery 2.7.0 pins an OCP/OCCT build that conflicts with build123d
(see `PLAN_NODE_CAD.md` and the memory note "build123d/cadquery OCP conflict").

```bash
docker compose up -d --build      # build + start
docker restart noodle         # after backend code changes (see §6)
docker logs -f noodle         # tail logs
```

- Node editor: <http://localhost:8090/nodes>   ·   code view: `/ui` (read-only
  build123d generated from the graph)   ·   health: `/health`
- The container runs as the non-root user **noodle (uid 1000)**, Python 3.10,
  serving `uvicorn server:app` on 8090.
- Volumes (see `docker-compose.yml`): `./projects` is read-write; `cad_nodes/`,
  `webui/`, `server.py`, `mcp_server.py` are mounted **read-only**, so
  host edits are visible to the container but the running process must be
  restarted to re-import them.

**Copilot LLM backend** (env in `docker-compose.yml`): defaults to a free local
**Ollama** at `host.docker.internal:11434` (`COPILOT_MODEL`, e.g. `qwen2.5`).
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
                       projects list/delete, /api/graph/{name}/execute|code,
                       /api/graph/{name}/code?map=1 (code + editable param
                       source map), PATCH /api/graph/{name}/param (clamped
                       single-param edit; `_cb.<name>` targets a CodeBlock
                       override), /api/graph/{name}/codeblock/{id}/scan,
                       /api/nodes (catalog), /api/copilot/chat|status,
                       /api/agent/help (self-contained remote-agent guide =
                       cad_nodes/AGENT_HELP.md, also MCP cad_help/cad://help —
                       keep it in sync when the API surface changes),
                       /api/agent/tags (ToAgent provenance index, §7b),
                       /api/graph/{name}/slice_summary|section_outline (§7b),
                       /api/graph/{name}/progress (SSE: per-node execution events
                       of the run in flight, tailed from the workdir's
                       progress.jsonl — see transpiler `_ev`),
                       /api/system/health|logs|restart.
                       NOTE /execute runs via `asyncio.to_thread`: a graph run is
                       seconds of blocking CPU and must NOT hold the event loop,
                       or nothing else can be served meanwhile (the progress
                       stream included). Keep any new long route off the loop.
mcp_server.py        MCP server exposing the same cad_nodes.api operations.
webui/
  viewer.js          ★ the SHARED Three.js viewport (ES module served at
                       /static/viewer.js), imported by BOTH pages. `CadViewer`
                       owns the Z-up CAD scene (grid/lights/ViewHelper), the
                       animate loop, framing/resize, `loadSTL`, and the live
                       multi-mesh `renderPreviews(previews, {colorOf,wireOf,
                       onEmpty})` from a view.json. Page-specific behaviour stays
                       in the pages and hooks onto the exposed scene/camera/
                       previewGroup (nodes.html: the gizmo + click-to-select via
                       viewer.pick()). Both pages now render identically.
  index.html         the `/ui` code view — generated build123d source (read-only
                       text) + STL preview. Parameter literals are highlighted
                       and click-to-edit via a terminal-style inline editor that
                       PATCHes the graph param and re-renders — non-destructive
                       (the code is regenerated; structure stays in nodes.html).
                       See PLAN_CODE_PARAMS.md.
  nodes.html         the node editor + 3D viewer (litegraph-style). Holds
                       WIRE_COLORS; INPUT_ACCEPTS is fetched at boot from
                       /api/wiretypes (derived from casts.py, §5 — the inline
                       literal is only an offline fallback).
                       Execution glow (beginExecGlow/glowEvent/drawExecGlow): the nodes
                       light up AS THEY RUN. openProgress() subscribes to the SSE
                       /api/graph/{name}/progress BEFORE POSTing /execute, so no start
                       event is missed; each event opens or closes a node's span. A
                       node executing right now breathes amber; when it finishes it
                       settles and fades — green if it really recomputed, cold blue if
                       the memo cache served it, red if it threw.
                       Cost badges (drawCostBadge, toolbar "Costi" toggle, remembered
                       in localStorage `noodle:settings:showCost`): the same story made
                       to stay — last run's wall-clock on each node's title bar, same
                       colour vocabulary (blue "cache" = the memo store served it and it
                       cost nothing, amber→green = a real recompute with the hue set by
                       cost, red = it threw). The editor doubles as a profiler.
                       parseCbParams() mirrors transpiler.parse_codeblock_params:
                       a CodeBlock's `#@param`s become live widgets + dynamic
                       input sockets (overrides in the `_cb` param namespace),
                       editable via the ✎ Edit code modal. A BroadcastChannel
                       ('noodle:link') cross-links the two views: clicking a
                       value in /ui selects+flashes the node here; selecting a
                       node here scrolls /ui to it (nodeByGraphId tracks on-disk
                       ids). The /ui code view also scrubs numbers by drag,
                       Tab-cycles spans, and Ctrl+Z-undoes param edits.
cad_nodes/
  catalog.py         ★ the node registry. Declarative NodeDef per node type:
                       sockets (typed wires), params (widgets+defaults), and a
                       code_template that the transpiler fills in. ADD NODES HERE.
  casts.py           ★ wire types + the cast registry (§5) — the ONE place wire
                       compatibility is defined; WIRE_COMPATIBLE (backend) and
                       INPUT_ACCEPTS (frontend, via /api/wiretypes) derive from it.
  transpiler.py      ★ Graph -> build123d source. Flat "algebra" assignments in
                       topo order; group nodes (BuildPart/BuildSketch) emit
                       nested `with` blocks. PREAMBLE injects runtime helpers
                       (_at, _pushpull, _section, _bbox_plane, _rotate,
                       _select_subshapes). Each node is wrapped in try/except so
                       one failing node is recorded in __errors__, not fatal.
                       run(emit_map=True) / transpile_with_map() also return a
                       param<->code source map (sentinel-wrapped literals measured
                       on the final text) for the editable code view. A CodeBlock
                       transpiles like two connected nodes: `#@param` decls
                       (parse_codeblock_params) become the generated function's
                       named ARGUMENTS — body stays pure (declaration lines dropped),
                       each value appears once at the call site as an editable span
                       (override in node.params["_cb"], wired socket drives + fans
                       out). The body itself is an editable `code` span (kind=code).
                       transpile(memo=True) — the execute path ONLY, /ui code stays
                       clean — wraps each cacheable node in _memo_get/_memo_put
                       keyed by a content hash (params+code+upstream keys, immune
                       to var renumbering); non-deterministic nodes (Import*,
                       open(), random.) poison their lineage, display/export
                       side-effect nodes stay keyed but re-run. tests/test_memo.py.
                       In memo mode each node also brackets itself in `_ev()`
                       (PREAMBLE): a start/end NDJSON line appended+flushed to
                       __PROGRESS_PATH__ = the workdir's progress.jsonl (injected by
                       executor.build_script). That file is the ONLY progress channel
                       that works on BOTH paths — the warm worker redirects stdout
                       into a buffer during exec, and the cold subprocess has no pipe
                       home at all. The editor tails it over SSE and lights each node
                       AS IT RUNS.
  executor.py        Runs the generated script in a worker subprocess; captures
                       STL + view JSON + per-node errors. execute_graph(graph, workdir).
  worker.py / mesh_extractor.py   the subprocess + meshing. The warm worker owns
                       the persistent __MEMO__ store (LRU 256: node outputs,
                       preview meshes, view stats) — on a repeat run only the
                       dirty subtree re-executes/re-meshes (~8.5s -> ~0.5s on the
                       lego brick; cache dies with the worker = ⚙ warm toggle).
                       Live-path bboxes use optimal=False (~2s -> ~5ms each,
                       ≤1% oversized, view.bbox carries approx:true); exports
                       and picker signatures keep exact geometry.
  graph.py           Graph/Node/Connection dataclasses; from_dict/to_dict;
                       validate() (raises on incompatible wires / unknown sockets).
  api.py             High-level ops over a GraphStore: add_node, connect,
                       set_param, delete_node, execute, transpile. Shared by the
                       MCP server AND the copilot — the single source of truth.
  store.py           GraphStore: load/save projects/<name>/{graph,meta,view}.json
                       and output.stl.
  copilot.py         ★ in-app NL copilot (§7). OpenAI-compatible tool loop.
  slice_summary.py   retro-engineering perception (§7b): slice_summary
                       (symbolic cross-sections; STEP exact, STL arc-fitted)
                       + section_outline (one exact section, edge by edge).
  toposort.py        topological sort + cycle detection.
  catalog … examples/  sample graphs used by tests.
projects/            saved graphs (written as uid 1000 — host-editable).
tests/               test_engine.py, test_api.py — pure-Python (no build123d).
PLAN_NODE_CAD.md     the full design doc + node roadmap (~150 planned nodes).
PLAN_VIZ_ALGORITHMS.md  the "algorithms as geometry" example family (softmax,
                     gradient descent, determinant, CLT, Fourier, k-means…): the
                     pattern they share, the idioms, the gotchas, and what's next.
                     Read it before adding an explanatory example.
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
  ],
  "groups": [                                        // optional, editor-only
    { "title": "Base body", "bounding": [20,40,300,760], "color": "#3f589e" }
  ]
}
```

`groups` are LiteGraph group boxes that visually cluster nodes (title + bounding
rect + colour). They're **editor-only metadata**: the engine never reads them, but
`Graph` carries them through `to_dict`/`from_dict` so logical grouping survives
save/reload and api/copilot round-trips. Serialized/restored in `nodes.html`
(`toGraphJSON`/`fromGraphJSON`); created with Ctrl+G (`groupSelected`).

`params._ui` is another editor-only namespace (like CodeBlock's `_cb`): per-slider
drag window + step set via the slider's ⚙ (`{param: {min,max,step}}`). Sliders in
the editor are a custom `cadslider` widget — the drag window defaults to ±10
(clipped to catalog hard bounds, auto-grown to contain the value) and drag snaps
to the step; the typed ✎ field clamps only on the catalog's hard min/max. The
engine resolves params by catalog name, so it never sees `_ui`.

Execution writes `output.stl` + `view.json` (meshes) alongside it.

## 5. Wire types

Typed wires gate which output may feed which input. The single source of truth
is **`cad_nodes/casts.py`** (see PLAN_DATA_PROTOCOL.md): a small cast registry
`CASTS[(src, dst)] -> coercion helper` from which both tables are **derived** —
`WIRE_COMPATIBLE` (backend, enforced hard by `Graph.validate()`) and
`INPUT_ACCEPTS` (frontend, fetched at boot from `/api/wiretypes`; the literal in
`nodes.html` is only a fallback for when the endpoint is absent). They can no
longer drift — to change compatibility, edit `casts.py` only.

Types (constants in `casts.py`): `solid` (3D B-Rep — the old `geometry`),
`surface` (2D sketch/face — the old `sketch`), `curve`, `plane`, `vector`
(points), `selection` (picked sub-shapes), `mesh` (triangles — §5c), `data`,
`tree` (declared, unused).
`data` is the universal bus (any output → a `data` input; a `data` output feeds
everything except `selection`/`tree`). Registered casts: `surface→solid`,
`solid↔plane` (transforms treat a plane like geometry), `curve→surface`
(closed curve → face via `_face`), `selection→vector`, `solid/surface→mesh`
(`_to_mesh`). A `Socket` can also widen per-socket via `accepts=[…]`, carry an
advisory `subtype` (legend only, not validation), or set `raw=True` to opt out of
automatic boundary casts. The transpiler applies a registered cast automatically
at the wire boundary (`Transpiler._cast`).

## 5c. The mesh lane (triangles)

**build123d cannot model meshes** — it treats them as an I/O format. `import_stl`
returns a `Face` with only a triangulation and no surface (`is_valid=False`,
`volume=0`, booleans refused outright); `Mesher.read` sews every triangle into a
planar B-Rep face — **300s** to open a 147k-triangle STL, **81s** per boolean. And
OCCT has no remesh/decimate/mesh-repair at all. So triangles get their own lane,
on **trimesh** (MIT — noodle stays MIT; pymeshlab is GPL-3 and is deliberately NOT
a dependency). Full findings + measurements: **`PLAN_MESH_LANE.md`**.

- Nodes (category `mesh`, `catalog.py` §12b): `ImportMesh`, `ToMesh`, `MeshFix`
  (merge verts, drop dup/degenerate faces + stray shards, fill holes, fix normals),
  `MeshInspect` (text health report → wire into a `Display`), `ExportMesh`,
  `MeshUnion`/`MeshSubtract`/`MeshIntersect` + `MeshSimplify` (**manifold3d**,
  Apache-2.0), and `MeshToSolid` — the only bridge back, guarded by `max_tris`.
- **Two engine gotchas, both load-bearing** (measured — PLAN_MESH_LANE.md §9):
  `trimesh.Trimesh(...)` defaults to `process=True`, which re-merges manifold3d's
  already-welded output and **silently breaks the manifold** — `_from_manifold` must
  pass `process=False`. And a simplify tolerance at/above the part's wall thickness
  (volume/area) *tears the part apart* while the triangle count climbs, so
  `MeshSimplify` verifies its own result (volume drift + `decompose()` piece count)
  and raises rather than returning a broken mesh.
- `Transpiler._cast` only fires on the **item-access** branch: `multiple` collectors
  and `list_access` sockets skip it (as the B-Rep `Union`/`Subtract` already did), so
  `_mesh_bool` coerces every item with `_as_mesh` itself. A solid reaches `MeshUnion`
  uncast — by design, and there's a test pinning it.
- Runtime: the `Mesh` class in the transpiler PREAMBLE wraps a `trimesh.Trimesh`.
  It carries a `_noodle_mesh` marker because `mesh_extractor` runs as an imported
  module and *cannot* import a class that lives in the generated script's globals —
  so it duck-types instead of `isinstance`.
- **Transforms are NOT duplicated.** `Move`/`Rotate`/`Scale`/`Mirror` take a mesh
  directly: their `shape` socket lists `accepts=[…, WIRE_MESH]`, the PREAMBLE
  helpers branch on `_is_mesh` and apply a 4×4 (`_mesh_matrix`) instead of a
  `Location`, and `output_follows="shape"` carries the mesh type back out. There is
  no `MeshMove` and there must not be. (Arrays/Align don't take meshes yet — their
  templates still build `Pos(…) * shape` inline.) `Rotate` also takes an optional
  `pivot` point and an `about` select — world (global axis, the default) / part (own
  bbox centre) / group (collective centre; under fan-out the emitter hoists ONE
  `_pivot_of(…)` out of the lambda so the ensemble turns rigidly); centres are
  measured on the tessellation, like `PlaceOnBed`.
- **The cast is asymmetric on purpose.** `solid/surface → mesh` is automatic
  (tessellation: milliseconds, safely lossy) — drop a `Box` straight into a mesh
  input and it just works. `mesh → solid` is **not** a cast: rebuilding a B-Rep from
  triangles costs ~300s, so it must stay an explicit guarded node (`MeshToSolid`,
  phase 2), never an implicit coercion that hangs the app for five minutes because
  someone wired a mesh into a `Fillet`.
- Previews cost nothing: a mesh IS triangles, so `mesh_extractor` hands the arrays
  straight to the viewer with no tessellation step.
- Not yet built (phase 3): hull/smooth/split/refine, and meshes through
  `ArrayLinear`/`ArrayPolar`/`Align` (their templates still build `Pos(…) * shape`
  inline, so they need helpers first — the four core transforms already work).
  Isotropic remesh has no non-GPL implementation that survives a real part — see
  `PLAN_MESH_LANE.md` §5.
- Example graph: `cad_nodes/examples/mesh-lane.json` (seeded into `projects/`). Tests: `tests/test_mesh_lane.py`.

## 5d. Print physics (category `print`)

Five nodes that answer what a slicer never asks: **which way up, and why**
(`catalog.py` §12c, runtime in the PREAMBLE, full notes in **`PLAN_PRINT_PHYSICS.md`**).
A printed part is anisotropic — the bond between layers is worth roughly a third to two
thirds of the material within one — so orientation decides **where the part breaks**.

- `PlaceOnBed` (lowest point → z=0; serves BOTH lanes: it measures on the mesh and moves
  the original, so a solid stays a solid), `Drop` (PlaceOnBed as a scrubbable FALL: a
  `timeline` slider 0→1, analytic bounce with restitution fixed per `material` — plastic
  0.55, lead 0.08, rubber 0.85… — then, with `settle` on, the part TOPPLES for real:
  `_settle_plan` walks the quasi-static cascade on the convex hull (com outside the
  contact patch → tip about the nearest support edge until the next facet lands, ≤40
  steps, replayed partially at scrub time). The energy guard — every step must strictly
  lower the com — is what stops a sphere rolling forever while letting the edge-balanced
  cube go over; balanced ties resolve deterministically. t=1 is always fully at rest;
  optional `plane` input. Gizmo `kind:"timeline"` (nodes.html): Edit-on-canvas shows a
  Z-only translate arrow — pull the part down to advance t, lift to rewind, one part
  height ≈ the full slider; wired `t` locks it. LIVE REPLAY: `_drop` attaches the whole
  journey as data to its result (`_noodle_anim`: bounce segs + topple steps, world
  coords, baked t); `_preview_of` lifts it into previews[id].anim, and nodes.html
  (`dropMatrixAt`/`applyDropAnim`) replays any t as pure matrix math at 60fps while the
  slider or a wired Number Slider drags (✥ fastDrag), mesh pose = M(t)·M(t_baked)⁻¹ —
  the engine re-bakes exactly when the drag settles. COLLISIONS: the `collide` toggle —
  off by default, it costs real compute — un-fans multiple shapes wired into one Drop
  into ONE scene (`_drop_collide` → `_dyn_sim`) and runs REAL rigid-body dynamics
  (pybullet, DIRECT mode): every part is its convex hull, they all fall TOGETHER —
  colliding mid-air, pushing each other over, tumbling, stacking — simulated once at a
  fixed 1/240s step (deterministic per scene) in MILLIMETRES directly (so the fixed
  collision margin is sub-micron, not the ~1mm it becomes when shrunk to metres; CCD +
  hull-volume masses), recorded as 60Hz keyframes per body until the scene sleeps
  (restitutionVelocityThreshold=100mm/s + friction/damping, ~0.6-1s). Each returned
  shape carries its own keyframe plan (`_noodle_anim` kind "keys"); mesh_extractor emits
  a `{kind:"Scene", bodies:[...]}` preview, viewer.js builds a Group of independently-
  posable meshes, and nodes.html (`keyInterp`/`sceneBodyPose`) replays the whole pile
  LIVE (lerp+slerp) while the slider drags. Limits: hulls not true meshes, chaotic like
  real falling), `PrintCheck` (report → Panel), `OverhangFaces`
  (the faces needing support, as a mesh of its own → its own colour in the viewer),
  `SupportVolume` (the support as a BODY), `OrientForPrint` (every stable pose scored; two
  outputs — the oriented mesh and the table saying why — from ONE search, via
  `_emit_orient`, modelled on `_emit_center`).
- **Support is a sweep and a boolean, not an estimate**: a prism from every overhanging
  triangle down to the bed, unioned (`manifold3d.batch_boolean`), minus the part *and the
  part shifted down by the clearance gap* (that second copy carves the space the support
  must leave, or it welds itself on). Checked against a pencil: a sphere of r=20 gives
  1.63 cm³ where the integral says 1.73. ~0.6 s at 20k triangles — so `OrientForPrint`
  uses it while the part is under `exact_below` triangles and the `area × height` proxy
  above, **all or nothing**, and the report says which. It is the ENVELOPE (a slicer fills
  it sparse) and it does not know about bridges — `PLAN_PRINT_PHYSICS.md` §5.
- **The weak plane** is the smallest cross-section perpendicular to Z: `manifold3d`'s
  `slice(z).area()` (~0.01 s for 80 sections, so scoring 100 poses is free). It is a
  property of the part *in this orientation*, and turning the part moves it.
- **Stable poses** = the convex-hull faces whose polygon contains the projected centre of
  mass — scipy, because trimesh's `compute_stable_poses` needs `networkx`+`shapely`, which
  are not in the image. Cluster hull normals by TOLERANCE, not by a rounded key.
- Two traps, both paid for: the faces resting **on the bed** must be excluded from the
  overhang (a flat base points down too, and counting it makes the one support-free
  orientation look worst), and `PlaceOnBed` must measure on the **tessellation**, not on
  `Shape.bounding_box()` — the fast OCCT box is oversized (hence `view.bbox.approx`), so a
  part dropped by it hovers above the bed.
- **Strength needs a load.** With a `load` vector the score is how much of it crosses the
  layers; with none declared the optimiser optimises for printability and will hand you the
  weakest possible part. That is the whole of `examples/print-orientation.json`.
- Tests: `tests/test_print.py`.

## 5b. Lists & fan-out (Grasshopper-style)

Inputs have a data-access mode (`Socket.list_access`):

- **item-access** (default): the input FANS OUT. Wire several connections into it
  (shift-drag in the editor) — or feed it a list-producing node — and the node
  runs once per item, producing a **list** output. Two points → one Circle → two
  circles. Scalars broadcast; shorter lists reuse their last item (longest-match).
- **list_access** (`Socket("list", …, list_access=True)`) and every `multiple`
  collector: consume the whole list as one value (List/Sort/Item/Slice…, Loft).

**Params as inputs:** an input socket that shares a param's name overrides the
widget when wired, and falls back to it when not (e.g. `Vector`/`ConstructPoint`
x/y/z, `Move` `offset`). Wire a list into such an input and the node fans out —
`Range → ConstructPoint.x → Move.offset` scatters one copy per position.

The transpiler wraps a fanned node as `_fanout(lambda …: <expr>, {…})` and tracks
which node outputs are lists (`_produces_list` + `_LIST_PRODUCERS`) so lists
propagate down a chain. List nodes live in the `data` category (ListCreate,
ListSort, ListItem, ListReverse, ListSlice, First/Last, Flatten, Concat, …);
`_sort` uses build123d `ShapeList.sort_by` for shapes, Python `sorted` otherwise.
Other list-producers: `Voronoi2D` (scipy → cell faces), `DivideSurface`
(`Face.position_at` UV grid → points) — both fan out downstream (Extrude per
cell, scatter per point). scipy/numpy are available in the worker.
Frontend multi-connect = dynamic input slots sharing one socket name (see
`onConnectionsChange` + `fromGraphJSON` in `nodes.html`).

## 6. How to change things

**Add or edit a node** — almost always pure data in `catalog.py`:

```python
register(NodeDef("MyNode", "category", "My Node",
    inputs=[Socket("shape", WIRE_SOLID), Socket("plane", WIRE_PLANE, required=False)],
    params=[_f("amount", 1.0, 0.0, 100)],          # _f/_i = float/int slider helpers
    outputs=_geo(),                                 # _geo/_sk = solid/surface output
    code_template={"algebra": "my_op({shape}, {amount})"},
    description="..."))
```

`{socket}` → the upstream variable; `{param}` → the formatted value. If the node
needs runtime logic that doesn't fit one expression, add a helper to the
transpiler **PREAMBLE** and call it from the template (e.g. `_bbox_plane`,
`_rotate`). Wire compatibility changes go in `cad_nodes/casts.py` (§5) — the
frontend picks them up from `/api/wiretypes`.

**Group nodes** (BuildPart/BuildSketch) use `is_group=True` + a `builder`
template and emit nested `with` blocks — see existing examples.

**Apply / reload rules:**
- Backend Python change → `docker restart noodle` (process caches imports;
  the read-only mount alone isn't enough).
- Frontend (`webui/*.html`) change → hard-refresh the browser (Ctrl+Shift+R);
  the file is static and cached.
- Verify engine logic fast on the host with `.venv-b123d` (§2) before restarting.

**Gotchas:**
- The container runs as **uid 1000** (`noodle`), matching the typical host user,
  so `projects/` is host-editable. Projects created by pre-non-root images are
  root-owned — fix once with `sudo chown -R 1000:1000 projects feedback`. The
  canonical writer is still the server API (`/api/graph/{name}/...` or the UI).
- Project names are validated (`cad_nodes/store.py::validate_graph_id`): one
  path segment, `[A-Za-z0-9][A-Za-z0-9._ -]{0,63}`. Anything else is a 400
  (path-traversal guard) — keep any new route that touches `projects/` on
  `project_dir()`/`GraphStore.dir()`.
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

## 7b. Retro-engineering ("retroeng") — STL/STEP → parametric graph

When the user says **"retroeng"** (e.g. *"fai il retroeng dell'STL che ti ho
passato"* / "reverse-engineer this part"), they mean the `PLAN_RETROENG.md`
workflow: rebuild an imported mesh/solid as a **parametric graph of catalog
nodes**. "The file I just passed" resolves through the **ToAgent tag index**:
in the editor the user tags an ImportSTL/ImportSTEP node with a `ToAgent` node
(label + auto-stamped save date); `GET /api/agent/tags` (= `api.agent_tags`,
MCP `cad_agent_tags`) lists every tag across ALL projects with the tagged
file's project-relative path. Pick the most recent (or label-matching) entry —
don't ask "which STL?".

The loop (validated on real parts: STEP rebuilt at Δvolume 0.05%, a 59k-tri
STL at +2.2% — see `projects/retro_nodes` and `projects/retromy`):

1. **Perceive** — `GET /api/graph/{name}/slice_summary?path=<file>&n=10`
   (`api.slice_summary`): symbolic cross-sections on all 3 axes (`circle r=3
   @(x,y)`, `rect 40x30`; dedup "z=a…b identical" ⇒ extrusion + its height).
   STEP sections are exact; STL is arc-fitted. The `text` field is the
   LLM-facing format. Omit `path` to slice the graph's OWN result.
2. **Microscope** where the summary is ambiguous —
   `.../section_outline?axis=z&pos=…`: ONE exact section, edge by edge.
   Mesh gotcha: a single section can drop loops near tangent surfaces —
   confirm with nearby sections or with per-section areas.
3. **Rebuild** with catalog nodes via `cad_nodes.api` (add_node / connect /
   set_param). Proceduralize, don't trace: constant section → Extrude; N equal
   circles in a regular layout → ArrayLinear/ArrayPolar with a count slider,
   not copies; small rounds → a downstream Fillet; overall dims → sliders.
   The user's stated intent about what to parameterize wins over defaults.
4. **Verify with the same tool** — execute, re-slice your own result
   (`slice_summary` without `path`) and diff the two summaries as text;
   comparing per-section AREAS localizes residuals; bbox + volume checksum
   is the final seal.

Not yet built (see PLAN_RETROENG.md): vision contact-sheet, gcode stripper,
numeric `cad_compare`.

## 8. Tests

`tests/` are pure-Python (no build123d needed): toposort, graph validation,
transpiler output, api ops.

```bash
python -m pytest tests/ -v        # pytest may need installing in your env
```
