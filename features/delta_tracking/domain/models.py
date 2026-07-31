"""
delta_tracking/models.py — Modelos para el índice de contexto federado.

Cada entrada del índice asocia un token (palabra/clave) con una lista
de referencias a objetos que lo contienen.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IndexEntry:
    """Una referencia a un objeto que contiene un token."""

    source: str  # "task" | "ckg" | "tracelog" | "file"
    source_id: str  # ID del objeto: task_id, ckg node id, etc.
    field: str  # Campo donde aparecio: "objective", "predicate", etc.
    snippet: str  # Texto relevante (primeros 200 chars)
    score: float = 1.0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "field": self.field,
            "snippet": self.snippet[:200],
            "score": self.score,
        }


@dataclass
class TokenIndex:
    """Índice invertido: token → lista de entries."""

    entries: dict[str, list[IndexEntry]] = field(default_factory=dict)

    def add(self, token: str, entry: IndexEntry) -> None:
        token = token.lower().strip()
        if not token or len(token) < 2:
            return
        if token not in self.entries:
            self.entries[token] = []
        # Evitar duplicados exactos
        for existing in self.entries[token]:
            if (
                existing.source == entry.source
                and existing.source_id == entry.source_id
                and existing.field == entry.field
            ):
                return
        self.entries[token].append(entry)

    def search(self, query: str, max_results: int = 10) -> list[IndexEntry]:
        """Busca tokens que coincidan con la query (exacto + substring)."""
        tokens = self._tokenize(query)
        results: dict[str, IndexEntry] = {}

        for token in tokens:
            # Match exacto
            if token in self.entries:
                for entry in self.entries[token]:
                    key = f"{entry.source}:{entry.source_id}:{entry.field}"
                    if key not in results:
                        results[key] = entry
                        results[key].score = entry.score * 3.0
                    else:
                        results[key].score += entry.score * 3.0

            # Match substring
            for idx_token, entries in self.entries.items():
                if token in idx_token and idx_token != token:
                    for entry in entries:
                        key = f"{entry.source}:{entry.source_id}:{entry.field}"
                        if key not in results:
                            results[key] = entry
                            results[key].score = entry.score * 0.5
                        else:
                            results[key].score += entry.score * 0.5

        # Ordenar por score descendente
        sorted_results = sorted(results.values(), key=lambda x: x.score, reverse=True)
        return sorted_results[:max_results]

    def search_by_source(self, source: str, source_id: str) -> list[IndexEntry]:
        """Recupera todas las entradas de un objeto especifico."""
        results = []
        for token, entries in self.entries.items():
            for entry in entries:
                if entry.source == source and entry.source_id == source_id:
                    results.append(entry)
        return results

    def remove_source(self, source: str, source_id: str) -> int:
        """Elimina todas las entradas de un objeto (para reindex)."""
        removed = 0
        for token in list(self.entries.keys()):
            before = len(self.entries[token])
            self.entries[token] = [
                e
                for e in self.entries[token]
                if not (e.source == source and e.source_id == source_id)
            ]
            removed += before - len(self.entries[token])
            if not self.entries[token]:
                del self.entries[token]
        return removed

    def to_dict(self) -> dict:
        return {
            token: [e.to_dict() for e in entries]
            for token, entries in self.entries.items()
        }

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Divide texto en tokens significativos."""
        import re

        # Separar por espacios, guiones, barras, camelCase
        text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        text = re.sub(r"[-_/]", " ", text)
        tokens = re.findall(r"[a-zA-Záéíóúñ][a-zA-Záéíóúñ0-9]{1,}", text)
        # Normalizar
        result = []
        for t in tokens:
            t = t.lower().strip()
            # Ignorar tokens muy comunes
            if t not in (
                "the",
                "is",
                "in",
                "at",
                "of",
                "to",
                "for",
                "on",
                "and",
                "or",
                "with",
                "from",
                "by",
                "as",
                "an",
                "que",
                "de",
                "en",
                "la",
                "el",
                "los",
                "las",
                "del",
                "para",
                "con",
                "por",
                "se",
                "su",
                "lo",
                "un",
                "una",
                "al",
                "lo",
                "como",
                "mas",
                "pero",
                "task",
                "tarea",
                "implement",
                "implementar",
            ):
                result.append(t)
        return result
