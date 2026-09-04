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
    """Índice invertido con bigramas para busqueda full-text O(1).

    entries: token → lista de IndexEntry (match exacto)
    _bigrams: bigrama → set de tokens (substring O(1) via interseccion)
    """

    entries: dict[str, list[IndexEntry]] = field(default_factory=dict)
    _bigrams: dict[str, set[str]] = field(default_factory=dict)
    # (token, node_id, field) ya indexados — dedup O(1) en add()
    _keys: set = field(default_factory=set)

    # ── Helpers de bigramas ──

    @staticmethod
    def _bigrams_of(token: str) -> set[str]:
        """Bigramas de un token (ej: 'auth' → {'au','ut','th'})."""
        return {token[i:i + 2] for i in range(len(token) - 1)} if len(token) >= 2 else set()

    def _add_bigrams(self, token: str) -> None:
        """Registra bigramas de un token en el indice."""
        for bg in self._bigrams_of(token):
            self._bigrams.setdefault(bg, set()).add(token)

    def _remove_bigrams(self, token: str) -> None:
        """Elimina bigramas de un token. Si el set queda vacio, borra la key."""
        for bg in self._bigrams_of(token):
            if bg in self._bigrams:
                self._bigrams[bg].discard(token)
                if not self._bigrams[bg]:
                    del self._bigrams[bg]

    # ── CRUD ──

    def add(self, token: str, entry: IndexEntry) -> None:
        token = token.lower().strip()
        if not token or len(token) < 2:
            return
        key = (token, entry.node_id, entry.field)
        if key in self._keys:
            # (token, node_id, field) ya indexado — dedup O(1).
            # Antes: scan lineal de entries[token] → O(n²) acumulado en
            # re-index masivo (37K nodos × tokens comunes = horas de CPU).
            return
        if token not in self.entries:
            self.entries[token] = []
        self.entries[token].append(entry)
        self._keys.add(key)
        self._add_bigrams(token)

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
            for e in self.entries[token]:
                if e.node_id == node_id:
                    self._keys.discard((token, e.node_id, e.field))
            self.entries[token] = [
                e for e in self.entries[token] if e.node_id != node_id
            ]
            after = len(self.entries[token])
            removed += before - after
            if not self.entries[token]:
                del self.entries[token]
                self._remove_bigrams(token)
        return removed

    def search(
        self, query: str, max_results: int = 20, fields: list[str] | None = None,
    ) -> list[IndexEntry]:
        """Busca tokens que coincidan con la query.

        - Match exacto: O(1) lookup via entries[token]
        - Match substring: O(1) via indice de bigramas (interseccion de sets)
          en vez de O(|entries|) scan lineal por token
        - fields: filtro opcional por campo (E1: field-scoped search)
        """
        tokens = tokenize(query)
        if not tokens:
            return []
        results: dict[str, IndexEntry] = {}

        for token in tokens:
            # Match exacto: O(1)
            if token in self.entries:
                for entry in self.entries[token]:
                    if fields is not None and entry.field not in fields:
                        continue
                    key = f"{entry.node_id}:{entry.field}"
                    if key not in results:
                        results[key] = entry
                        results[key].score = entry.score * 3.0
                    else:
                        results[key].score += entry.score * 3.0

            # Match substring via bigramas: O(1) interseccion
            query_bigrams = self._bigrams_of(token)
            if not query_bigrams:
                continue
            candidate_tokens = None
            for bg in query_bigrams:
                matched = self._bigrams.get(bg, set())
                if candidate_tokens is None:
                    candidate_tokens = set(matched)
                else:
                    candidate_tokens &= matched
                if not candidate_tokens:
                    break
            if candidate_tokens:
                for idx_token in candidate_tokens:
                    if idx_token == token or token not in idx_token:
                        continue
                    for entry in self.entries.get(idx_token, []):
                        if fields is not None and entry.field not in fields:
                            continue
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
