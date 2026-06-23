"""
Graph model: Node, Connection, Graph + JSON (de)serialisation + validation.

Graph JSON shape:
{
  "name": "flange",
  "nodes": [
    {"id": "n1", "type": "Circle", "params": {"radius": 20}, "position": [0, 0],
     "parent": null},
    ...
  ],
  "connections": [
    {"id": "c1", "from_node": "n1", "from_socket": "result",
     "to_node": "n2", "to_socket": "sketch"},
    ...
  ]
}

`parent` (optional) links a node to a group node (BuildPart/BuildSketch) for
builder-mode nesting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import catalog
from .catalog import wires_compatible


@dataclass
class Node:
    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    position: tuple[float, float] = (0.0, 0.0)
    parent: Optional[str] = None  # id of a group node, if nested
    preview: bool = True          # draw this node's result in the live preview

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "params": self.params,
            "position": list(self.position),
            "parent": self.parent,
            "preview": self.preview,
        }

    @staticmethod
    def from_dict(d: dict) -> "Node":
        pos = d.get("position", [0, 0])
        return Node(
            id=d["id"],
            type=d["type"],
            params=dict(d.get("params", {})),
            position=(pos[0], pos[1]) if pos else (0.0, 0.0),
            parent=d.get("parent"),
            preview=d.get("preview", True),
        )


@dataclass
class Connection:
    id: str
    from_node: str
    from_socket: str
    to_node: str
    to_socket: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from_node": self.from_node,
            "from_socket": self.from_socket,
            "to_node": self.to_node,
            "to_socket": self.to_socket,
        }

    @staticmethod
    def from_dict(d: dict) -> "Connection":
        return Connection(
            id=d["id"],
            from_node=d["from_node"],
            from_socket=d["from_socket"],
            to_node=d["to_node"],
            to_socket=d["to_socket"],
        )


class ValidationError(Exception):
    pass


@dataclass
class Graph:
    name: str = "untitled"
    nodes: list[Node] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)

    # -- lookups -----------------------------------------------------------
    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"No node {node_id!r} in graph")

    def edges(self) -> list[tuple[str, str]]:
        return [(c.from_node, c.to_node) for c in self.connections]

    def inputs_of(self, node_id: str) -> dict[str, list[tuple[str, str]]]:
        """socket_name -> list of (from_node_id, from_socket) feeding it."""
        result: dict[str, list[tuple[str, str]]] = {}
        for c in self.connections:
            if c.to_node == node_id:
                result.setdefault(c.to_socket, []).append((c.from_node, c.from_socket))
        return result

    def children_of(self, group_id: str) -> list[Node]:
        return [n for n in self.nodes if n.parent == group_id]

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes],
            "connections": [c.to_dict() for c in self.connections],
        }

    @staticmethod
    def from_dict(d: dict) -> "Graph":
        return Graph(
            name=d.get("name", "untitled"),
            nodes=[Node.from_dict(n) for n in d.get("nodes", [])],
            connections=[Connection.from_dict(c) for c in d.get("connections", [])],
        )

    # -- validation --------------------------------------------------------
    def validate(self) -> list[str]:
        """
        Raise ValidationError on hard problems (unknown type/socket, type
        mismatch, duplicate ids). Return a list of soft warnings (e.g. missing
        required inputs) that don't prevent transpilation.
        """
        warnings: list[str] = []

        seen_ids: set[str] = set()
        for n in self.nodes:
            if n.id in seen_ids:
                raise ValidationError(f"Duplicate node id: {n.id}")
            seen_ids.add(n.id)
            if n.type not in catalog.REGISTRY:
                raise ValidationError(f"Unknown node type {n.type!r} (node {n.id})")
            if n.parent is not None and n.parent not in {x.id for x in self.nodes}:
                raise ValidationError(f"Node {n.id} has unknown parent {n.parent!r}")

        node_by_id = {n.id: n for n in self.nodes}

        for c in self.connections:
            if c.from_node not in node_by_id:
                raise ValidationError(f"Connection {c.id}: unknown from_node {c.from_node!r}")
            if c.to_node not in node_by_id:
                raise ValidationError(f"Connection {c.id}: unknown to_node {c.to_node!r}")

            src_def = catalog.get(node_by_id[c.from_node].type)
            dst_def = catalog.get(node_by_id[c.to_node].type)

            out = src_def.output(c.from_socket)
            inp = dst_def.input(c.to_socket)
            if out is None:
                raise ValidationError(
                    f"Connection {c.id}: node {c.from_node} ({src_def.type}) "
                    f"has no output {c.from_socket!r}")
            if inp is None:
                raise ValidationError(
                    f"Connection {c.id}: node {c.to_node} ({dst_def.type}) "
                    f"has no input {c.to_socket!r}")
            if not wires_compatible(out.wire_type, inp.wire_type):
                raise ValidationError(
                    f"Connection {c.id}: incompatible wire types "
                    f"{out.wire_type} -> {inp.wire_type}")

        # Soft: required inputs that are unconnected.
        for n in self.nodes:
            ndef = catalog.get(n.type)
            connected = set(self.inputs_of(n.id).keys())
            for s in ndef.inputs:
                if s.required and s.name not in connected:
                    warnings.append(
                        f"Node {n.id} ({n.type}): required input {s.name!r} is unconnected")

        return warnings
