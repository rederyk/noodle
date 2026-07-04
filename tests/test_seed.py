"""
Tests for first-run example seeding (GraphStore.seed_examples) — pure-Python,
no build123d required. Verifies a fresh install lands on real graphs while an
existing / already-seeded store is left untouched.

Run: python -m pytest tests/ -v
"""

from cad_nodes.store import EXAMPLES_DIR, GraphStore


def _bundled_names():
    return sorted(p.stem for p in EXAMPLES_DIR.glob("*.json"))


def test_bundled_examples_exist():
    # Guard the onboarding contract: shipping zero examples would silently
    # regress a new user back to a blank canvas.
    assert _bundled_names(), "expected at least one bundled example graph"


def test_seed_populates_empty_store(tmp_path):
    store = GraphStore(tmp_path)
    seeded = store.seed_examples()
    assert seeded == _bundled_names()
    # every seeded graph is a real, loadable project with a description
    for name in seeded:
        assert store.exists(name)
        store.load(name)  # from_dict must not raise
        meta = (tmp_path / name / "meta.json")
        assert meta.exists()
    assert (tmp_path / ".seeded").exists()


def test_seed_is_idempotent(tmp_path):
    store = GraphStore(tmp_path)
    first = store.seed_examples()
    assert first
    assert store.seed_examples() == []          # marker present -> no-op
    assert store.list() == first                 # nothing added or removed


def test_seed_skips_when_a_project_already_exists(tmp_path):
    store = GraphStore(tmp_path)
    from cad_nodes.graph import Graph
    store.save("mine", Graph.from_dict({"name": "mine", "nodes": [], "connections": []}))
    assert store.seed_examples() == []           # user already has work -> hands off


def test_seed_not_repeated_after_user_deletes_examples(tmp_path):
    store = GraphStore(tmp_path)
    for name in store.seed_examples():
        store.delete(name)
    # marker persists, so a user who cleared the examples is not re-seeded
    assert store.seed_examples() == []
