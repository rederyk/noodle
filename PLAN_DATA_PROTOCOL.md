# Plan — A coherent data-flow protocol (types, container/legend nodes, node fixes)

## Why this exists

The wire-type system grew organically and is **incoherent at the edges**: the
runtime helpers are mostly polymorphic (they work on any build123d shape), but the
*type gates* on sockets are narrow and inconsistent, so the editor blocks
connections that would actually run. Concrete symptoms the user hit:

- **Select Vertex won't accept a curve** to pull its points — yet
  `_select_subshapes` already returns vertices for a `Wire` (verified: a hex
  Polygon curve → 6 vertices). Only the `geometry`-only input gate blocks it.
- **Transforms (Move/Rotate/Scale/Mirror/Array) won't accept a curve** — yet
  `_move/_rotate/_scale` all return a transformed `Wire` at runtime. Same gate.
- **Getting points out of things is fragmented**: SelectVertex (geometry only),
  CurveEndpoints (curve), DeconstructPlane (plane→point), DivideCurve (→*frames*,
  not points), DivideSurface (→points). No single rule.
- **`data` is a black box**: int, float, bool, str, list, and domain `[min,max]`
  all ride the same `data` wire. You can't use the wire as a *legend*, can't
  validate, and the Panel has to guess what it's looking at.

Goal of this doc (code comes later): **(1)** enumerate the data types and what
each really carries, **(2)** define one protocol for how nodes produce/consume/
coerce them, **(3)** design a *container / legend* node per type (the
caster/"transformer" role is deferred), **(4)** list every node that must be
adapted.

---

## 1. The data types today (wire types)

Source of truth: `cad_nodes/catalog.py` → `WIRE_*` + `WIRE_COMPATIBLE`; mirror in
`webui/nodes.html` → `INPUT_ACCEPTS` + `WIRE_COLORS`.

| Wire type | Runtime payload (build123d) | Produced by | Consumed by | Notes |
|---|---|---|---|---|
| `geometry` | `Solid` / `Part` / `Compound` (3D) | primitives_3d, boolean, extrude/revolve/loft/sweep, transforms, import STEP/STL | boolean, modifiers, transforms, select, section, export | the "solid/brep" type |
| `sketch` | `Sketch` / `Face` (2D filled) | MakeFace, Text, Section, Voronoi2D, MapToSurface, ToPlane, BuildSketch | extrude/revolve/loft/sweep/thicken, map/voronoi/populate (as region) | 2D *surface* |
| `curve` | `Wire` / `Edge` (1D) | primitives_2d (now!), curves (Line/Arc/Spline…), import DXF/SVG | MakeFace, DivideCurve, EvaluateCurve, Curve*, Sweep.path | 1D *curve* |
| `plane` | `Plane` / `Location` (a frame) | PlaneOrigin, BoundingPlane, DivideCurve (frames), EvaluateCurve | ToPlane, Section, DeconstructPlane, BuildSketch | position **+** orientation |
| `vector` | `Vector` (a point) | ConstructPoint, Vector, CurveEndpoints, DivideSurface, PopulateGeometry, DeconstructPlane | primitive `origin`, Move.offset, curve points, Voronoi | a point; lists scatter |
| `selection` | `ShapeList` of sub-shapes (edges/faces/vertices) | SelectEdge/Face/Vertex | Chamfer/Fillet/ExtrudeSelected* | also castable to points (origins) |
| `data` | **anything**: `int`, `float`, `bool`, `str`, `list`, domain `[min,max]` | inputs, math, list/domain ops, Panel, CodeBlock | every params-as-input socket, math, lists | the universal bus (overloaded) |
| `tree` | — | **nothing** | **nothing** | declared, **dead** (no node uses it) |

### What `data` is really hiding (sub-types we don't name)

`data` conflates at least: **float**, **integer**, **boolean**, **text**,
**list**, and **domain** (`[min, max]`). The user's request for *container nodes
per type* (`int`, `float`, `point`, `curve`, `surface`, `brep`…) is exactly the
ask to give these a name and a colour.

---

## 2. The current compatibility matrix

`WIRE_COMPATIBLE` (output → set of input types it may feed). The inverse lives in
the frontend as `INPUT_ACCEPTS` and **must stay in sync** (CLAUDE.md §5).

| output ↓ \ accepted input | geometry | sketch | curve | plane | vector | selection | data | tree |
|---|---|---|---|---|---|---|---|---|
| geometry  | ✅ | | | ✅ | | | ✅ | |
| sketch    | ✅ | ✅ | | | | | ✅ | |
| curve     | | ✅ | ✅ | | | | ✅ | |
| plane     | ✅ | | | ✅ | | | ✅ | |
| vector    | | | | | ✅ | | ✅ | |
| selection | | | | | ✅ | ✅ | ✅ | |
| data      | ✅ | ✅ | ✅ | ✅ | ✅ | | ✅ | |
| tree      | | | | | | | ✅ | ✅ |

Implicit **coercions** baked into this table (the "casts" that already happen):
- `sketch → geometry` (a face is treated as geometry by transforms/booleans)
- `curve → sketch` (a closed curve auto-fills a face — added with the curve work)
- `geometry ↔ plane` (transforms treat a Plane like geometry; a transformed plane
  flows back into a plane input)
- `selection → vector` (picked vertices/edges/faces serve as point origins)
- `data → *` (the universal source; a list from List/Sort can flow into a
  geometry/vector/plane input — mismatches surface as a per-node runtime error)

These work but are **undocumented and ad-hoc** — there's no single place that
defines "to feed X you accept {…} and coerce with f()".

---

## 3. Incoherences to fix (evidence-backed)

| # | Incoherence | Evidence | Root cause |
|---|---|---|---|
| I1 | Select* reject curves/sketches | `_select_subshapes(polygon_curve,'vertex')` returns 6 verts, but input is `geometry` only | type gate too narrow |
| I2 | Transforms reject curves (and vectors) | `_move/_rotate/_scale(curve)` all return a Wire, but input is `geometry` only; `curve→geometry` not in compat | type gate + missing coercion |
| I3 | Point extraction is fragmented | 5 different nodes, 3 different output shapes (points vs frames) | no "explode to points" protocol |
| I4 | DivideCurve emits *frames*, not points | OUT `frames:plane`; need DeconstructPlane to get points | single-output design; discoverability |
| I5 | `data` is untyped | int/float/bool/str/list/domain share one wire | no sub-types / no legend |
| I6 | Vector vs ConstructPoint duplicate | both `x/y/z → vector`; only the output *name* differs | historical duplication |
| I7 | `tree` is dead | no node references it | declared prematurely |
| I8 | Coercions are implicit | scattered in helpers (`_face`, `_at`, `_move`) | no central cast layer |
| I9 | `selection→vector` is invisible | works, but no UI/legend hint that a pick is also points | underdocumented cast |

---

## 4. The proposed protocol (the standard)

### Decisions taken (2026-06-28)

- **Sub-typing**: do **(A) tag, don't split** now; the real split **(B)** comes
  with the transformer phase.
- **Type ids**: keep the current ids **for now**, rename later. Target renames:
  `geometry` → an **umbrella for all geometry** (the user: "geometry should
  comprise all geometries"); `sketch` is a **confusing name** (it's really a
  surface/face — clarify); `vertex`/point naming is fine to keep.
- **`curve` is itself a family**: line / polyline / spline (and arc). Treat it
  like `data` — one wire id now, with **subtypes** (`line|polyline|spline|arc`)
  for legend/colour/validation later. Same tag-not-split approach as 4d-A.
- **Start order**: **gate-widening first** (done — see §6), then cast registry.

### 4a. Canonical type set

Keep the geometric types; **name the scalars** so a wire is a legend. Proposed:

- **Geometric**: `solid` (rename of `geometry`/"brep"), `surface` (`sketch`),
  `curve`, `point` (`vector`), `plane`, `selection`.
- **Scalar** (split out of `data`): `number` (float), `integer`, `boolean`,
  `text`. *(Rename optional — we may keep `data` as the wire id and carry a
  sub-type tag to avoid a breaking rename; see 4d.)*
- **Aggregate**: `list` (typed list / "tree" later), `domain` (`[min,max]`).
- `data` stays as the **explicit "any" bus** (opt-in universal), not the default.

Every type gets: a stable **id**, a **colour** (`WIRE_COLORS`), a **legend label**,
and a **default container node** (§5).

### 4b. Consumer rule (one rule for every socket)

> A socket of type **T** accepts: **T**, every type with a **registered cast → T**,
> and **data** (any). The cast runs at the boundary, centrally.

This replaces hand-maintained `WIRE_COMPATIBLE`/`INPUT_ACCEPTS` with a **cast
registry**: `CASTS[(src, dst)] = fn`. Compatibility is "a cast exists"; the
matrix is *derived* from it (so the two tables can't drift). Examples already
implemented as helpers, to be centralised:

```
curve   → surface : _face            (fill closed wire)
surface → curve   : _outline         (boundary wire)
*shape  → point   : center()/vertices()  (explode)
plane   → point   : _plane_origin
selection → point : anchor points
any closed-1D/2D → solid : extrude/loft caller decides
```

### 4c. Explode / extract protocol (fixes I3/I4)

One consistent way to get sub-elements, regardless of source:
- **Deconstruct** family (or widen Select*): any `solid|surface|curve|selection`
  → its `points` / `curves` / `faces`. Curves included (runtime already supports).
- DivideCurve keeps `frames` (its job) but **also** offer points via the same
  Deconstruct (or a `points` companion) so users aren't forced through
  DeconstructPlane.

### 4d. Migration-safe sub-typing for `data` (fixes I5)

Two options, pick later:
- **(A) Tag, don't split**: keep wire id `data`, attach `subtype` metadata
  (`number|integer|boolean|text|list|domain`) used for colour/legend/validation
  only. Non-breaking; Panel and containers read the tag.
- **(B) Real split**: add `number/integer/boolean/text/list/domain` wire ids with
  casts up to `data`. Cleaner legend, but a breaking change to saved graphs +
  both tables. Likely do **A first**, **B** with the transformer work.

### 4e. Fan-out / list semantics (already good — codify it)

Keep the current rule (CLAUDE.md §5b) as part of the protocol: item-access fans
out (one run per item → list output); `list_access`/`multiple` consume the whole
list. Every new type must declare its access mode deliberately.

---

## 5. Container / legend nodes (one per type) — design only

A **Container** node per type that is, today, a **typed pass-through + legend**
(and *later* the caster/transformer). Behaviour:

- Input: one socket of its type (optional). Output: same type.
- When **wired**: displays the value (like Panel) and re-emits it unchanged →
  acts as a **labelled probe / legend chip** in the graph, colour = the type.
- When **unwired**: acts as a **source** with a type-appropriate widget
  (Number/Integer/Boolean/Text already exist as input nodes; Point via x/y/z).
- Later (transformer phase): a target-type dropdown turns it into a **cast** node
  (`curve→surface`, `surface→curve`, `*→point`, …) driven by the cast registry.

Proposed set (reuse what exists, fill the gaps):

| Container | Type | Status |
|---|---|---|
| Number | number | exists: NumberInput/NumberSlider (rebrand) |
| Integer | integer | exists: IntegerSlider |
| Boolean | boolean | exists: BooleanToggle |
| Text | text | exists: StringInput |
| Point | point | **new** (Vector/ConstructPoint merge → one) |
| Curve | curve | **new** container/probe |
| Surface | surface | **new** |
| Solid (Brep) | solid | **new** |
| Plane | plane | exists-ish: PlaneOrigin (rebrand) |
| Selection | selection | **new** probe |
| List | list | exists-ish: ListCreate (rebrand) |
| Domain | domain | exists: ConstructDomain (rebrand) |

`Panel` stays as the universal inspector; containers are the *typed* siblings.

---

## 6. Nodes to adapt (annotated)

Grouped by the incoherence they resolve. **No behaviour change to geometry** —
these only widen type gates / add casts / unify.

### Widen Select* to all explodable shapes (I1) — ✅ DONE
- `SelectVertex`, `SelectEdge`, `SelectFace`: input now `geometry` (+ `sketch`
  via compat) **+ `curve`** via the new per-socket `Socket.accepts`. Runtime
  (`_select_subshapes`) already handles `.vertices()/.edges()/.faces()` on a
  Wire/Face — gate-only change. (Note: a closed Circle curve has a single
  vertex = its seam; ties into the loft-seam work.)

### Widen transforms to curves (I2) — ✅ DONE (curve)
- `Move`, `Rotate`, `Scale`, `Mirror`, `ArrayLinear`: `shape` input now accepts
  **`curve`** (via `Socket.accepts=[curve]`); `sketch`/`plane` already accepted
  via compat. Runtime helpers already polymorphic (a moved/rotated curve stays a
  `Wire`).
  - **Type-preserving output — ✅ DONE.** `Move/Rotate/Scale` now carry
    `NodeDef.output_follows="shape"`: their first output mirrors the effective
    wire type flowing into `shape`, so a transformed curve stays a `curve` and can
    feed `curve`/`surface` inputs (e.g. Circle→Move→MakeFace / →DivideCurve).
    `Graph.effective_output_type()` resolves it up the chain (cycle-guarded) and is
    used in `validate()`; the editor mirrors it live (`refreshPolyType()` retypes
    the output socket on connect + propagates downstream). Verified precise:
    Box→Move→DivideCurve is still rejected (geometry ≠ curve). Mirror/ArrayLinear
    stay `geometry` (they aggregate into a compound/list).
  - Implementation of the gate: new **`Socket.accepts`** (extra OUTPUT types an
    input takes beyond `WIRE_COMPATIBLE`); checked in `Graph.validate()` and
    merged into the frontend's accepts string. Surgical — does **not** let a curve
    into booleans/fillet (verified: `curve→Union` still rejected).

### Unify point extraction (I3/I4)
- Add/rationalise a **Deconstruct** that takes `solid|surface|curve|selection`
  and yields `points` (+ `curves`/`faces`). 
- `DivideCurve`: keep `frames`; document/relabel that points come from
  Deconstruct(frames) or add a `points` route. `CurveEndpoints` becomes a thin
  alias of Deconstruct(curve).

### Type the scalar bus (I5) — see §4d
- `Panel`: read the `subtype` tag for a typed, coloured readout.
- Input nodes: tag their `data` output (`number/integer/boolean/text`).

### Remove duplication / dead types (I6/I7)
- Merge `Vector` + `ConstructPoint` into one **Point** node (keep an alias for
  old graphs).
- Drop `WIRE_TREE` from the tables until a tree feature lands (or wire it into
  `list`).

### Centralise coercions (I8/I9) — ✅ DONE
- `casts.py` (registry); `WIRE_COMPATIBLE` + frontend `INPUT_ACCEPTS` derived from
  it (manual mirror killed). The transpiler now **auto-applies** a boundary cast
  via `cast_helper` (`curve→sketch ⇒ _face`) on non-`raw` inputs; nodes that pick
  coercion by a flag (Extrude/Revolve/Sweep/Loft/Thicken/ToPlane) mark their
  profile socket `raw=True` so the transpiler leaves the value untouched.

---

## 7. Sequencing

1. **This doc** (agree the type set + protocol). ✅
3. **Gate widening** (Select* + transforms) — pure compatibility, geometry
   unchanged. Quick win, fixes the user's two concrete bugs. ✅ (`Socket.accepts`)
2. **Cast registry** (`casts.py`) + derive `WIRE_COMPATIBLE` and emit
   `INPUT_ACCEPTS` from it (kills table drift). No node changes yet. ✅
   - `cad_nodes/casts.py` owns the wire ids + `CASTS` registry + `wires_compatible`
     / `build_compatible()` / `build_input_accepts()` / `cast_helper()`.
   - `catalog.py` re-exports them; `WIRE_COMPATIBLE = build_compatible()` (derived,
     verified identical to the old hand-written table → zero behaviour change).
   - `GET /api/wiretypes` serves the derived `input_accepts`; `nodes.html` loads it
     at boot and drops the hand-maintained mirror to a fallback (fixed a real
     drift: `tree` input wrongly accepted `data`).
   - `CASTS` records the coercion helper per edge (`curve→sketch = _face`, …) for
     the **next** phase: apply casts automatically at the wire boundary + give
     transforms type-preserving output.
4. **Deconstruct/explode unification** (I3/I4).
5. **`data` sub-typing tag** (4d-A) + Panel/inputs read it; legend colours.
6. **Container nodes** per type (§5), reusing existing source nodes.
7. **Transformer phase** (deferred): containers gain a target-type cast dropdown;
   optionally the real `data` split (4d-B).

> Working discipline stays as in PLAN_PARAMETRIC_CURVES.md: smallest piece first,
> verify headless, keep both wire tables in sync (or better: generate one from the
> other), one demo per step.
