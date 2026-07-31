"""domain/graph.py — Core CPG entities: Node, Edge, Graph."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    """A node in the Code Property Graph."""
    id: str
    type: str          # file, function, method, class, module, variable, ...
    name: str
    path: str          # file path
    line: int = 0
    kind: str = ""
    signature: str = ""
    is_import: bool = False
    ast_kind: str = ""
    cyclo: int | None = None
    loc: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Node:
        return cls(
            id=data.get("id", ""),
            type=data.get("type", ""),
            name=data.get("name", ""),
            path=data.get("path", ""),
            line=data.get("line", 0),
            kind=data.get("kind", ""),
            signature=data.get("signature", ""),
            is_import=data.get("is_import", False),
            ast_kind=data.get("ast_kind", ""),
            cyclo=data.get("cyclo"),
            loc=data.get("loc"),
            extra={k: v for k, v in data.items()
                   if k not in ("id", "type", "name", "path", "line", "kind",
                                "signature", "is_import", "ast_kind", "cyclo", "loc")},
        )

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "type": self.type, "name": self.name,
            "path": self.path, "line": self.line, "kind": self.kind,
            "signature": self.signature, "is_import": self.is_import,
            "ast_kind": self.ast_kind,
        }
        if self.cyclo is not None:
            d["cyclo"] = self.cyclo
        if self.loc is not None:
            d["loc"] = self.loc
        d.update(self.extra)
        return d


@dataclass
class Edge:
    """An edge connecting two nodes."""
    from_id: str
    to_id: str
    type: str  # CONTAINS, IMPORTS, CALLS, FLOWS_TO, ...
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Edge:
        return cls(
            from_id=data.get("from", ""),
            to_id=data.get("to", ""),
            type=data.get("type", ""),
        )

    def to_dict(self) -> dict:
        return {"from": self.from_id, "to": self.to_id, "type": self.type, **self.extra}


class Graph:
    """Graph container: nodes + edges + derived indexes."""

    def __init__(self, nodes: dict[str, Node] | None = None,
                 edges: list[Edge] | None = None):
        self._nodes: dict[str, Node] = nodes or {}
        self._edges: list[Edge] = edges or []
        self._outgoing: dict[str, list[dict]] = {}
        self._incoming: dict[str, list[dict]] = {}
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._outgoing.clear()
        self._incoming.clear()
        for e in self._edges:
            f = e.from_id
            t = e.to_id
            self._outgoing.setdefault(f, []).append({"to": t, "type": e.type})
            self._incoming.setdefault(t, []).append({"from": f, "type": e.type})

    @property
    def nodes(self) -> dict[str, Node]:
        return self._nodes

    @property
    def edges(self) -> list[Edge]:
        return self._edges

    @property
    def outgoing(self) -> dict[str, list[dict]]:
        return self._outgoing

    @property
    def incoming(self) -> dict[str, list[dict]]:
        return self._incoming

    def get_node(self, nid: str) -> Node | None:
        return self._nodes.get(nid)

    def add_node(self, node: Node) -> None:
        self._nodes[node.id] = node

    def add_edge(self, edge: Edge) -> None:
        self._edges.append(edge)

    def get_outgoing(self, nid: str) -> list[dict]:
        return self._outgoing.get(nid, [])

    def get_incoming(self, nid: str) -> list[dict]:
        return self._incoming.get(nid, [])

    @classmethod
    def from_dict(cls, data: dict) -> Graph:
        raw_nodes = data.get("nodes", {})
        nodes = {nid: Node.from_dict(n) for nid, n in raw_nodes.items()}
        edges = [Edge.from_dict(e) for e in data.get("edges", [])]
        return cls(nodes, edges)

    def to_dict(self) -> dict:
        return {
            "nodes": {nid: n.to_dict() for nid, n in self._nodes.items()},
            "edges": [e.to_dict() for e in self._edges],
        }
