"""
Tests for the application layer (cad_nodes.api) — no build123d / mcp required.
Execution/export are covered end-to-end in Docker, not here.

Run: python -m pytest tests/ -v
"""

import pytest

from cad_nodes import api
from cad_nodes.graph import ValidationError
from cad_nodes.store import GraphStore


@pytest.fixture
def store(tmp_path):
    return GraphStore(tmp_path)


def test_create_and_list_graph(store):
    gid = api.create_graph(store, "part1", "a test part")
    assert gid == "part1"
    assert api.list_graphs(store) == ["part1"]
    with pytest.raises(ValueError):
        api.create_graph(store, "part1")  # duplicate


def test_add_node_generates_unique_ids(store):
    api.create_graph(store, "g")
    a = api.add_node(store, "g", "Box", {"width": 5})
    b = api.add_node(store, "g", "Box")
    assert a == "box_1" and b == "box_2"
    graph = store.load("g")
    assert graph.node("box_1").params["width"] == 5


def test_add_unknown_node_type(store):
    api.create_graph(store, "g")
    assert "error" not in str(api.add_node.__doc__)  # sanity
    with pytest.raises(ValueError):
        api.add_node(store, "g", "DoesNotExist")


def test_connect_validates_wiring(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "Circle", {"radius": 10})
    api.add_node(store, "g", "Extrude", {"amount": 5})
    cid = api.connect(store, "g", "circle_1", "result", "extrude_1", "sketch")
    assert cid == "c1"
    # Bad socket -> validation error, connection not persisted.
    with pytest.raises(ValidationError):
        api.connect(store, "g", "circle_1", "result", "extrude_1", "nope")
    assert len(store.load("g").connections) == 1


def test_set_param_and_code(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "Box")
    assert api.set_param(store, "g", "box_1", {"width": 42})
    assert store.load("g").node("box_1").params["width"] == 42

    api.add_node(store, "g", "CodeBlock")
    assert api.set_code(store, "g", "codeblock_1", "result = Sphere(3)")
    assert store.load("g").node("codeblock_1").params["code"] == "result = Sphere(3)"
    with pytest.raises(ValueError):
        api.set_code(store, "g", "box_1", "x = 1")  # not a CodeBlock


def test_delete_node_removes_connections(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "Circle")
    api.add_node(store, "g", "Extrude")
    api.connect(store, "g", "circle_1", "result", "extrude_1", "sketch")
    api.delete_node(store, "g", "circle_1")
    g = store.load("g")
    assert all(n.id != "circle_1" for n in g.nodes)
    assert g.connections == []


def test_get_code_roundtrip(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "Box", {"width": 1, "height": 2, "depth": 3})
    code = api.get_code(store, "g")
    assert "Box(1.0, 2.0, 3.0, align=_al(True))" in code
    assert "__result__" in code


def test_catalog(store):
    cats = api.list_catalog("boolean")
    assert all(n["category"] == "boolean" for n in cats)
    assert api.get_node_def("Box")["type"] == "Box"


# --- patch_param: clamp + CodeBlock override (code-view editing) ----------
def test_patch_param_clamps_builtin(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "Box", {"width": 10})
    assert api.patch_param(store, "g", "box_1", "width", 9999) == 500.0   # max
    assert api.patch_param(store, "g", "box_1", "width", -5) == 0.1       # min
    assert store.load("g").node("box_1").params["width"] == 0.1


def test_patch_param_int_coerced(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "ArrayLinear", {})   # `count` is an int param
    # a float string lands as a rounded int, clamped to range
    assert api.patch_param(store, "g", "arraylinear_1", "count", "5.7") == 6
    assert api.patch_param(store, "g", "arraylinear_1", "count", 999) == 200


def test_patch_param_unknown_raises(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "Box")
    with pytest.raises(ValueError):
        api.patch_param(store, "g", "box_1", "nope", 1)


def test_patch_codeblock_override(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "CodeBlock",
                 {"code": "teeth = 12 #@param int min=6 max=40\nresult=None"})
    assert api.patch_param(store, "g", "codeblock_1", "_cb.teeth", 99) == 40  # clamp
    assert store.load("g").node("codeblock_1").params["_cb"] == {"teeth": 40}
    schema = api.scan_codeblock(store, "g", "codeblock_1")
    assert schema[0]["name"] == "teeth" and schema[0]["value"] == 40


def test_patch_codeblock_unknown_param(store):
    api.create_graph(store, "g")
    api.add_node(store, "g", "CodeBlock", {"code": "result=None"})
    with pytest.raises(ValueError):
        api.patch_param(store, "g", "codeblock_1", "_cb.ghost", 1)
