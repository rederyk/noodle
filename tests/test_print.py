"""
Print physics (PLAN_PRINT_PHYSICS.md) — wire types, params and transpiler emission.

Pure-Python: no build123d, no trimesh. These assert the *shape* of the lane (what may
connect to what, what code comes out), not the geometry — the measurements themselves
run in the worker and are exercised end-to-end by examples/print-orientation.json.
"""

import pytest

from cad_nodes import catalog
from cad_nodes.casts import WIRE_DATA, WIRE_MESH, WIRE_SOLID, WIRE_VECTOR
from cad_nodes.graph import Graph, ValidationError
from cad_nodes.transpiler import transpile


def _g(nodes, connections):
    return Graph.from_dict({"name": "t", "nodes": nodes, "connections": connections})


def _calls(code: str, helper: str) -> bool:
    # the PREAMBLE *defines* these helpers, so a bare substring match would pass for the
    # wrong reason. A call site is an assignment; a definition is a def.
    return any(f"= {helper}(" in ln for ln in code.splitlines())


PRINT_NODES = ["PlaceOnBed", "PrintCheck", "OverhangFaces", "OrientForPrint"]


@pytest.mark.parametrize("ntype", PRINT_NODES)
def test_the_print_nodes_are_registered(ntype):
    assert catalog.get(ntype).category == "print"


def test_place_on_bed_serves_both_lanes():
    # It measures on the mesh and moves the ORIGINAL, so a solid comes out a solid — the
    # same trick that lets one Move node serve both lanes.
    sock = catalog.get("PlaceOnBed").inputs[0]
    assert sock.wire_type == WIRE_SOLID
    assert WIRE_MESH in (sock.accepts or [])
    assert catalog.get("PlaceOnBed").output_follows == "shape"


def test_a_solid_dropped_on_the_bed_stays_a_solid():
    g = _g(
        [{"id": "b", "type": "Box", "params": {}},
         {"id": "d", "type": "PlaceOnBed", "params": {}},
         {"id": "f", "type": "Fillet", "params": {}}],   # a B-Rep-only node downstream
        [{"id": "c1", "from_node": "b", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"},
         {"id": "c2", "from_node": "d", "from_socket": "result",
          "to_node": "f", "to_socket": "part"}],
    )
    g.validate()                                   # output_follows carries `solid` through
    assert _calls(transpile(g), "_bed_drop")


def test_print_check_reports_on_the_data_bus():
    out = catalog.get("PrintCheck").outputs[0]
    assert out.wire_type == WIRE_DATA              # it is text: it goes to a Panel
    g = _g(
        [{"id": "b", "type": "Box", "params": {}},
         {"id": "p", "type": "PrintCheck", "params": {}},
         {"id": "d", "type": "Display", "params": {}}],
        [{"id": "c1", "from_node": "b", "from_socket": "result",
          "to_node": "p", "to_socket": "mesh"},    # a solid tessellates at the boundary
         {"id": "c2", "from_node": "p", "from_socket": "report",
          "to_node": "d", "to_socket": "value"}],
    )
    g.validate()
    code = transpile(g)
    # the solid tessellates AT THE BOUNDARY (the solid->mesh cast), so the report is
    # measured on triangles, not on the B-Rep: PrintCheck never sees a solid
    assert "_print_check(_to_mesh(__out_" in code


def test_overhang_faces_stays_on_the_mesh_lane():
    assert catalog.get("OverhangFaces").outputs[0].wire_type == WIRE_MESH


def test_orient_for_print_takes_a_load_vector_and_it_is_optional():
    load = next(s for s in catalog.get("OrientForPrint").inputs if s.name == "load")
    assert load.wire_type == WIRE_VECTOR and not load.required


def test_orient_for_print_has_two_outputs_from_one_search():
    # The mesh and the table that says why it won. Both must come from ONE _orient_plan
    # call: scoring the poses means slicing each of them, and doing that twice because a
    # Panel happens to be wired in would be daft.
    outs = {s.name: s.wire_type for s in catalog.get("OrientForPrint").outputs}
    assert outs == {"result": WIRE_MESH, "report": WIRE_DATA}
    g = _g(
        [{"id": "m", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "o", "type": "OrientForPrint", "params": {}},
         {"id": "e", "type": "ExportMesh", "params": {"path": "out.stl"}},
         {"id": "d", "type": "Display", "params": {}}],
        [{"id": "c1", "from_node": "m", "from_socket": "result",
          "to_node": "o", "to_socket": "mesh"},
         {"id": "c2", "from_node": "o", "from_socket": "result",
          "to_node": "e", "to_socket": "mesh"},
         {"id": "c3", "from_node": "o", "from_socket": "report",
          "to_node": "d", "to_socket": "value"}],
    )
    g.validate()
    code = transpile(g)
    assert code.count("_orient_plan(__out_") == 1
    assert "['mesh']" in code and "['report']" in code


def test_the_two_outputs_do_not_collapse_onto_one_variable():
    # The bug this guards: without out_var_of, a node's outputs share one var, and the
    # exported STL would silently be the report string.
    g = _g(
        [{"id": "m", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "o", "type": "OrientForPrint", "params": {}},
         {"id": "e", "type": "ExportMesh", "params": {"path": "out.stl"}},
         {"id": "d", "type": "Display", "params": {}}],
        [{"id": "c1", "from_node": "m", "from_socket": "result",
          "to_node": "o", "to_socket": "mesh"},
         {"id": "c2", "from_node": "o", "from_socket": "result",
          "to_node": "e", "to_socket": "mesh"},
         {"id": "c3", "from_node": "o", "from_socket": "report",
          "to_node": "d", "to_socket": "value"}],
    )
    code = transpile(g)
    export = next(l for l in code.splitlines() if "_mesh_export(" in l and "__out_" in l)
    probe = next(l for l in code.splitlines() if "_probe(" in l and "__out_" in l)
    assert "_rep" not in export       # the STL gets the mesh…
    assert "_rep" in probe            # …and the Panel gets the report


def test_a_mesh_may_not_be_wired_into_the_load():
    # `load` is a direction, not a body. The data bus feeds a vector, a mesh does not.
    g = _g(
        [{"id": "m", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "o", "type": "OrientForPrint", "params": {}}],
        [{"id": "c", "from_node": "m", "from_socket": "result",
          "to_node": "o", "to_socket": "load"}],
    )
    with pytest.raises(ValidationError):
        g.validate()


def test_support_volume_is_a_body_on_the_mesh_lane():
    # Not a number, a BODY: you can preview it, inspect it, export it. That is the point —
    # `area x height` gestures at the cost; a boolean IS the cost.
    assert catalog.get("SupportVolume").outputs[0].wire_type == WIRE_MESH
    g = _g(
        [{"id": "m", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "s", "type": "SupportVolume", "params": {}},
         {"id": "i", "type": "MeshInspect", "params": {}}],
        [{"id": "c1", "from_node": "m", "from_socket": "result",
          "to_node": "s", "to_socket": "mesh"},
         {"id": "c2", "from_node": "s", "from_socket": "result",
          "to_node": "i", "to_socket": "mesh"}],
    )
    g.validate()
    assert _calls(transpile(g), "_support_body")


def test_the_search_declares_which_support_number_it_used():
    # All-or-nothing: ranking one pose by real volume and the next by a proxy would compare
    # two different quantities and call it a decision. `exact_below` is the switch, and it
    # reaches _orient_plan as an argument (not a silent default).
    p = {p.name: p for p in catalog.get("OrientForPrint").params}
    assert "exact_below" in p
    g = _g(
        [{"id": "m", "type": "ImportMesh", "params": {"path": "p.stl"}},
         {"id": "o", "type": "OrientForPrint", "params": {"exact_below": 1234}}],
        [{"id": "c", "from_node": "m", "from_socket": "result",
          "to_node": "o", "to_socket": "mesh"}],
    )
    code = transpile(g)
    call = next(l for l in code.splitlines() if "_orient_plan(__out_" in l)
    assert "1234" in call
