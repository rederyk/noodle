"""Join (sew) — wire shape, emission, and the line it draws against Union.

Pure-Python: no build123d. These pin the *contract* — that Join takes curves,
surfaces and solids on one collector, that its pieces reach the helper as a
LIST (the collector spreads its wires, so the template brackets them), and that
Union stopped advertising itself as a sew. The geometry — sewing six faces into
a closed solid, five into an open shell, chaining edges into a wire, and the
refusals — runs in the worker and is exercised end-to-end there.
"""

from cad_nodes import catalog
from cad_nodes.casts import WIRE_CURVE, WIRE_SOLID, WIRE_SURFACE
from cad_nodes.graph import Graph
from cad_nodes.transpiler import PREAMBLE, transpile


def _g(nodes, connections=()):
    return Graph.from_dict({"name": "t", "nodes": nodes, "connections": list(connections)})


def _wire(i, src, dst, socket="shapes"):
    return {"id": str(i), "from_node": src, "from_socket": "result",
            "to_node": dst, "to_socket": socket}


# --- catalog contract ------------------------------------------------------
def test_registered_next_to_the_booleans():
    d = catalog.get("Join")
    assert d.category == "boolean"
    assert [s.wire_type for s in d.outputs] == [WIRE_SURFACE]
    assert d.output_follows == "shapes"


def test_one_collector_that_takes_all_three_lanes():
    """Curves, surfaces and solids all sew — the helper dispatches at runtime."""
    s = catalog.get("Join").input("shapes")
    assert s.multiple
    assert s.wire_type == WIRE_SURFACE
    assert WIRE_CURVE in s.accepts and WIRE_SOLID in s.accepts


def test_a_closed_curve_is_not_filled_on_the_way_in():
    """A `multiple` collector skips boundary casts, so a closed curve arrives as
    a curve and can be CHAINED. Were it cast through `_face` it would land as a
    surface and Join would sew a region instead."""
    s = catalog.get("Join").input("shapes")
    assert s.multiple, "the no-cast guarantee rides on the collector branch"
    assert not s.raw, "collectors already skip casts — an extra raw would be noise"


def test_findable_under_the_words_another_cad_uses():
    words = catalog.get("Join").aliases
    for w in ("sew", "stitch", "unisci"):
        assert w in words


# --- emission --------------------------------------------------------------
def test_pieces_reach_the_helper_as_one_list():
    """The collector SPREADS its wires (`a, b`); the template must bracket them
    or `tolerance` would land in the middle of the shapes."""
    g = _g([{"id": "a", "type": "Box", "params": {}},
            {"id": "b", "type": "Box", "params": {}},
            {"id": "j", "type": "Join",
             "params": {"tolerance": 0.05, "make_solid": True}}],
           [_wire(1, "a", "j"), _wire(2, "b", "j")])
    g.validate()
    line = [l for l in transpile(g).splitlines() if "@node:j" in l][0]
    assert "_join([__out_1, __out_2], 0.05, True)" in line


def test_close_into_a_solid_is_a_switch():
    g = _g([{"id": "a", "type": "Box", "params": {}},
            {"id": "j", "type": "Join", "params": {"make_solid": False}}],
           [_wire(1, "a", "j")])
    line = [l for l in transpile(g).splitlines() if "@node:j" in l][0]
    assert line.rstrip().endswith("False)  # @node:j (Join)")


def test_nothing_wired_is_not_a_syntax_error():
    g = _g([{"id": "j", "type": "Join", "params": {}}])
    line = [l for l in transpile(g).splitlines() if "@node:j" in l][0]
    assert "_join([], " in line


def test_the_helpers_ship_in_the_preamble():
    assert "def _join(" in PREAMBLE
    assert "def _coplanar_check(" in PREAMBLE


# --- the line against Union ------------------------------------------------
def test_union_still_takes_many_shapes_the_same_way():
    """The guard is runtime-only: Union's shape, sockets and template are
    untouched, so every existing graph still transpiles identically."""
    d = catalog.get("Union")
    assert d.input("shapes").multiple
    assert d.code_template["algebra"] == "_union({shapes})"


def test_union_sends_you_to_join():
    """Both directions of the pointer, so neither description drifts alone."""
    assert "Join" in catalog.get("Union").description
    assert "Union" in catalog.get("Join").description


# --- what Join feeds: Shell, and the traps found downstream of it -----------
# Joining faces gives an open Shell, and the obvious next node is Shell (thicken)
# or Shell By Faces. Both lied when they could not do the job; these pin the
# fixes. The geometry is measured in the worker — see the §5f notes.
def test_thicken_works_on_a_copy():
    """A FAILED BRepOffset scribbles on the input faces' TShapes, poisoning them
    for any node still holding them (measured: a sibling Shell By Faces on the
    same polyhedron then returned volume 4728 on a part of 2536, invalid,
    instead of 432). Thicken a copy — same family as the `_reanchor` trap."""
    assert "_copy.deepcopy(_surf)" in PREAMBLE
    assert "import copy as _copy" in PREAMBLE


def test_thicken_never_returns_an_invalid_wall():
    """OCCT returns a SHELL when it could not close the wall, and build123d's
    blind TopoDS.Solid cast then raises `Standard_TypeMismatch` at the user."""
    body = PREAMBLE.split("def _thicken(")[1].split("\ndef ")[0]
    assert "if not _out.is_valid:" in body
    assert body.count("raise ValueError(") == 2


def test_shell_by_faces_no_longer_swallows_its_failure():
    """It used to `except Exception: return _part` — no error, no hollow, a node
    that quietly handed back its input."""
    body = PREAMBLE.split("def _shell_faces(")[1].split("\ndef ")[0]
    assert "return _part\n    _op" not in body
    assert body.count("raise ValueError(") == 2


def test_shell_by_faces_rejects_an_open_surface():
    """The natural mistake after a Join: remove the faces, THEN try to hollow."""
    body = PREAMBLE.split("def _shell_faces(")[1].split("\ndef ")[0]
    assert "hollows a closed solid" in body


def test_a_curve_still_cannot_reach_union():
    """It never could — there is no curve->solid cast — and Join is now the
    node that does want one. Pinned so a future cast doesn't quietly open it."""
    from cad_nodes.casts import wires_compatible

    assert not wires_compatible(WIRE_CURVE, WIRE_SOLID)
    assert catalog.get("Union").input("shapes").accepts == []
