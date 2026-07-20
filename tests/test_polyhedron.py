"""Polyhedron — wire shape, face-count snapping, and transpiler emission.

Pure-Python: no build123d, no scipy. These pin the *contract* — that one node
serves both lanes off one param, that `faces` always snaps to a count the chosen
family can actually build, and what code comes out. The geometry itself (hull,
sewing, the volume optimum) runs in the worker and is exercised end-to-end there.
"""

import pytest

from cad_nodes import catalog
from cad_nodes.casts import WIRE_SOLID, WIRE_VECTOR
from cad_nodes.graph import Graph
from cad_nodes.transpiler import transpile


def _g(nodes, connections=()):
    return Graph.from_dict({"name": "t", "nodes": nodes, "connections": list(connections)})


def _node(**params):
    p = {"kind": "platonic", "faces": 6, "size": 10, "as_mesh": False}
    p.update(params)
    return {"id": "n1", "type": "Polyhedron", "params": p, "position": [0, 0]}


# --- catalog contract ------------------------------------------------------
def test_registered_as_a_solid_primitive():
    d = catalog.get("Polyhedron")
    assert d.category == "primitives_3d"
    assert [s.wire_type for s in d.outputs] == [WIRE_SOLID]


def test_origin_and_params_as_inputs():
    """origin places it; faces/size are params-as-inputs, so a Range can fan it out."""
    d = catalog.get("Polyhedron")
    assert d.input("origin").wire_type == WIRE_VECTOR
    assert not d.input("origin").required
    for name in ("faces", "size"):
        assert d.input(name) is not None, f"{name} must be wirable, not widget-only"
        assert d.param(name) is not None, f"{name} must fall back to a widget"


def test_brep_is_the_default_lane():
    """The mesh toggle is opt-in — wiring a fresh Polyhedron into a Fillet must work."""
    assert catalog.get("Polyhedron").param("as_mesh").default is False


def test_every_kind_option_is_buildable():
    opts = catalog.get("Polyhedron").param("kind").options
    assert set(opts) == {"platonic", "prism", "antiprism", "bipyramid", "sphere"}


# --- emission --------------------------------------------------------------
def test_emits_the_helper_with_every_argument():
    code = transpile(_g([_node(kind="prism", faces=9, size=4.5)]))
    assert "_polyhedron('prism', 9, 4.5, False)" in code


def test_mesh_toggle_reaches_the_helper():
    code = transpile(_g([_node(as_mesh=True)]))
    assert "_polyhedron('platonic', 6, 10.0, True)" in code


def test_origin_is_applied_exactly_once():
    """The emitter wraps any node with an `origin` socket in _at() by itself, so
    the helper must NOT place the shape too — that doubled the translation."""
    g = _g([{"id": "p", "type": "ConstructPoint",
             "params": {"x": 1, "y": 2, "z": 3}, "position": [0, 0]}, _node()],
           [{"id": "c1", "from_node": "p", "from_socket": "point",
             "to_node": "n1", "to_socket": "origin"}])
    code = transpile(g)
    assert code.count("_at(_polyhedron") == 1
    assert "_polyhedron('platonic', 6, 10.0, False)" in code


# --- face-count snapping ---------------------------------------------------
# The whole point of the node: `faces` is a slider over the integers, but no
# family has a solid at every one of them. It must land on a real solid always.
_SNAP = [
    ("platonic", 4, 4), ("platonic", 5, 4), ("platonic", 7, 6), ("platonic", 11, 12),
    ("platonic", 17, 20), ("platonic", 200, 20),
    ("prism", 5, 5), ("prism", 4, 5), ("prism", 30, 30),
    ("antiprism", 8, 8), ("antiprism", 9, 8), ("antiprism", 4, 8), ("antiprism", 15, 14),
    ("bipyramid", 6, 6), ("bipyramid", 9, 8), ("bipyramid", 4, 6),
    ("sphere", 4, 4), ("sphere", 5, 4), ("sphere", 41, 40),
]


@pytest.mark.parametrize("kind,ask,want", _SNAP)
def test_face_count_snaps_to_something_real(kind, ask, want):
    ns = {}
    exec(_preamble_fragment(), ns)
    assert ns["_poly_snap"](kind, ask)[0] == want


@pytest.mark.parametrize("kind", ["platonic", "prism", "antiprism", "bipyramid", "sphere"])
def test_snapping_is_idempotent(kind):
    """Snap a snapped count and nothing moves — otherwise dragging the slider
    would drift the shape away under the user's hand."""
    ns = {}
    exec(_preamble_fragment(), ns)
    snap = ns["_poly_snap"]
    for ask in range(4, 60):
        once = snap(kind, ask)[0]
        assert snap(kind, once)[0] == once


def _preamble_fragment():
    """_poly_snap out of the PREAMBLE, standing alone — it is pure arithmetic, so
    it can be checked here without build123d in the room."""
    from cad_nodes.transpiler import PREAMBLE
    start = PREAMBLE.index("_PLATONIC_FACES = ")
    end = PREAMBLE.index("def _poly_ring(")
    return PREAMBLE[start:end].replace('\\"\\"\\"', '"""')
