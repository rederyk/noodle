"""
Pure-Python tests for the persistent memo cache (transpile(memo=True)).

The memo layer is a transpiler feature: each cacheable node is wrapped in a
_memo_get/_memo_put lookup keyed by a content hash. These tests check the
STATIC properties the cache's correctness rests on — key determinism, exact
dirty-subtree invalidation, var-renumbering immunity, and poisoning of
non-deterministic lineages — without executing any build123d.

Run: python -m pytest tests/ -v
"""

from cad_nodes.graph import Graph
from cad_nodes.transpiler import Transpiler, transpile


def _chain(fillet_radius=1.0, box_size=10.0):
    """Box -> Fillet -> Sphere-Subtract: a 3-node chain + a parallel Sphere."""
    return Graph.from_dict({
        "nodes": [
            {"id": "box", "type": "Box",
             "params": {"width": box_size, "height": 10, "depth": 10}},
            {"id": "fil", "type": "Fillet", "params": {"radius": fillet_radius}},
            {"id": "sph", "type": "Sphere", "params": {"radius": 3}},
            {"id": "sub", "type": "Subtract", "params": {}},
        ],
        "connections": [
            {"id": "l1", "from_node": "box", "from_socket": "result",
             "to_node": "fil", "to_socket": "part"},
            {"id": "l2", "from_node": "fil", "from_socket": "result",
             "to_node": "sub", "to_socket": "a"},
            {"id": "l3", "from_node": "sph", "from_socket": "result",
             "to_node": "sub", "to_socket": "b"},
        ],
    })


def _keys(graph):
    t = Transpiler(graph, memo=True)
    t.run()
    return t.key_of


def test_memo_off_by_default():
    src = transpile(_chain())
    assert "_m = _memo_get(" not in src          # node wrapping is memo-only
    assert "__hashes__.update(" not in src


def test_memo_wraps_nodes_and_emits_hashes():
    src = transpile(_chain(), memo=True)
    assert src.count("_m = _memo_get(") == 4     # all four nodes cacheable
    assert "__hashes__.update(" in src
    assert "'__result__'" in src.split("__hashes__.update")[1]


def test_keys_are_deterministic():
    assert _keys(_chain()) == _keys(_chain())


def test_param_edit_dirties_only_downstream():
    base, edited = _keys(_chain()), _keys(_chain(fillet_radius=2.0))
    assert base["box"] == edited["box"]          # upstream untouched
    assert base["sph"] == edited["sph"]          # parallel branch untouched
    assert base["fil"] != edited["fil"]          # edited node
    assert base["sub"] != edited["sub"]          # downstream of the edit


def test_root_edit_dirties_chain_not_parallel_branch():
    base, edited = _keys(_chain()), _keys(_chain(box_size=20.0))
    assert base["sph"] == edited["sph"]
    for nid in ("box", "fil", "sub"):
        assert base[nid] != edited[nid]


def test_keys_survive_var_renumbering():
    """Adding an unrelated node shifts every __out_N var name; content keys
    must not change (else any edit would flush the whole cache)."""
    base = _keys(_chain())
    g = _chain()
    d = g.to_dict()
    d["nodes"].insert(0, {"id": "zzz", "type": "Box", "params": {"width": 1}})
    grown = _keys(Graph.from_dict(d))
    for nid in ("box", "fil", "sph", "sub"):
        assert base[nid] == grown[nid]


def test_import_poisons_lineage():
    """A file-reading node is non-deterministic (the file can change on disk):
    no key for it NOR for anything downstream of it."""
    g = Graph.from_dict({
        "nodes": [
            {"id": "imp", "type": "ImportSTEP", "params": {"path": "a.step"}},
            {"id": "fil", "type": "Fillet", "params": {"radius": 1}},
        ],
        "connections": [
            {"id": "l1", "from_node": "imp", "from_socket": "result",
             "to_node": "fil", "to_socket": "part"},
        ],
    })
    keys = _keys(g)
    assert "imp" not in keys
    assert "fil" not in keys


def test_codeblock_is_keyed_and_code_edit_invalidates():
    def cb_graph(code):
        return Graph.from_dict({
            "nodes": [{"id": "cb", "type": "CodeBlock", "params": {"code": code}}],
            "connections": [],
        })
    k1 = _keys(cb_graph("result = Box(1, 1, 1)"))
    k2 = _keys(cb_graph("result = Box(1, 1, 1)"))
    k3 = _keys(cb_graph("result = Box(2, 2, 2)"))
    assert "cb" in k1 and k1 == k2
    assert k1["cb"] != k3["cb"]


def test_preview_toggle_keeps_key():
    """The eye toggle must not invalidate the cache (previews re-run outside
    the memoized body)."""
    g1, g2 = _chain(), _chain()
    g2.node("fil").preview = True
    assert _keys(g1)["fil"] == _keys(g2)["fil"]
    src = transpile(g2, memo=True)
    # the preview assignment runs on BOTH branches of the cache lookup
    line = next(l for l in src.splitlines() if l.strip().startswith("__previews__['fil']"))
    assert line.startswith("    ")               # inside try, outside if/else


def test_sidefx_node_keyed_but_not_wrapped():
    """A Display probe records its input every run (side effect) yet must not poison
    the lineage of what flows through it."""
    g = Graph.from_dict({
        "nodes": [
            {"id": "box", "type": "Box", "params": {}},
            {"id": "pan", "type": "Display", "params": {}},
        ],
        "connections": [
            {"id": "l1", "from_node": "box", "from_socket": "result",
             "to_node": "pan", "to_socket": "value"},
        ],
    })
    t = Transpiler(g, memo=True)
    src = t.run()
    assert "pan" in t.key_of                     # lineage continues
    probe_stmt = next(l for l in src.splitlines() if "_probe(" in l and "@node:pan" in l)
    assert probe_stmt is not None                # emitted...
    assert "_memo_get" not in probe_stmt         # ...but re-runs every time
