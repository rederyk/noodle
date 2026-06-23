"""
Pure-Python tests for the node engine (no build123d required).

Run: python -m pytest tests/ -v
"""

import json
from pathlib import Path

import pytest

from cad_nodes import catalog
from cad_nodes.graph import Graph, ValidationError
from cad_nodes.toposort import toposort, CycleError
from cad_nodes.transpiler import transpile

EXAMPLES = Path(__file__).resolve().parent.parent / "cad_nodes" / "examples"


# --- toposort -------------------------------------------------------------
def test_toposort_linear_order():
    order = toposort(["a", "b", "c"], [("a", "b"), ("b", "c")])
    assert order == ["a", "b", "c"]


def test_toposort_is_stable():
    # No edges -> original order preserved.
    assert toposort(["x", "y", "z"], []) == ["x", "y", "z"]


def test_toposort_diamond():
    order = toposort(["a", "b", "c", "d"],
                     [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")


def test_toposort_detects_cycle():
    with pytest.raises(CycleError):
        toposort(["a", "b"], [("a", "b"), ("b", "a")])


# --- catalog --------------------------------------------------------------
def test_catalog_covers_core_categories():
    cats = {d.category for d in catalog.REGISTRY.values()}
    for expected in ["primitives_3d", "primitives_2d", "operations", "boolean",
                     "modifiers", "transform", "math", "export"]:
        assert expected in cats
    assert "Box" in catalog.REGISTRY
    assert catalog.get("Extrude").input("sketch") is not None


def test_catalog_serialises_to_json():
    data = catalog.as_json()
    assert isinstance(data, list) and len(data) > 20
    # Must be JSON-serialisable end to end.
    json.loads(json.dumps(data))


# --- validation -----------------------------------------------------------
def _flange() -> Graph:
    return Graph.from_dict(json.loads((EXAMPLES / "flange.json").read_text()))


def test_validate_flange_ok():
    warnings = _flange().validate()
    assert warnings == []


def test_validate_unknown_type():
    g = Graph.from_dict({"nodes": [{"id": "x", "type": "Nope"}], "connections": []})
    with pytest.raises(ValidationError):
        g.validate()


def test_validate_incompatible_wire():
    g = Graph.from_dict({
        "nodes": [
            {"id": "n", "type": "NumberSlider", "params": {"value": 1}},
            {"id": "b", "type": "Box"},
        ],
        # A data output cannot feed a geometry input.
        "connections": [{"id": "c", "from_node": "n", "from_socket": "result",
                         "to_node": "b", "to_socket": "missing"}],
    })
    with pytest.raises(ValidationError):
        g.validate()


# --- transpiler golden ----------------------------------------------------
def test_transpile_flange_golden():
    code = transpile(_flange())
    assert "from build123d import *" in code
    assert "__out_1 = Circle(20.0)" in code
    assert "extrude(__out_1, amount=10.0, taper=0.0)" in code
    assert "(__out_2 - __out_4)" in code
    assert "export_step(__out_5, 'flange.step')" in code
    assert "__result__ = __out_5" in code


def test_transpile_select_param_maps_to_code():
    g = Graph.from_dict({
        "nodes": [
            {"id": "b", "type": "Box"},
            {"id": "m", "type": "Mirror", "params": {"plane": "YZ"}},
        ],
        "connections": [{"id": "c", "from_node": "b", "from_socket": "result",
                         "to_node": "m", "to_socket": "shape"}],
    })
    code = transpile(g)
    assert "mirror(__out_1, Plane.YZ)" in code


def test_transpile_boolean_operators():
    # build123d algebra: + union, - difference, & intersection (NOT *).
    g = Graph.from_dict({
        "nodes": [
            {"id": "a", "type": "Box"},
            {"id": "b", "type": "Sphere"},
            {"id": "x", "type": "Intersect"},
        ],
        "connections": [
            {"id": "1", "from_node": "a", "from_socket": "result", "to_node": "x", "to_socket": "a"},
            {"id": "2", "from_node": "b", "from_socket": "result", "to_node": "x", "to_socket": "b"},
        ],
    })
    code = transpile(g)
    assert "(__out_1 & __out_2)" in code
    assert " * " not in code  # '*' is for Locations, not intersection


def test_transpile_bypass_passes_input_through():
    # A bypassed node is skipped; its first matching geometry input passes
    # straight through to its output, so downstream nodes see the upstream value.
    g = Graph.from_dict({
        "nodes": [
            {"id": "b", "type": "Box"},
            {"id": "f", "type": "Fillet", "bypassed": True},
            {"id": "m", "type": "Move"},
        ],
        "connections": [
            {"id": "1", "from_node": "b", "from_socket": "result", "to_node": "f", "to_socket": "part"},
            {"id": "2", "from_node": "f", "from_socket": "result", "to_node": "m", "to_socket": "shape"},
        ],
    })
    code = transpile(g)
    assert "__out_2 = __out_1" in code          # Fillet output == Box output
    assert "# bypassed" in code
    assert ".fillet" not in code.lower()         # the fillet op is not emitted


def test_bypass_flag_round_trips():
    g = Graph.from_dict({"nodes": [{"id": "f", "type": "Fillet", "bypassed": True}],
                         "connections": []})
    assert g.node("f").bypassed is True
    assert g.node("f").to_dict()["bypassed"] is True
    # default omitted when not bypassed
    g2 = Graph.from_dict({"nodes": [{"id": "b", "type": "Box"}], "connections": []})
    assert "bypassed" not in g2.node("b").to_dict()


def test_transpile_select_edge_and_targeted_fillet():
    # SelectEdge resolves its picked set at run time; FilletSelectedEdges rounds
    # only those edges (vs Fillet which rounds all).
    g = Graph.from_dict({
        "nodes": [
            {"id": "b", "type": "Box"},
            {"id": "s", "type": "SelectEdge",
             "params": {"selection": {"kind": "edge", "indices": [0, 2],
                                      "sigs": [[5, 0, 5, 10, 0, 0, 1]]}}},
            {"id": "fl", "type": "FilletSelectedEdges", "params": {"radius": 1.5}},
        ],
        "connections": [
            {"id": "1", "from_node": "b", "from_socket": "result", "to_node": "s", "to_socket": "geometry"},
            {"id": "2", "from_node": "b", "from_socket": "result", "to_node": "fl", "to_socket": "part"},
            {"id": "3", "from_node": "s", "from_socket": "selection", "to_node": "fl", "to_socket": "edges"},
        ],
    })
    code = transpile(g)
    assert "def _select_subshapes(" in code          # helper injected
    assert "_select_subshapes(__out_1, 'edge'" in code
    assert "[[5, 0, 5, 10, 0, 0, 1]]" in code        # signatures passed through
    assert "fillet(__out_2, radius=1.5)" in code      # operates on the selection var


def test_transpile_codeblock():
    g = Graph.from_dict({
        "nodes": [{"id": "cb", "type": "CodeBlock",
                   "params": {"code": "result = Box(in_0 or 4, 4, 4)"}}],
        "connections": [],
    })
    code = transpile(g)
    assert "def __codeblock_" in code
    assert "result = Box(in_0 or 4, 4, 4)" in code
    assert "return result" in code
