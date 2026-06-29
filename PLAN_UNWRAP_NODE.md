# Task — an `Unwrap` node (flatten a 3D surface into a 2D pattern)

Status: **NOT STARTED** — task brief for a future session. Picks up after the data
protocol work (see PLAN_DATA_PROTOCOL.md) and the richer Loft node landed.

## Goal

Add a node that takes a 3D surface (or a solid's face) and produces a **flat 2D
representation** — the kind of thing you print, lay out, or laser-cut. Two distinct
meanings of "unwrap" exist; decide which (or both) up front (see §3).

## What build123d / OCC actually expose (verified on 0.11.0)

There is **no native developable-surface unroll** in OCC. The available primitives:

- **UV parametrization** — `Face.position_at(u, v)`, `Face.normal_at`, `Face.uv_face`,
  `Face._uv_bounds` (the (u0,u1,v0,v1) domain). `DivideSurface` already uses
  `position_at` over a UV grid (`_divide_surface` in transpiler.py) — reuse that
  machinery.
- **Projection** — `project`, `Shape.project_faces`, `Face.project_to_shape`,
  `Face.project_to_viewport` (HLR — produces visible/hidden 2D edges),
  `project_workplane`, and `GeomAPI_ProjectPointOnSurf` for point→surface.
- `BRepOffsetAPI_NormalProjection` (project edges onto a surface), `MiddlePath`.

## 3. Design options (pick in the next session — or offer as a `mode`)

| Mode | Meaning | Feasibility | build123d tool |
|---|---|---|---|
| **A. UV unwrap** | Map the face (its boundary + any curves on it) from 3D into its 2D UV domain → a flat patch in the UV rectangle. Distorts non-rectangular params but always works. | **Easy** — reuse `position_at`/`_uv_bounds`. | `Face.position_at`, `uv_face` |
| **B. Developable unroll** | True arc-length unroll of a developable surface (cylinder/cone/extrusion of a curve) into a flat strip — preserves lengths. The "sheet-metal / papercraft" unroll. | **Hard, general** — but **tractable for cylinders/cones/ruled extrusions** by walking arc-length along U and keeping V (the ruling) straight. | custom (arc-length along iso-curves) |
| **C. Planar projection** | Project the shape onto a plane (drop a dimension). Fast, lossy (overlaps for curved shells). | **Easy** | `project_to_viewport` / `project` |

Recommended: ship **A (UV unwrap)** first as the smallest correct piece — it composes
with the existing UV machinery and the filter/transform containers — then add **B**
(developable unroll) for the cylinder/cone/extrusion case behind a `mode` select.
**C** is essentially the existing projection helpers; expose only if asked.

## 4. Proposed node shape (follows the data protocol)

```
Unwrap  (category: operations)
  inputs:  surface   : WIRE_SURFACE (accepts WIRE_SOLID — takes its faces, like DivideSurface)
  params:  mode      : select [uv | developable | project]   (start: uv only)
           plane?    : for `project` mode (target plane); default XY
  outputs: pattern   : WIRE_CURVE   (the flattened boundary/edges as 2D wires; a list -> fans out)
```

- Output is **curve** (the flat outline/edges), so it feeds Make Face → cut/laser,
  Dimension, or a Surface container. A multi-face input fans out (one pattern per face).
- Runtime helper `_unwrap(shape, mode, ...)` in the transpiler PREAMBLE; add `Unwrap`
  to `_LIST_PRODUCERS` (it explodes a shape into per-face patterns).
- Wire types use the **renamed ids** (`solid`/`surface`) — see PLAN_DATA_PROTOCOL.md §4a.
- Consider laying multiple patterns out side-by-side (a nesting offset per face) so they
  don't overlap at the origin — or leave each at its own UV origin and let the user Move.

## 5. Open questions

1. **A vs B vs all-three-as-a-mode** — which semantics does the user want? ("unwrap"
   most often means **B**, the developable unroll, for fabrication.)
2. For **B**, scope to **cylinder/cone/planar-ruled** first (detect via `Face.geom_type`)
   and error clearly on a non-developable face, or attempt a triangulated approximate
   flatten?
3. Output: just the **boundary** wire, or **all** edges/curves lying on the face
   (holes, engraved curves)? Probably all, via `Face.edges()` mapped through the unwrap.
4. Should it emit a **surface** (filled flat face) as well as the curve? A `solid`/fill
   toggle like Loft, or leave filling to a downstream Make Face / Surface[transform].

## 6. Acceptance criteria

- Headless: a cylinder side face → **developable unroll** is a rectangle whose width ≈
  `2πr` and height = the cylinder height (verify arc-length preservation).
- A planar face → unwrap is congruent to the original (area preserved).
- UV mode on any face returns a closed 2D wire in the UV domain; fans out over a
  multi-face input.
- 41/41 existing tests still pass; new node serialized in `/api/nodes`; verify in the
  editor after a hard-refresh.

> Working discipline (per PLAN_PARAMETRIC_CURVES.md): smallest piece first (UV mode),
> verify headless with `.venv-b123d`, one demo graph per step, keep both wire tables in
> sync (they derive from casts.py now).
