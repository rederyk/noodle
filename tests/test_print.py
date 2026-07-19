"""
Print physics (PLAN_PRINT_PHYSICS.md) — wire types, params and transpiler emission.

Pure-Python: no build123d, no trimesh. These assert the *shape* of the lane (what may
connect to what, what code comes out), not the geometry — the measurements themselves
run in the worker and are exercised end-to-end by examples/print-orientation.json.
"""

import math

import pytest

from cad_nodes import catalog
from cad_nodes.casts import WIRE_DATA, WIRE_MESH, WIRE_PLANE, WIRE_SOLID, WIRE_VECTOR
from cad_nodes.graph import Graph, ValidationError
from cad_nodes.transpiler import transpile


def _g(nodes, connections):
    return Graph.from_dict({"name": "t", "nodes": nodes, "connections": connections})


def _calls(code: str, helper: str) -> bool:
    # the PREAMBLE *defines* these helpers, so a bare substring match would pass for the
    # wrong reason. A call site is an assignment; a definition is a def.
    return any(f"= {helper}(" in ln for ln in code.splitlines())


PRINT_NODES = ["PlaceOnBed", "Drop", "PrintCheck", "OverhangFaces", "OrientForPrint"]


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


# --- Drop: Place on Bed as a fall you can scrub ------------------------------

def test_drop_serves_both_lanes_like_place_on_bed():
    # Same trick as PlaceOnBed: measure on the mesh, move the original.
    sock = catalog.get("Drop").inputs[0]
    assert sock.wire_type == WIRE_SOLID
    assert WIRE_MESH in (sock.accepts or [])
    assert catalog.get("Drop").output_follows == "shape"


def test_drop_takes_an_optional_plane_to_fall_toward():
    plane = next(s for s in catalog.get("Drop").inputs if s.name == "plane")
    assert plane.wire_type == WIRE_PLANE and not plane.required


def test_drop_timeline_is_a_normalised_scrub():
    # 0 = where the part is, 1 = at rest. Always the FULL settle: the material
    # changes the shape of the journey, never the reach of the slider.
    t = catalog.get("Drop").param("t")
    assert (t.min, t.max) == (0.0, 1.0)


def test_drop_declares_the_timeline_gizmo():
    # Edit-on-canvas: drag the part along Z to scrub t (pull down = drop, lift
    # = rewind). A wired `t` locks the gizmo — the upstream slider owns time.
    g = catalog.get("Drop").gizmo
    assert g == {"kind": "timeline", "binds": ["t"], "anchor": "preview",
                 "lock": ["t"]}


def test_drop_materials_are_fixed_and_contrasting():
    # Plastic is the default; lead is the promised counterpoint (one dead thud).
    mat = catalog.get("Drop").param("material")
    assert mat.default == "plastic"
    assert "lead" in mat.options and "rubber" in mat.options


def test_drop_settles_by_default_and_the_toggle_reaches_the_call():
    # The topple cascade is the point of the node — on by default; `settle`
    # off must fall back to the translation-only fall (balanced on its edge).
    p = catalog.get("Drop").param("settle")
    assert p.type == "bool" and p.default is True
    g = _g(
        [{"id": "b", "type": "Box", "params": {}},
         {"id": "d", "type": "Drop", "params": {"settle": False}}],
        [{"id": "c", "from_node": "b", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"}],
    )
    call = next(l for l in transpile(g).splitlines() if "= _drop(" in l)
    assert "False" in call


def test_drop_transpiles_with_time_and_material_at_the_call_site():
    g = _g(
        [{"id": "b", "type": "Box", "params": {}},
         {"id": "d", "type": "Drop", "params": {"t": 0.35, "material": "lead"}}],
        [{"id": "c", "from_node": "b", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"}],
    )
    g.validate()
    code = transpile(g)
    assert _calls(code, "_drop")
    call = next(l for l in code.splitlines() if "= _drop(" in l)
    assert "0.35" in call and "'lead'" in call


def test_a_solid_dropped_stays_a_solid():
    # output_follows carries `solid` through, so a B-Rep-only node may follow.
    g = _g(
        [{"id": "b", "type": "Box", "params": {}},
         {"id": "d", "type": "Drop", "params": {}},
         {"id": "f", "type": "Fillet", "params": {}}],
        [{"id": "c1", "from_node": "b", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"},
         {"id": "c2", "from_node": "d", "from_socket": "result",
          "to_node": "f", "to_socket": "part"}],
    )
    g.validate()


def test_drop_collide_exists_and_is_off_by_default():
    # Collisions cost real compute (rays + sampled rotations): opt-in only.
    p = catalog.get("Drop").param("collide")
    assert p.type == "bool" and p.default is False


def test_collide_unfans_the_shapes_into_one_scene():
    # Two shapes + collide: the transpiler must NOT fan out — the runtime needs
    # the whole list to stack the parts against each other — and the output is
    # a list again so downstream still fans.
    g = _g(
        [{"id": "a", "type": "Box", "params": {}},
         {"id": "b", "type": "Box", "params": {}},
         {"id": "d", "type": "Drop", "params": {"collide": True}}],
        [{"id": "c1", "from_node": "a", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"},
         {"id": "c2", "from_node": "b", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"}],
    )
    g.validate()
    code = transpile(g)
    call = next(l for l in code.splitlines() if "= _drop(" in l)
    assert "_fanout" not in call and "[__out_" in call


def test_without_collide_several_shapes_still_fan_out():
    g = _g(
        [{"id": "a", "type": "Box", "params": {}},
         {"id": "b", "type": "Box", "params": {}},
         {"id": "d", "type": "Drop", "params": {}}],
        [{"id": "c1", "from_node": "a", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"},
         {"id": "c2", "from_node": "b", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"}],
    )
    code = transpile(g)
    call = next(l for l in code.splitlines() if "_drop(" in l and "__out_" in l)
    assert "_fanout" in call


def test_container_unfans_the_shapes_even_without_the_toggle():
    # Parts falling INTO one bowl are one scene by definition — the container
    # implies collide, or there would be nothing for the parts to land in.
    g = _g(
        [{"id": "a", "type": "Box", "params": {}},
         {"id": "b", "type": "Box", "params": {}},
         {"id": "bowl", "type": "Sphere", "params": {}},
         {"id": "d", "type": "Drop", "params": {}}],
        [{"id": "c1", "from_node": "a", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"},
         {"id": "c2", "from_node": "b", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"},
         {"id": "c3", "from_node": "bowl", "from_socket": "result",
          "to_node": "d", "to_socket": "container"}],
    )
    g.validate()
    code = transpile(g)
    call = next(l for l in code.splitlines() if "= _drop(" in l)
    assert "_fanout" not in call and "[__out_" in call


def test_container_is_optional_and_absent_by_default():
    # Nothing wired: the argument must still be passed, as None — the runtime
    # signature takes it positionally.
    g = _g(
        [{"id": "a", "type": "Box", "params": {}},
         {"id": "d", "type": "Drop", "params": {}}],
        [{"id": "c1", "from_node": "a", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"}],
    )
    g.validate()
    sock = catalog.get("Drop").input("container")
    assert sock is not None and sock.required is False
    call = next(l for l in transpile(g).splitlines() if "= _drop(" in l)
    args = [a.strip() for a in
            call.split("_drop(", 1)[1].rsplit(")", 1)[0].split(",")]
    # Positional, but pinned by the template rather than by a magic index: adding
    # an argument (motion did exactly this) must not silently move the assertion
    # onto a neighbouring slot.
    slots = catalog.get("Drop").code_template["algebra"]
    slots = slots.split("_drop(", 1)[1].rsplit(")", 1)[0].split(",")
    assert args[[s.strip() for s in slots].index("{container}")] == "None"


def test_grip_reaches_the_runtime_as_an_argument():
    # The scene's friction is a knob, not a constant: at grip 1 a sloped static
    # face grabs a falling part and flings it sideways, which is what turned the
    # Galton board's bell into two lumps against the walls. It must be emitted.
    p = {p.name: p for p in catalog.get("Drop").params}
    assert "grip" in p and p["grip"].default == 1.0
    g = _g(
        [{"id": "a", "type": "Box", "params": {}},
         {"id": "d", "type": "Drop", "params": {"grip": 0.15}}],
        [{"id": "c1", "from_node": "a", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"}],
    )
    g.validate()
    call = next(l for l in transpile(g).splitlines() if "= _drop(" in l)
    assert "0.15" in call


# ---------------------------------------------------------------------------
# ContainerMotion — the container stops being furniture (PLAN_PRINT_PHYSICS.md).
# The wiring is asserted here; the FRAME MATH is asserted for real by exec'ing
# the PREAMBLE helpers below, because "world xyz -> bed frame" and the pivot
# correction are exactly the two things that look right and silently are not.
# ---------------------------------------------------------------------------

def _drop_args(call: str) -> list[str]:
    # The emitted line carries a trailing `# @node:… (Drop)` comment, and that
    # comment contains a closing paren — so the call has to be cut at the FIRST
    # one after the arguments, not the last one on the line.
    inner = call.split("_drop(", 1)[1]
    return [a.strip() for a in inner[:inner.index(")")].split(",")]


def _preamble_fns(*names):
    """Lift pure-math helpers out of the PREAMBLE and exec them. They need only
    math + numpy, so they are testable without build123d like everything else
    here — and they are worth testing: a rotation carried into the wrong frame
    still produces a plausible-looking animation."""
    import math as _m

    from cad_nodes.transpiler import PREAMBLE
    src = "\n".join(f"def {n}(" + PREAMBLE.split(f"\ndef {n}(")[1].split("\ndef ")[0]
                    for n in names)
    ns = {"math": _m}
    exec(src, ns)
    return ns


def test_container_motion_is_registered_and_outputs_a_plan():
    d = catalog.get("ContainerMotion")
    assert d.category == "print"
    assert d.outputs[0].wire_type == WIRE_DATA
    # It is driven in world xyz — both a wired vector and the widgets.
    assert d.input("offset").wire_type == WIRE_VECTOR
    assert d.input("pivot").wire_type == WIRE_VECTOR
    assert {p.name for p in d.params} >= {"x", "y", "z", "rx", "ry", "rz",
                                          "cycles", "duration", "delay"}


def test_motion_socket_is_optional_and_reaches_the_runtime():
    # Absent by default: a Drop with no motion must emit None in that slot, or
    # every existing graph starts driving a container that should hold still.
    slots = catalog.get("Drop").code_template["algebra"]
    slots = [s.strip() for s in
             slots.split("_drop(", 1)[1].rsplit(")", 1)[0].split(",")]
    assert "{motion}" in slots
    sock = catalog.get("Drop").input("motion")
    assert sock is not None and sock.required is False and sock.wire_type == WIRE_DATA

    g = _g(
        [{"id": "a", "type": "Box", "params": {}},
         {"id": "d", "type": "Drop", "params": {}}],
        [{"id": "c1", "from_node": "a", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"}],
    )
    g.validate()
    call = next(l for l in transpile(g).splitlines() if "= _drop(" in l)
    args = _drop_args(call)
    assert args[slots.index("{motion}")] == "None"

    # Wired, the plan node is emitted and threaded in.
    g2 = _g(
        [{"id": "a", "type": "Box", "params": {}},
         {"id": "b", "type": "Box", "params": {}},
         {"id": "m", "type": "ContainerMotion", "params": {"ry": 90}},
         {"id": "d", "type": "Drop", "params": {}}],
        [{"id": "c1", "from_node": "a", "from_socket": "result",
          "to_node": "d", "to_socket": "shape"},
         {"id": "c2", "from_node": "b", "from_socket": "result",
          "to_node": "d", "to_socket": "container"},
         {"id": "c3", "from_node": "m", "from_socket": "motion",
          "to_node": "d", "to_socket": "motion"}],
    )
    g2.validate()
    code = transpile(g2)
    assert _calls(code, "_container_motion")
    plan_var = next(ln.split(" = ")[0].strip() for ln in code.splitlines()
                    if "= _container_motion(" in ln)
    call = next(ln for ln in code.splitlines() if "= _drop(" in ln)
    assert _drop_args(call)[slots.index("{motion}")] == plan_var


def test_motion_ramp_and_oscillation_are_the_two_shapes():
    ns = _preamble_fns("_mat_quat", "_container_motion", "_motion_driver")
    import numpy as np
    I = np.eye(3)

    # cycles = 0 is a RAMP: nothing before the delay, full displacement at the
    # end, and it STAYS there (a tilted tray does not un-tilt).
    plan = ns["_container_motion"](None, 10.0, 0.0, 0.0, 0, 0, 0, None,
                                   0.0, 2.0, 1.0, "linear")
    pose, end = ns["_motion_driver"](plan, I, np.zeros(3), np.zeros(3))
    assert end == 3.0
    assert np.allclose(pose(0.0)[0], [0, 0, 0])
    assert np.allclose(pose(1.0)[0], [0, 0, 0])          # still waiting
    assert np.allclose(pose(2.0)[0], [5, 0, 0])          # halfway through
    assert np.allclose(pose(3.0)[0], [10, 0, 0])
    assert np.allclose(pose(9.0)[0], [10, 0, 0])         # and stays

    # cycles > 0 OSCILLATES about the start and must come back to it, or the rig
    # would be parked mid-swing forever after the shaking stops.
    plan = ns["_container_motion"](None, 10.0, 0.0, 0.0, 0, 0, 0, None,
                                   2.0, 2.0, 0.0, "linear")
    pose, end = ns["_motion_driver"](plan, I, np.zeros(3), np.zeros(3))
    assert np.allclose(pose(0.0)[0], [0, 0, 0])
    assert np.allclose(pose(0.25)[0], [10, 0, 0])        # first quarter cycle
    assert np.allclose(pose(0.75)[0], [-10, 0, 0], atol=1e-9)
    assert np.allclose(pose(2.0)[0], [0, 0, 0], atol=1e-9)
    assert np.allclose(pose(5.0)[0], [0, 0, 0])          # parked where it began


def test_motion_rotates_about_the_pivot_not_the_bed_origin():
    # bullet poses a body as x -> R x + pos, so turning a bowl about its own
    # centre is ENTIRELY the job of that pos term. If it is wrong the container
    # swings through the scene on an invisible arm instead of turning in place.
    ns = _preamble_fns("_mat_quat", "_container_motion", "_motion_driver")
    import numpy as np
    piv = np.array([30.0, 0.0, 5.0])
    plan = ns["_container_motion"](None, 0, 0, 0, 0, 0, 90.0, None,
                                   0.0, 1.0, 0.0, "linear")
    pose, _ = ns["_motion_driver"](plan, np.eye(3), np.zeros(3), piv)
    pos, q = pose(1.0)
    R = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)   # +90 about z
    assert np.allclose(R @ piv + pos, piv, atol=1e-9)               # pivot fixed
    # a point 20mm out along +y from the pivot swings round to 20mm along -x
    far = piv + np.array([0.0, 20.0, 0.0])
    assert np.allclose(R @ far + pos, piv + np.array([-20.0, 0.0, 0.0]), atol=1e-9)


def test_motion_is_dictated_in_world_and_carried_into_the_bed_frame():
    # The user tilts a tray in WORLD xyz; the colliders live in bed coordinates.
    # On a bed rotated 90 deg about x, a world +z shove must come out as bed +y.
    ns = _preamble_fns("_mat_quat", "_container_motion", "_motion_driver")
    import numpy as np
    B = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])   # bed z = world -y
    plan = ns["_container_motion"](None, 0.0, 0.0, 10.0, 0, 0, 0, None,
                                   0.0, 1.0, 0.0, "linear")
    pose, _ = ns["_motion_driver"](plan, B, np.zeros(3), np.zeros(3))
    assert np.allclose(pose(1.0)[0], B.T @ np.array([0, 0, 10.0]))

    # A world rotation must likewise arrive as R_bed = B^T R_world B.
    plan = ns["_container_motion"](None, 0, 0, 0, 0, 0, 40.0, None,
                                   0.0, 1.0, 0.0, "linear")
    pose, _ = ns["_motion_driver"](plan, B, np.zeros(3), np.zeros(3))
    _, q = pose(1.0)
    c, s = math.cos(math.radians(40)), math.sin(math.radians(40))
    Rw = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    qm = ns["_mat_quat"](B.T @ Rw @ B)
    assert np.allclose(q, qm, atol=1e-12)


def test_angular_velocity_reproduces_the_prescribed_turn():
    # resetBaseVelocity is what actually carries the contents (measured: without
    # it a translated tray drags a resting part 1.2% of the way, i.e. not at
    # all). The velocity handed to the solver must match the motion it is paired
    # with, or the friction is a lie in a subtler way.
    ns = _preamble_fns("_mat_quat", "_ang_vel")
    import numpy as np
    dt = 1.0 / 240.0
    for deg in (1.0, 15.0, -30.0):
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
        w = ns["_ang_vel"](ns["_mat_quat"](np.eye(3)), ns["_mat_quat"](R), dt)
        assert np.allclose(w[:2], [0, 0], atol=1e-9)          # about z only
        assert w[2] == pytest.approx(a / dt, rel=2e-3)        # right rate, right sign
