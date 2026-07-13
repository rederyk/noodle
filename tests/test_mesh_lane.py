"""
The mesh lane (PLAN_MESH_LANE.md) — wire types, casts and transpiler emission.

Pure-Python: no build123d, no trimesh. These assert the *shape* of the lane (what
may connect to what, and what code comes out), not the geometry — the actual mesh
ops run in the worker and are exercised end-to-end there.
"""

import pytest

from cad_nodes import catalog
from cad_nodes.casts import WIRE_MESH, WIRE_SOLID, WIRE_SURFACE, wires_compatible
from cad_nodes.graph import Graph, ValidationError
from cad_nodes.transpiler import transpile


def _g(nodes, connections):
    return Graph.from_dict({"name": "t", "nodes": nodes, "connections": connections})


# The transpiled script is PREAMBLE + body, and the PREAMBLE *defines* the very
# helpers we assert on — so a bare `"_to_mesh(" in code` would match the def and
# pass for the wrong reason. Every helper is called on a `__out_N` variable, so
# anchoring on that is what actually proves it was emitted at a call site.
def _calls(code: str, helper: str) -> bool:
    return f"{helper}(__out_" in code


# --- the cast asymmetry ---------------------------------------------------
# B-Rep tessellates INTO the mesh lane freely (milliseconds, safely lossy). The
# reverse is NOT a cast: rebuilding a B-Rep from triangles costs ~300s on a real
# part, so it must stay an explicit, guarded node — never an implicit coercion.
def test_solid_and_surface_cast_into_the_mesh_lane():
    assert wires_compatible(WIRE_SOLID, WIRE_MESH)
    assert wires_compatible(WIRE_SURFACE, WIRE_MESH)
    assert catalog.cast_helper(WIRE_SOLID, WIRE_MESH) == "_to_mesh"


def test_mesh_never_casts_back_into_the_brep_lane():
    assert not wires_compatible(WIRE_MESH, WIRE_SOLID)
    assert not wires_compatible(WIRE_MESH, WIRE_SURFACE)


def test_mesh_reaches_the_universal_data_bus():
    assert wires_compatible(WIRE_MESH, catalog.WIRE_DATA)


def test_wiring_a_mesh_into_a_solid_input_is_rejected():
    g = _g(
        [{"id": "m", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "e", "type": "ExportSTEP", "params": {}}],
        [{"id": "c", "from_node": "m", "from_socket": "result",
          "to_node": "e", "to_socket": "shape"}],
    )
    with pytest.raises(ValidationError):
        g.validate()


# --- transpiler emission --------------------------------------------------
def test_mesh_chain_emits_the_mesh_helpers():
    g = _g(
        [{"id": "i", "type": "ImportMesh", "params": {"path": "part.stl"}},
         {"id": "f", "type": "MeshFix", "params": {"min_body": 16, "fill_holes": True}},
         {"id": "x", "type": "ExportMesh", "params": {"path": "out.stl"}}],
        [{"id": "c1", "from_node": "i", "from_socket": "result",
          "to_node": "f", "to_socket": "mesh"},
         {"id": "c2", "from_node": "f", "from_socket": "result",
          "to_node": "x", "to_socket": "mesh"}],
    )
    g.validate()
    code = transpile(g)
    assert "_mesh_load('part.stl')" in code
    assert _calls(code, "_mesh_fix")
    assert _calls(code, "_mesh_export")


def test_a_solid_wired_into_a_mesh_input_is_tessellated_at_the_boundary():
    """The whole point of the solid->mesh cast: drop a Box straight into a mesh
    node and the transpiler inserts _to_mesh for you."""
    g = _g(
        [{"id": "b", "type": "Box", "params": {}},
         {"id": "f", "type": "MeshFix", "params": {}}],
        [{"id": "c", "from_node": "b", "from_socket": "result",
          "to_node": "f", "to_socket": "mesh"}],
    )
    g.validate()
    code = transpile(g)
    assert _calls(code, "_to_mesh")


# --- transforms are NOT duplicated for meshes -----------------------------
# The crux of the design: a Move/Rotate/Scale/Mirror on triangles is the same
# operation as on a B-Rep (a 4x4 on the vertices instead of a Location), so the
# mesh lane reuses the existing nodes rather than growing a MeshMove twin.
@pytest.mark.parametrize("ntype", ["Move", "Rotate", "Scale", "Mirror"])
def test_transforms_accept_a_mesh(ntype):
    sock = catalog.get(ntype).input("shape")
    assert WIRE_MESH in sock.accepts


@pytest.mark.parametrize("ntype", ["Move", "Rotate", "Scale", "Mirror"])
def test_no_duplicate_mesh_transform_node_exists(ntype):
    assert f"Mesh{ntype}" not in catalog.REGISTRY


def test_a_moved_mesh_stays_a_mesh():
    """output_follows='shape' must carry the mesh type through the transform, or
    the result could not feed another mesh node downstream."""
    g = _g(
        [{"id": "i", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "m", "type": "Move", "params": {"x": 5}},
         {"id": "f", "type": "MeshFix", "params": {}}],
        [{"id": "c1", "from_node": "i", "from_socket": "result",
          "to_node": "m", "to_socket": "shape"},
         {"id": "c2", "from_node": "m", "from_socket": "result",
          "to_node": "f", "to_socket": "mesh"}],
    )
    g.validate()   # would raise if Move's output degraded to a plain solid
    assert g.effective_output_type("m", "result") == WIRE_MESH


def test_moving_a_mesh_does_not_tessellate_it_again():
    """A mesh flowing through Move must NOT pick up a _to_mesh cast — it is
    already triangles, and re-casting would be a silent round-trip."""
    g = _g(
        [{"id": "i", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "m", "type": "Move", "params": {"x": 5}}],
        [{"id": "c", "from_node": "i", "from_socket": "result",
          "to_node": "m", "to_socket": "shape"}],
    )
    code = transpile(g)
    g.validate()
    assert _calls(code, "_move")
    assert not _calls(code, "_to_mesh")


# --- phase 2: booleans, simplify, and the guarded bridge back -------------
def test_mesh_booleans_emit_the_manifold_engine():
    g = _g(
        [{"id": "i", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "b", "type": "Box", "params": {}},
         {"id": "s", "type": "MeshSubtract", "params": {}}],
        [{"id": "c1", "from_node": "i", "from_socket": "result",
          "to_node": "s", "to_socket": "a"},
         {"id": "c2", "from_node": "b", "from_socket": "result",
          "to_node": "s", "to_socket": "b"}],
    )
    g.validate()
    assert "_mesh_bool('subtract'" in transpile(g)


def test_a_solid_cutter_is_tessellated_at_an_item_access_boundary():
    """MeshIntersect's `b` is item-access, so the transpiler inserts the cast."""
    g = _g(
        [{"id": "i", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "b", "type": "Box", "params": {}},
         {"id": "x", "type": "MeshIntersect", "params": {}}],
        [{"id": "c1", "from_node": "i", "from_socket": "result",
          "to_node": "x", "to_socket": "a"},
         {"id": "c2", "from_node": "b", "from_socket": "result",
          "to_node": "x", "to_socket": "b"}],
    )
    g.validate()
    assert _calls(transpile(g), "_to_mesh")


def test_collectors_coerce_at_runtime_not_at_the_wire():
    """Transpiler._cast only fires on the item-access branch — `multiple` and
    `list_access` sockets deliberately skip it (the B-Rep `_union` copes the same
    way). So a solid reaches MeshUnion RAW, and _mesh_bool must call _as_mesh on
    every item itself. If that ever regresses, a Box into MeshUnion breaks."""
    g = _g(
        [{"id": "b", "type": "Box", "params": {}},
         {"id": "u", "type": "MeshUnion", "params": {}}],
        [{"id": "c", "from_node": "b", "from_socket": "result",
          "to_node": "u", "to_socket": "shapes"}],
    )
    g.validate()
    code = transpile(g)
    assert "_mesh_bool('union', __out_" in code   # the solid arrives uncast…
    assert not _calls(code, "_to_mesh")
    assert "_as_mesh(i) for i in _flatten" in code   # …and _mesh_bool coerces it


def test_mesh_boolean_output_stays_on_the_mesh_lane():
    for ntype in ("MeshUnion", "MeshSubtract", "MeshIntersect", "MeshSimplify"):
        assert catalog.get(ntype).outputs[0].wire_type == WIRE_MESH


def test_mesh_to_solid_is_the_only_way_back_to_the_brep_lane():
    """It returns a solid — but it must remain a NODE, never a cast, or a mesh
    wired into a Fillet would silently sew for five minutes."""
    assert catalog.get("MeshToSolid").outputs[0].wire_type == WIRE_SOLID
    assert catalog.cast_helper(WIRE_MESH, WIRE_SOLID) is None
    assert not wires_compatible(WIRE_MESH, WIRE_SOLID)


def test_mesh_to_solid_carries_a_triangle_guard():
    """The guard is the whole point: 147k triangles is ~300s of sewing."""
    p = catalog.get("MeshToSolid").param("max_tris")
    assert p is not None and p.default == 20000


def test_mesh_to_solid_output_feeds_the_brep_lane():
    g = _g(
        [{"id": "i", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "t", "type": "MeshToSolid", "params": {"max_tris": 20000}},
         {"id": "e", "type": "ExportSTEP", "params": {}}],
        [{"id": "c1", "from_node": "i", "from_socket": "result",
          "to_node": "t", "to_socket": "mesh"},
         {"id": "c2", "from_node": "t", "from_socket": "result",
          "to_node": "e", "to_socket": "shape"}],
    )
    g.validate()   # a mesh reaches STEP only through this node
    assert _calls(transpile(g), "_mesh_to_solid")


def test_manifold_output_is_not_reprocessed_by_trimesh():
    """The subtlest bug of phase 2, pinned. trimesh.Trimesh(...) defaults to
    process=True, which re-merges the vertices of manifold3d's ALREADY-welded
    output and silently breaks the manifold: simplify(1.5mm) came back
    non-watertight with process=True and watertight with process=False, from the
    identical triangles. If this regresses, meshes corrupt with no error."""
    from cad_nodes.transpiler import PREAMBLE
    body = PREAMBLE.split("def _from_manifold")[1].split("\ndef ")[0]
    assert "process=False" in body


def test_mesh_simplify_verifies_its_own_result():
    """A tolerance above the part's wall thickness tears it apart while the
    triangle count CLIMBS. The node must check the outcome, not trust the setting."""
    from cad_nodes.transpiler import PREAMBLE
    body = PREAMBLE.split("def _mesh_simplify")[1].split("\ndef ")[0]
    assert "decompose()" in body          # piece count: did it tear the part?
    assert "_max_error" in body           # volume drift bound
    assert "raise ValueError" in body
    assert catalog.get("MeshSimplify").param("max_error").default == 5.0
