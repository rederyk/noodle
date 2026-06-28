# Plan — Foundational parametric nodes (curves → domains → graph mapper → variable loft)

## Why this plan exists

The Voronoi work delivered a *high-level* component (cells on a cylinder) but
leaned on a CodeBlock and a hard-coded base solid. The real value is the layer
*underneath*: the small, composable atoms — lines, curves, domains, series,
divide, remap, graph mapper, orient, loft — that together cover most of
parametric 3D modelling. Voronoi, variable loft, lattices, panelling, etc. are
all just *compositions* of those atoms.

**Working discipline (non-negotiable):**
1. Build the most basic piece first. One node = one clear, single responsibility.
2. Verify each node **headless** (`.venv-b123d` transpile+execute) the moment it
   exists — geometry, wire type, fan-out behaviour.
3. Push a tiny demo graph that uses it, before moving on.
4. Only then compose upward. No jumping to the end component.
5. Prefer extending the catalog (data) + a PREAMBLE helper over special-casing.

Reference target workflow that this foundation must make trivial (no CodeBlock):

```
Line/Spline ─→ DivideCurve(count) ─→ frames(planes) ─┐
Series / GraphMapper(t) ─→ radii ─→ Circle(radius) ──┴─→ ToPlane ─→ Loft → variable solid
```

Verified feasible: build123d gives `Line/Spline/ThreePointArc`,
`curve.location_at(t)` → a `Location`, `Plane(loc) * Circle(r)` orients a
profile, `loft([profiles])` builds the variable section. numpy/scipy are in the
worker for graph-mapper evaluation.

---

## Inventory — have vs missing

| Layer | Have | Missing (this plan) |
|---|---|---|
| Curves | (Circle/Rect… as **sketches**), MakeFace | **Line, Polyline, Arc3pt, ArcCenter, Spline**, (Bezier, Helix) |
| Curve ops | — | **DivideCurve (→ points + frames), EvaluateCurve, CurveEndpoints, CurveLength** |
| Domains/series | ListRange | **ConstructDomain, Series, DivideDomain, Remap, Bounds** |
| Mapping | Expression, Clamp, math | **GraphMapper (richgraph)** |
| Placement | origin input, Move, BoundingPlane | **ToPlane / Orient** (shape → plane/frame) |
| Loft/sweep | Loft (sections) | **Loft `ruled`/`closed`, Sweep (profile along rail)** |
| Params→inputs | Vector/Point xyz, Move.offset, Shell.thickness | **sweep the rest** (radius, dims, amount, angle…) |
| Infra | mesh of solids/sketches | **curve + point preview in the viewer** |
| Data | List*, fan-out | (optional) **data trees: Graft/Flatten/Path** |

---

## Infra prerequisites (do first, they unblock everything)

- **P0a — Curve preview.** `WIRE_CURVE` is in `_PREVIEWABLE` but the viewer/mesh
  path must render edges/wires as polylines (sample `edge @ t`), not tessellated
  faces. Without this, every curve node is invisible and undebuggable.
  *Check `mesh_extractor` + `nodes.html` viewer; add a polyline path for curves.*
- **P0b — Point preview.** Optionally render `WIRE_VECTOR` points (and point
  lists) as small dots, so DivideCurve / PopulateGeometry are inspectable on
  their own (today they’re only visible once geometry is placed at them).
- **P0c — Params-as-inputs sweep.** We already support "input socket named like a
  param overrides the widget" (see `_emit_simple`, `Vector`/`Move`). Apply it to
  the high-value numeric params so series/graph-mappers can drive them:
  `Circle.radius`, `Rectangle.width/height`, `Polygon.radius/sides`,
  `Cylinder.radius/height`, `Box.width/height/depth`, `Sphere.radius`,
  `Cone.*`, `Extrude.amount`, `Revolve.angle`, `Fillet.radius`, `Chamfer.length`,
  `Offset.amount`, `Rotate.angle`, `Scale.factor`.
  *Pure data-entry: add optional `Socket(name, WIRE_DATA, required=False)` next to
  each param. Zero transpiler changes. DoD: drive each from a slider AND a list.*

---

## Phase 1 — Curves (WIRE_CURVE producers)

Each outputs `WIRE_CURVE` (a build123d Edge/Wire). All point inputs are
`WIRE_VECTOR` and fan out.

| Node | Inputs / params | build123d |
|---|---|---|
| **Line** | start, end (vectors) | `Line(p0, p1)` |
| **Polyline** | points (list_access), `closed` bool | `Polyline(*pts, close=closed)` |
| **Arc3pt** | p0, mid, p1 | `ThreePointArc(p0, mid, p1)` |
| **ArcCenter** | center; params radius, start°, end° | `CenterArc(center, r, a0, a1)` |
| **Spline** | points (list_access); `tangents?` | `Spline(*pts)` |

DoD per node: renders as a curve (needs P0a); `Line` feeds `MakeFace`/`Divide`;
demo `line_demo` (two ConstructPoints → Line).

## Phase 2 — Curve evaluation & division (the frames layer)

This is the keystone: oriented **frames** along a curve are what let profiles be
placed for loft/sweep.

| Node | Out | build123d |
|---|---|---|
| **EvaluateCurve** | point, tangent, plane | `position_at(t)`, `tangent_at(t)`, `Plane(location_at(t))` |
| **DivideCurve** | points (list), frames (list of planes) | `[Plane(c.location_at(i/(n-1)))…]` |
| **CurveEndpoints** | start, end | `@0`, `@1` |
| **CurveLength** | number | `.length` |

DoD: `DivideCurve → frames`; place a `Box` at each point (origin) → beads on the
curve. `DivideCurve` is a **list-producer** (downstream fans out).

## Phase 3 — Domains, Series, Remap

Represent a domain as a 2-element list `[min, max]` (plain data; no new wire
type). Remap is the glue between series, bounds and parameters.

| Node | Behaviour |
|---|---|
| **ConstructDomain** | min, max → `[min, max]` |
| **Series** | start, step, count → `[start + i*step]` (semantic alias of ListRange) |
| **DivideDomain** | domain, count → N values spanning it (endpoints inclusive) |
| **Remap** | value, source `[a,b]`, target `[c,d]` → `c + (v-a)/(b-a)*(d-c)`; fan-out |
| **Bounds** | list → `[min, max]` |

DoD: `Series → Remap([0,n]→[2,8]) → Circle.radius` gives a graded set of radii.

## Phase 4 — Graph Mapper ("richgraph")

A node whose value is an **editable function curve** `f: [0,1] → [0,1]`, then
optionally remapped to a target domain. This is what makes "variable radius from
a graph" direct and visual.

- **Inputs:** `t` (number or list, item-access → fan-out), optional `domain`.
- **Params:** `curve` = list of control points `[[x,y],…]` (stored in node params);
  `mode` = `linear | monotone | bezier`; presets to seed the points
  (`linear, ease-in/out, sine, parabola, s-curve`).
- **Output:** mapped value(s).
- **Backend helper** `_graphmap(t, pts, mode)`: `numpy.interp` (linear),
  `scipy.interpolate.PchipInterpolator` (monotone, no overshoot), or De Casteljau
  (bezier). Vectorised; fan-out over lists.
- **Frontend (the real work):** a small canvas widget drawn on the node body —
  plot the curve, drag control points, add/remove points, pick a preset; serialise
  to `node.params.curve`. Reuse the litegraph custom-widget pattern
  (`onDrawForeground` + mouse handling, like the bypass overlay / eye widget).

DoD: `DivideCurve` gives t=0..1 per station → `GraphMapper(t)` → radii → variable
loft profile bulges/necks exactly as the drawn curve.

## Phase 5 — Orientation / placement

| Node | Behaviour | build123d |
|---|---|---|
| **ToPlane** | shape + plane → shape re-seated on that plane/frame | `plane * shape` |
| (later) **Orient** | from-plane, to-plane, shape | `to.location * from.location.inverse() * shape` |

`ToPlane` fan-out zips a list of shapes with a list of frames (broadcast rules
already in `_fanout`). DoD: `Circle` list + `DivideCurve` frames → oriented
profiles ready to loft. (`MapToSurface` is the UV-domain cousin of this; keep both.)

## Phase 6 — Variable Loft & Sweep

- **Loft**: add `ruled` (straight vs smooth) and `closed` (periodic) params; it
  already lofts an ordered `sections` list.
- **Sweep**: profile + rail (curve) → solid. `sweep(profile, path=rail)`; param
  `is_frenet`.
- **Validate end-to-end, no CodeBlock:**
  `Spline → DivideCurve(frames) ; Series|GraphMapper → radii ;
   Circle(radius) → ToPlane(frames) → Loft` → a variable-section horn/vase.
  Push as `variable_loft` demo.

## Phase 7 — (optional) Data trees

Only if fan-out proves insufficient for grid/branched data (e.g. a U×V field of
profiles): `TreeCreate, TreeBranch, Graft, Flatten, TreePaths`. Defer until a
real workflow needs branches; fan-out + lists cover the linear cases.

---

## Sequencing & definition of done

Order: **P0 infra → P1 curves → P2 divide/frames → P3 domains/remap → P5 ToPlane
→ P6 variable loft (first full payoff) → P4 graph mapper → P7 trees**.
(Phase 4’s frontend is heavy; the variable loft already works with Series+Remap,
so the graph mapper is an enhancement, not a blocker — hence after the first
payoff.)

For **every** node, "done" means:
1. `NodeDef` in `catalog.py` (+ PREAMBLE helper if it needs runtime logic),
   correct wire types, `list_access`/params-as-input set deliberately.
2. Headless transpile+execute test proving geometry + fan-out.
3. A one- or two-node demo graph pushed to the app and executed clean.
4. Wire-type changes mirrored in `webui/nodes.html` `INPUT_ACCEPTS`; restart
   container; note any browser hard-refresh.

This file is the running checklist; tick phases off as they land.

## Status

- [x] **P0c** — params-as-inputs sweep (radius/dims/amount/angle/factor… optional
  `_pin()` sockets on primitives, ops, modifiers, transforms). Verified: a Range
  into `Circle.radius` fans out to N circles.
- [x] **P0a/P0b** — curve & point preview. `mesh_extractor._preview_of` now has
  three render paths (points → dots, mesh → triangles, curves → polylines);
  `WIRE_VECTOR` added to `_PREVIEWABLE`; `nodes.html` renders `THREE.Points` /
  `THREE.LineSegments`. (Frontend needs a browser hard-refresh to take effect.)
- [x] **P1** — curves: `Line, Polyline, Arc3pt, ArcCenter, Spline` (→ WIRE_CURVE).
  Verified headless + live demo `p1_curves_demo`; Polyline→MakeFace→Extrude and
  Line fan-out both work.
- [x] **P2** — divide / frames: `DivideCurve` (→ frames, list of Planes whose Z =
  tangent), `EvaluateCurve` (frame at t, fans), `CurveEndpoints`, `CurveLength`,
  plus `DeconstructPlane` (Plane Origin → point) to bridge frames→points. Verified:
  DivideCurve→DeconstructPlane→Box = beads on a curve. Single-output by design (the
  transpiler is one-var-per-node); the Plane is the first-class "frame" object.
- [x] **P3** — domains / series / remap: `ConstructDomain, Series, DivideDomain,
  Remap, Bounds`. Verified: `Series → Remap → Circle.radius` = graded radii;
  domain wires override Remap's scalar widgets.
- [x] **P5** — `ToPlane` (sketch + plane → re-seated sketch; zips a list of
  profiles with a list of frames). Verified: graded circles seated on curve frames.
- [x] **P6** — variable loft & sweep: `Loft` now takes a single list of sections
  (+`ruled`); new `Sweep` (profile along path, auto-seated). **End-to-end payoff
  verified live** — `variable_loft` demo (Spline→DivideCurve; Series→Remap→radii;
  Circle→ToPlane→Loft) builds a variable-section solid with NO CodeBlock.
- [ ] P4 — graph mapper (heavy frontend canvas widget; enhancement, not a blocker)
- [ ] P7 — data trees (optional)

### Notes / discoveries
- **2D primitives are now curves.** `Circle/Rectangle/RoundedRectangle/Ellipse/
  Polygon` emit a closed `WIRE_CURVE` (via `_outline()`), not a filled sketch —
  they read as curves (Grasshopper-style) and feed DivideCurve/Sweep/MakeFace.
  A **Surface (Make Face)** node patches a closed curve into a face. Face-consuming
  ops (Extrude/Revolve/Sweep/Thicken/`_loft`) auto-fill a curve via `_face()` so
  pre-existing graphs keep producing solids. (feedback/20260628-121305-voronoi-vase)
- The transpiler is **one variable per node**, so multi-output nodes aren't
  supported. Resolved cleanly by making the `Plane` the carrier of both position
  and orientation (DivideCurve → frames), with `DeconstructPlane` as the
  points extractor. Revisit only if a node genuinely needs two unrelated outputs.
- `loft` has no `closed` param in this build123d; only `ruled` was added.
- Backend changes are all verified headless via `.venv-b123d`; the **frontend
  preview changes (curve polylines / point dots, new nodes) need a browser
  hard-refresh** to show up. Demos pushed: `p1_curves_demo`, `variable_loft`.
