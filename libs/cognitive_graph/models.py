"""models.py — Modelos de datos para CognitiveGraph.

TokenIndex: índice invertido para búsqueda full-text.
IndexEntry: referencia a un nodo que contiene un token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class IndexEntry:
    """Una referencia a un nodo que contiene un token."""

    node_id: str  # ID del nodo en el grafo
    field: str  # Campo donde apareció: "objective", "description", etc.
    snippet: str  # Texto relevante (primeros 200 chars)
    score: float = 1.0

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "field": self.field,
            "snippet": self.snippet[:200],
            "score": self.score,
        }


_STOP_WORDS = {
    "the", "is", "in", "at", "of", "to", "for", "on", "and", "or",
    "with", "from", "by", "as", "an", "que", "de", "en", "la", "el",
    "los", "las", "del", "para", "con", "por", "se", "su", "lo", "un",
    "una", "al", "como", "mas", "pero", "task", "tarea", "implement",
    "implementar",
}


def tokenize(text: str) -> list[str]:
    """Divide texto en tokens significativos."""
    if not text:
        return []
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[-_/]", " ", text)
    tokens = re.findall(r"[a-zA-Záéíóúñ][a-zA-Záéíóúñ0-9]{1,}", text)
    result = []
    for t in tokens:
        t = t.lower().strip()
        if t not in _STOP_WORDS and len(t) >= 2:
            result.append(t)
    return result


@dataclass
class TokenIndex:
    """Índice invertido: token → lista de nodos que lo contienen.

    Permite búsqueda full-text sobre campos de nodos en el grafo.
    """

    entries: dict[str, list[IndexEntry]] = field(default_factory=dict)

    def add(self, token: str, entry: IndexEntry) -> None:
        token = token.lower().strip()
        if not token or len(token) < 2:
            return
        if token not in self.entries:
            self.entries[token] = []
        for existing in self.entries[token]:
            if existing.node_id == entry.node_id and existing.field == entry.field:
                return
        self.entries[token].append(entry)

    def index_node(self, node_id: str, text: str, field: str) -> None:
        """Tokeniza un texto y lo indexa para un nodo."""
        for token in tokenize(text):
            self.add(
                token,
                IndexEntry(node_id=node_id, field=field, snippet=text[:200], score=1.0),
            )

    def remove_node(self, node_id: str) -> int:
        """Elimina todas las entradas de un nodo (para reindex)."""
        removed = 0
        for token in list(self.entries.keys()):
            before = len(self.entries[token])
            self.entries[token] = [
                e for e in self.entries[token] if e.node_id != node_id
            ]
            removed += before - len(self.entries[token])
            if not self.entries[token]:
                del self.entries[token]
        return removed

    def search(self, query: str, max_results: int = 20) -> list[IndexEntry]:
        """Busca tokens que coincidan con la query (exacto + substring)."""
        tokens = tokenize(query)
        if not tokens:
            return []
        results: dict[str, IndexEntry] = {}

        for token in tokens:
            # Match exacto
            if token in self.entries:
                for entry in self.entries[token]:
                    key = f"{entry.node_id}:{entry.field}"
                    if key not in results:
                        results[key] = entry
                        results[key].score = entry.score * 3.0
                    else:
                        results[key].score += entry.score * 3.0

            # Match substring
            for idx_token, entries in self.entries.items():
                if token in idx_token and idx_token != token:
                    for entry in entries:
                        key = f"{entry.node_id}:{entry.field}"
                        if key not in results:
                            results[key] = entry
                            results[key].score = entry.score * 0.5
                        else:
                            results[key].score += entry.score * 0.5

        sorted_results = sorted(results.values(), key=lambda x: x.score, reverse=True)
        return sorted_results[:max_results]

    def to_dict(self) -> dict:
        return {
            token: [e.to_dict() for e in entries]
            for token, entries in self.entries.items()
        }

    @classmethod
    def from_dict(cls, data: dict) -> TokenIndex:
        idx = cls()
        for token, entries_raw in data.items():
            for e in entries_raw:
                idx.add(
                    token,
                    IndexEntry(
                        node_id=e.get("node_id", ""),
                        field=e.get("field", ""),
                        snippet=e.get("snippet", ""),
                        score=e.get("score", 1.0),
                    ),
                )
        return idx
