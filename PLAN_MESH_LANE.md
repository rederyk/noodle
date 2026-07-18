# PLAN — the mesh lane

A second geometry lane alongside the B-Rep one: a `mesh` wire type, a `Mesh*` node
suite, and a real mesh engine (**trimesh** + **manifold3d**) — because build123d
cannot do this, and forcing it to is the wrong shape.

---

## 0. The finding that motivates this

**build123d does not support meshes as a modelling domain.** It treats a mesh as an
I/O *format*. Measured in the `noodle` container (build123d 0.11.0, OCP 7.9.3) on
`projects/raccordo/assets/obj-…-ve.stl` — a real part, 147,092 triangles:

| | via build123d / OCCT | via trimesh + manifold3d |
|---|---|---|
| open the STL | **300s** (`Mesher.read` sews every triangle into a planar B-Rep face) | **0.16s** |
| make it usable | *no concept of it* | **0.31s** → watertight |
| one boolean | **81s** | **0.107s** |
| simplify to ~35k tris | *does not exist* | 0.07s, **−0.10%** volume |
| isotropic remesh | *does not exist* | see §5 (licensing) |

The two doors build123d offers, and why neither is mesh support:

- `import_stl()` → a `Face` carrying only a `Poly_Triangulation`, **no underlying
  surface** (`BRep_Tool.Surface_s → None`). `is_valid=False`, `volume=0.0`, and a
  boolean is refused by the API itself: *"Only shapes with the same dimension can be
  added"* — it is a 2D face, not a solid. This is what `ImportSTL` uses today.
- `Mesher().read()` → sews each triangle into a planar B-Rep face, yielding a valid
  `Solid` with the correct volume, on which booleans *do* work. But that is
  **B-Rep-ification, not mesh modelling**: 300s to open, 81s per boolean, and the
  result is a 147,010-face solid that OCCT chokes on.

And nothing in OCCT does remesh/decimate/repair: `BRepMesh_IncrementalMesh` is a
*tessellator* for B-Rep (it needs a surface — it runs the other way); `ShapeFix_*`
repairs **B-Rep topology**, not mesh defects; `ShapeUpgrade_UnifySameDomain` merged
0.9% of faces (147,092 → 145,757) in 26s — not a decimator; `Mesher` is I/O only.

The clincher: the raccordo STL is **not watertight**, and its actual defect is *one
stray triangle* plus a duplicate face (0 boundary edges, 1 non-manifold edge, 2
bodies — the main body is watertight with euler −2, i.e. genus 2, entirely legitimate
for a fitting with two through-holes). OCCT reported `is_valid=True` on it, because it
was validating the B-Rep topology it had just built — it lacks the vocabulary to even
*notice* the problem, let alone fix it.

---

## 1. The wire type

`WIRE_MESH = "mesh"` in `cad_nodes/casts.py` (§5 of CLAUDE.md — the one place wire
compatibility is defined; both `WIRE_COMPATIBLE` and the frontend's `INPUT_ACCEPTS`
derive from it, so adding it there is the whole backend+frontend change).

Casts — and the asymmetry is the point:

```python
CASTS = {
    ...
    (WIRE_SOLID,   WIRE_MESH): "_to_mesh",   # tessellate: cheap, lossy, safe
    (WIRE_SURFACE, WIRE_MESH): "_to_mesh",
    # (WIRE_MESH, WIRE_SOLID) is DELIBERATELY ABSENT — see below
}
```

B-Rep flows **into** the mesh lane freely and automatically: drop a `Box` straight into
a `MeshSubtract` and it tessellates at the wire boundary. That direction is milliseconds
and cannot surprise anyone.

The reverse is **not a cast**. `mesh → solid` costs 300s on a real part, so it must be
an explicit, guarded node (`MeshToSolid`, §4), never an implicit boundary coercion —
otherwise a user wires a mesh into a `Fillet` and the app hangs for five minutes with
no explanation. The asymmetry mirrors reality: tessellation is cheap and safely lossy;
reconstruction is expensive, lossy *and* dangerous.

`mesh → data` comes free (the universal bus already accepts any output into a `data` input).

---

## 2. The runtime value

A thin wrapper in the transpiler PREAMBLE:

```python
class Mesh:
    """A triangle mesh flowing on a `mesh` wire. Wraps trimesh.Trimesh so the
    engine stays swappable and so _move/_scale/_rotate and the preview extractor
    can dispatch on a named type rather than on a third-party class."""
    __slots__ = ("tm",)
```

Keeping trimesh behind a wrapper means the engine is an implementation detail, and the
dispatch sites below are explicit rather than duck-typed.

---

## 3. Transform compatibility — Move/Scale/Rotate stay ONE node each

This is the hard requirement, and it is already 90% built.

`_move`, `_rotate`, `_scale`, `_mirror` in the PREAMBLE are **already polymorphic** —
they each handle a `Shape` *or* a `Plane` (`_rotate`'s docstring says so outright: it
uses Location algebra "so it is polymorphic: a plane rotates just like a solid"). A
transform on a mesh is a 4×4 matrix on the vertex array — the same operation on a
different representation. So each helper gains **one branch**:

```python
def _rotate(_obj, _axis, _angle):
    if _obj is None: return None
    if isinstance(_obj, Mesh):
        return _obj.transformed(_rot_matrix(_axis, _angle))   # ~1ms on 147k verts
    d = _axis.direction
    return Rot(d.X * _angle, d.Y * _angle, d.Z * _angle) * _obj
```

And in `catalog.py` the transform nodes' `shape` socket gains `accepts=[WIRE_MESH]`.
Their existing `output_follows="shape"` already carries the input's wire type to the
output, so a mesh in → a mesh out, with the wire correctly coloured.

Net: **Move, Rotate, Scale, Mirror, ArrayLinear, ArrayPolar work on meshes with zero
new nodes** — one `accepts` entry and one `isinstance` branch each. No `MeshMove`.

---

## 4. The node suite (category `mesh`, prefix `Mesh*`)

**Import / convert**
- `ImportMesh` — STL/OBJ/PLY/3MF → `mesh`. (Keep `ImportSTL` as-is for back-compat and
  reference geometry; its docstring already admits it is "limited for solid ops".)
- `ToMesh` — solid → mesh, with a deflection param. The explicit form of the auto-cast.
- `MeshToSolid` — the sewing route, **guarded** by a `max_tris` param (default ~20k):
  above it the node refuses with a message pointing at `MeshSimplify`, because 147k
  triangles is 300 seconds. This is the only bridge back to the B-Rep lane.

**Booleans** (manifold3d) — mirror the shape of the existing B-Rep boolean nodes so the
two lanes read the same:
- `MeshUnion` (a `multiple` collector, like `Union`), `MeshSubtract` (`a`; `b` list-access),
  `MeshIntersect`.
- Inputs must be watertight. manifold3d's own error is unhelpful (`"Not all meshes are
  volumes!"`) — the node wraps it and says *"input is not watertight — insert a MeshFix"*.

**Repair / inspect** — the half that would have saved the raccordo:
- `MeshFix` — merge vertices, drop duplicate/degenerate faces, drop stray shards (below a
  face-count/volume floor), fix normals, fill holes. 0.31s on 147k. Second output: a
  `data` report of what it actually did (watertight before/after, shards dropped, holes
  filled) — so the fix is *legible*, not magic.
- `MeshInspect` — no-op on geometry, emits `data`: watertight, euler/genus, body count,
  boundary loops, non-manifold edges, volume, area. The mesh-lane sibling of the
  `slice_summary` perception tools in the retroeng flow (§7b of CLAUDE.md).

**Simplify / refine**
- `MeshSimplify` — manifold3d's native `simplify(tolerance)`: **0.07s, 147k → 35k tris,
  −0.10% volume, stays watertight.** Tolerance-driven (a bounded geometric deviation in
  mm) rather than count-driven, which is the correct CAD semantic — you say how much
  error you accept, not how many triangles you want. A `target_tris` mode can bisect on
  tolerance if wanted.
- `MeshRefine` — `subdivide_to_size(max_edge)`.

**Cheap wins, same engines**
- `MeshHull` (convex hull), `MeshSmooth` (laplacian), `MeshSplit` (→ a *list* of bodies,
  which fans out through the existing list machinery — §5b of CLAUDE.md — for free),
  `ExportMesh` (STL/OBJ/PLY/3MF).

---

## 5. Licensing — the constraint that shapes the suite

noodle is **MIT**, and `THIRD_PARTY_NOTICES.md` takes this seriously (it carries a full
LGPL-compliance note for OCCT). So the engine choice is a licensing decision, not just a
technical one.

| library | role | license | verdict |
|---|---|---|---|
| **manifold3d** | booleans, `simplify` | **Apache-2.0** | ✅ ship it |
| **trimesh** | I/O, repair, transforms, split, hull | **MIT** | ✅ ship it |
| pymeshlab | isotropic remesh, quadric decimate | **GPL-3.0** | ❌ cannot be a Python import in the default image |
| fast_simplification | decimate | MIT | ❌ **tested, rejected**: never reaches the target count (stops at 104k of a requested 5k) and with `agg=9` it *destroys* the mesh — −14.69% volume, no longer watertight |
| pyacvd | isotropic remesh | MIT | ❌ **tested, rejected**: −8.8% volume, no longer watertight; also drags in VTK, which this image deliberately excludes (`cadquery-ocp-novtk`) |

So: **boolean, fix, and simplify are fully covered by MIT/Apache libraries** and go in
`requirements.txt` + `THIRD_PARTY_NOTICES.md`.

**True isotropic remesh is the one gap.** The only tool that does it correctly on a real
part is pymeshlab (4.64s, −0.56%, watertight) — and it is GPL-3. Both MIT alternatives
break the geometry. Two honest options:

1. **Don't ship it (recommended for v1).** For CAD and 3D-printing work, `MeshSimplify`'s
   bounded-deviation simplification is what people actually reach for; isotropic remesh
   matters for simulation and sculpting, neither of which noodle does yet.
2. **Ship it as a separate process.** `THIRD_PARTY_NOTICES.md` already blesses exactly
   this pattern — components used "as a **separate process** invoked over a CLI" do not
   affect noodle's MIT license. A `MeshRemesh` node shelling out to an optional,
   user-installed MeshLab binary is license-safe and keeps the default image clean.

Note pymeshlab also needs system GL libraries (`libgl1`, `libopengl0`, `libegl1`) that
the current image lacks — a second, independent reason to keep it out of the default build.

---

## 6. Preview & viewport

`mesh_extractor.extract_view` currently calls `shape.tessellate(...)`. A `Mesh` needs a
branch that simply hands over its vertices/faces — **it is already triangles.** The mesh
lane therefore becomes the *cheapest* thing in the app to preview: no meshing step at
all. Give the `mesh` wire its own colour in `WIRE_COLORS` (`nodes.html`) so the two lanes
are visually distinct in the graph.

## 7. Memo cache

Mesh nodes are deterministic and hash like any other (params + code + upstream keys), so
they cache for free. `ImportMesh` is an `Import*` and under the current rule poisons its
lineage as non-deterministic — worth revisiting by hashing the asset file, since a
`MeshFix` + `MeshSimplify` chain is the kind of thing you do *once* and then want cached
behind every slider drag.

## 8. Rollout

- **Phase 1 — perceive & repair. DONE.** Wire type + `Mesh` wrapper + transform
  polymorphism (§3) + `ImportMesh`, `ToMesh`, `MeshFix`, `MeshInspect`, `ExportMesh`
  + the preview branch.
- **Phase 2 — operate. DONE.** `MeshUnion`/`MeshSubtract`/`MeshIntersect`,
  `MeshSimplify`, and the guarded `MeshToSolid` (§9).
- **Phase 3 — extras.** Hull, smooth, split, refine; arrays/Align on meshes (their
  templates still build `Pos(…) * shape` inline, so they need helpers first); decide
  the remesh question (§5).

---

## 9. What phase 2 cost us to learn

Three things only showed up once the nodes ran on the real part. All three are now
encoded in the code, not just in this document.

**`trimesh.Trimesh(...)` defaults to `process=True`, and that silently breaks a
manifold.** manifold3d returns an already-welded, valid manifold; trimesh's default
processing re-merges its vertices and can tear it. Measured: `simplify(1.5mm)` came
back **non-watertight** through `process=True` and **watertight** through
`process=False` — identical triangles. `_from_manifold` now passes `process=False`,
and says why.

**A tolerance at or above the part's feature size does not simplify a part, it
destroys it — quietly.** The raccordo is a thin-walled shell: mean wall =
volume/area = **1.11mm**. `simplify` tracks that almost exactly:

| tolerance | triangles | bodies | volume |
|---|---|---|---|
| 0.05mm | 35,026 | 1 | −0.1% |
| 0.5mm | 34,482 | 1 | −0.2% |
| 1.0mm | 46,314 | 1 | +0.1% |
| **1.2mm** | 81,580 | **2 — torn** | −5.8% |
| **1.5mm** | 97,180 | **3 — torn** | −11.7% |
| **3.0mm** | 112,180 | 1 | **−22.5%** |

Note the triangle count is **not monotonic** in the tolerance — past the wall
thickness it climbs. You cannot move a surface further than the wall is thick and
still have a wall. So `MeshSimplify` **verifies its own result** instead of trusting
the setting: it fails loudly if the volume moved past `max error` (default 5%) or if
the part came out in more pieces than it went in as. `Manifold.decompose()` makes the
piece count essentially free (~0.02s).

**`MeshToSolid` works, and confirms why it needed a guard.** Sewing the 35k-triangle
simplified raccordo produced a valid `Solid` (35,426 faces, volume exact to the mesh)
— in **54s**, and `ExportSTEP` then wrote a **92 MB** file. It is a *faceted* solid,
not a reconstructed CAD model. It is the right tool for handing a mesh to a B-Rep
operation, and the wrong tool for "turn my STL into a real CAD part" — that is what
the retroeng flow (PLAN_RETROENG.md) is for.

**Testing note:** `tests/` are pure-Python by design (no build123d). trimesh and
manifold3d import without OCCT, so the mesh lane's actual geometry ops can be unit-tested
in CI — the mesh lane is *more* testable than the B-Rep one, not less.
