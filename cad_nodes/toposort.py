"""
Kahn topological sort over the node graph DAG, with cycle detection.
"""

from __future__ import annotations


class CycleError(Exception):
    """Raised when the graph contains a cycle (not a DAG)."""

    def __init__(self, remaining: list[str]):
        self.remaining = remaining
        super().__init__(f"Graph has a cycle involving nodes: {sorted(remaining)}")


def toposort(node_ids: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """
    Return node ids in dependency order (sources first).

    edges: list of (from_node_id, to_node_id) meaning from -> to (to depends on from).
    Order is deterministic: ready nodes are emitted in their original `node_ids` order.
    """
    ids = list(node_ids)
    indegree: dict[str, int] = {n: 0 for n in ids}
    adj: dict[str, list[str]] = {n: [] for n in ids}

    for src, dst in edges:
        if src not in indegree or dst not in indegree:
            # Edge references an unknown node — caller should validate first.
            raise KeyError(f"Edge references unknown node: {src} -> {dst}")
        adj[src].append(dst)
        indegree[dst] += 1

    # Stable queue: preserve original node order among the ready set.
    ready = [n for n in ids if indegree[n] == 0]
    order: list[str] = []

    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in adj[n]:
            indegree[m] -= 1
            if indegree[m] == 0:
                # Insert keeping original-order stability.
                _insert_stable(ready, m, ids)

    if len(order) != len(ids):
        remaining = [n for n in ids if n not in set(order)]
        raise CycleError(remaining)

    return order


def _insert_stable(ready: list[str], item: str, ids: list[str]) -> None:
    """Insert `item` into `ready` keeping it sorted by index in `ids`."""
    rank = ids.index(item)
    for i, existing in enumerate(ready):
        if ids.index(existing) > rank:
            ready.insert(i, item)
            return
    ready.append(item)
