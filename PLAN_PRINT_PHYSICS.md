# PLAN — print physics

Four nodes that answer the only question a slicer never asks you: **which way up, and
why**. They live in the catalog category `print` (`catalog.py` §12c), they run on the
mesh lane (`PLAN_MESH_LANE.md`), and the runtime is in the transpiler PREAMBLE.

---

## 1. The physics, in one paragraph

A printed part is **anisotropic**. Within a layer the plastic is continuous; between two
layers it is *glued*, and that bond is worth roughly a third to two thirds of the real
material (it depends on polymer, nozzle and chamber temperature, layer height and line
width — treat the ratio as a fact of life, not as a number to quote). So a part fails at
a **layer line**, and the layer line it fails at is the one with the least glued area.

Two consequences, and every node here is one of them:

- **Where it breaks** is the smallest cross-section perpendicular to Z. Call it the *weak
  plane*. It is not a property of the part — it is a property of the part **in this
  orientation**, and turning the part moves it.
- **Which way to turn it** cannot be answered without knowing what the part is *for*.
  Given the direction the part will be pulled, the rule is: keep that load **in** the
  layer plane, never across it.

These are heuristics, not an FEA. They catch the dominant failure mode and say nothing
about a stress riser around a hole. Use them to rule out the stupid orientation, not to
certify the clever one.

## 2. The nodes

| node | what it does |
|---|---|
| `PlaceOnBed` | lowest point → z=0, optionally centred in XY. Serves **both lanes** (measures on the mesh, moves the original), so a solid stays a solid |
| `PrintCheck` | text report → a Panel: height and layers, bed contact, overhang area past the critical angle, and the **weak plane** (area + height) |
| `OverhangFaces` | the faces that need support, as a mesh of its own — so the viewer gives them their own colour and you *see* them on the part |
| `OrientForPrint` | tries every stable resting pose, scores it, returns the winner already on the bed + a `report` table of the top five |

`OrientForPrint` has **two outputs** from one search (`_emit_orient`, modelled on
`_emit_center`): scoring the poses means slicing each of them, and doing it twice because
a Panel happens to be wired in would be daft.

## 3. How each number is got

- **Overhang** — a face needs support if its normal points down more steeply than the
  critical angle (45° by default). **The faces resting ON the bed are excluded**: a flat
  base points straight down too, and counting it as an overhang is the classic way to get
  this backwards — it makes the one orientation that needs *no* support at all look like
  the worst option on the list. (Measured on the example bracket: 1150 mm² of "overhang"
  that is simply the plate, sitting on the glass.)
- **Support cost** — not the overhang *area* but `Σ area × height above the bed`: what
  support costs is how far it has to reach down.
- **The weak plane** — `manifold3d`'s `slice(z).area()`, 48 heights up the part. ~0.01 s
  for 80 sections, so scoring a hundred candidate poses is free. A non-watertight mesh has
  no honest cross-section: the report says so and points at `MeshFix` rather than
  inventing a number.
- **Stable poses** — every face of the **convex hull** whose polygon contains the centre
  of mass, projected down. That is what "stable" means: let go of the part and it stays.
  (trimesh ships `compute_stable_poses`, but it needs `networkx` and `shapely`, which are
  not in the image. The convex hull is `scipy`, which is.) Cluster the hull's faces by
  normal with a **tolerance**, not by a rounded key: one flat side of a part is many
  triangles whose normals agree to 6 decimals and not to 3, and a rounded key splits them
  into two identical "poses" that then both appear in the report.
- **Strength** — with a `load` vector wired in, it is `|load · Z|` after the rotation:
  0 means the load lies in the layer plane, 1 means it hits the layer bond head-on. With
  no load declared, the proxy is the largest weak plane (maximise the smallest glued
  section).

## 4. What the example shows (`examples/print-orientation.json`)

A bracket: a plate, a stem, a hole. Two copies, side by side.

| | as modelled | as oriented (load along the stem) |
|---|---|---|
| support | **0 mm²** | 384 mm² |
| bed contact | 1150 mm² | 120 mm² |
| height | 49 mm | 40 mm |
| weak plane | **64 mm²** at the stem root | 88 mm² |
| strength (load across the layers) | **1.00** — head-on | **0.00** |

Printed as modelled it needs **not one support** — and it is standing in the one
orientation that puts its job at right angles to its strength. `OrientForPrint` lays it
down, paying 384 mm² of support and nine tenths of the bed contact, and it is right to:
supports are money, a snapped bracket is a bracket.

**Then unwire the `load`** and it puts the part back upright. Nothing broke — you asked a
different question and got an honest answer to it. That is the lesson worth keeping: an
optimiser will hand you the weakest possible part if you never tell it what the part is
FOR.

## 5. Not built yet

- **Support volume proper** — the real thing is the swept volume under the overhangs down
  to the bed or to the part below, not `area × height`. Needs a projection + boolean per
  candidate; affordable (manifold does it in ms), just not done.
- **Bridges** — an overhang spanning two supported walls does not need support at all. The
  face test cannot see that; a per-layer island analysis can.
- **Print time** — height is a proxy. The honest number is `Σ layer perimeter + infill`,
  which is a slicer, and noodle is not one.
- **A real load case** — one vector is one load. A bracket in bending wants a moment, not
  a force. The step after that is an FEA, and the step after *that* is admitting the layer
  bond needs a material model per printer.
