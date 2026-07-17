"""
True 3D Voronoi + universal Populate — wire shape and transpiler emission.

Pure-Python: no build123d, no scipy. These pin the *contract* — what may wire
into Populate's region, that the raw socket really suppresses boundary casts
(the helper dispatches on the runtime type, so a cast would break it), and what
code comes out of the flagship lattice chain. The geometry itself runs in the
worker and is exercised end-to-end there.
"""

import pytest

from cad_nodes import catalog
from cad_nodes.casts import WIRE_MESH, WIRE_VECTOR
from cad_nodes.graph import Graph, ValidationError
from cad_nodes.transpiler import transpile


def _g(nodes, connections):
    return Graph.from_dict({"name": "t", "nodes": nodes, "connections": connections})


# Anchor on `__out_` so we match a call site, never the PREAMBLE def (see
# test_mesh_lane._calls for the full why).
def _calls(code: str, helper: str) -> bool:
    return f"{helper}(__out_" in code


def _lattice_graph():
    """The flagship chain: Sphere -> Populate(volume) -> Voronoi3D -> MeshSubtract."""
    return _g(
        [{"id": "n1", "type": "Sphere", "params": {"radius": 20}},
         {"id": "n2", "type": "PopulateGeometry", "params": {"count": 60, "seed": 7}},
         {"id": "n3", "type": "Voronoi3D", "params": {"scale": 0.85}},
         {"id": "n4", "type": "MeshSubtract", "params": {}, "preview": True}],
        [{"id": "c1", "from_node": "n1", "from_socket": "result",
          "to_node": "n2", "to_socket": "region"},
         {"id": "c2", "from_node": "n2", "from_socket": "points",
          "to_node": "n3", "to_socket": "points"},
         {"id": "c3", "from_node": "n1", "from_socket": "result",
          "to_node": "n3", "to_socket": "body"},
         {"id": "c4", "from_node": "n1", "from_socket": "result",
          "to_node": "n4", "to_socket": "a"},
         {"id": "c5", "from_node": "n3", "from_socket": "cells",
          "to_node": "n4", "to_socket": "b"}],
    )


# --- catalog shape --------------------------------------------------------
def test_voronoi3d_lives_on_the_mesh_lane():
    nd = catalog.get("Voronoi3D")
    assert nd.outputs[0].wire_type == WIRE_MESH
    points = nd.input("points")
    assert points.wire_type == WIRE_VECTOR and points.list_access


def test_populate_region_accepts_every_dimension():
    # curve comes free via the global curve->surface compatibility; solid and
    # mesh are per-socket widenings — and raw, so none of them gets coerced
    # before the helper can see what it was handed.
    region = catalog.get("PopulateGeometry").input("region")
    assert region.raw
    assert set(region.accepts or []) == {"solid", "mesh"}


# --- validation -----------------------------------------------------------
@pytest.mark.parametrize("src_type,src_params,src_socket", [
    ("Box", {}, "result"),                      # solid  -> 3D volume fill
    ("Line", {}, "result"),                     # curve  -> 1D along
    ("ImportMesh", {"path": "p.stl"}, "result"),  # mesh -> volume/surface
])
def test_every_dimension_wires_into_populate(src_type, src_params, src_socket):
    g = _g(
        [{"id": "s", "type": src_type, "params": src_params},
         {"id": "p", "type": "PopulateGeometry", "params": {}}],
        [{"id": "c", "from_node": "s", "from_socket": src_socket,
          "to_node": "p", "to_socket": "region"}],
    )
    g.validate()


def test_lattice_graph_validates_end_to_end():
    _lattice_graph().validate()


def test_voronoi3d_cells_never_reenter_the_brep_lane():
    g = _g(
        [{"id": "v", "type": "Voronoi3D", "params": {}},
         {"id": "f", "type": "Fillet", "params": {}}],
        [{"id": "c", "from_node": "v", "from_socket": "cells",
          "to_node": "f", "to_socket": "part"}],
    )
    with pytest.raises(ValidationError):
        g.validate()


# --- transpiler emission --------------------------------------------------
def test_lattice_chain_emits_the_whole_story():
    code = transpile(_lattice_graph())
    # the exact Populate call shape — params first, region last (template pin)
    assert "_populate(60, 7, 100.0, 100.0, __out_" in code
    assert _calls(code, "_voronoi3d")
    assert _calls(code, "_to_mesh")               # the solid->mesh cast on `body`
    assert "_mesh_bool('subtract'" in code


def test_raw_region_gets_no_boundary_cast():
    # A curve into a surface socket normally emits _face(...); Populate's raw
    # socket must receive the curve UNTOUCHED — closed-vs-open dispatch happens
    # inside the helper. Same for a solid: no _to_mesh at this boundary.
    g = _g(
        [{"id": "l", "type": "Line", "params": {}},
         {"id": "p", "type": "PopulateGeometry", "params": {}}],
        [{"id": "c", "from_node": "l", "from_socket": "result",
          "to_node": "p", "to_socket": "region"}],
    )
    code = transpile(g)
    assert not _calls(code, "_face")
    g2 = _g(
        [{"id": "b", "type": "Box", "params": {}},
         {"id": "p", "type": "PopulateGeometry", "params": {}}],
        [{"id": "c", "from_node": "b", "from_socket": "result",
          "to_node": "p", "to_socket": "region"}],
    )
    assert not _calls(transpile(g2), "_to_mesh")


def test_voronoi3d_fans_out_downstream():
    # cells is a list: a downstream item-access input must wrap in _fanout —
    # pins the _LIST_PRODUCERS registration.
    g = _g(
        [{"id": "v", "type": "Voronoi3D", "params": {}},
         {"id": "m", "type": "Move", "params": {"z": 5}}],
        [{"id": "c", "from_node": "v", "from_socket": "cells",
          "to_node": "m", "to_socket": "shape"}],
    )
    assert "_fanout(" in transpile(g)


def test_seeded_randomness_stays_memo_cacheable():
    # The seed lives INSIDE the helper (np.random.RandomState), so the emitted
    # line has no "random." substring and both nodes keep their memo keys.
    src = transpile(_lattice_graph(), memo=True)
    assert src.count("_m = _memo_get(") >= 2
