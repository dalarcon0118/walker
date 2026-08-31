"""domain/query.py — Query value objects for CPG queries."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Filter:
    """A single filter key=value pair."""
    key: str
    value: str


@dataclass
class Query:
    """A parsed CPG query with filters and optional free text."""
    filters: list[Filter] = field(default_factory=list)
    free_text: str | None = None

    @classmethod
    def parse(cls, query_text: str) -> Query:
        """Parse 'bug:unused_functions file:query.py' into Filters."""
        filters: list[Filter] = []
        free_text: str | None = None
        for part in query_text.strip().split():
            if ":" in part:
                key, _, val = part.partition(":")
                if key and val:
                    filters.append(Filter(key, val))
            else:
                free_text = part if free_text is None else free_text + " " + part
        return cls(filters, free_text)

    def get(self, key: str) -> str | None:
        for f in self.filters:
            if f.key == key:
                return f.value
        return None

    def exclude_keys(self, keys: set[str]) -> list[Filter]:
        return [f for f in self.filters if f.key not in keys]

    def to_query_text(self) -> str:
        parts = [f"{f.key}:{f.value}" for f in self.filters]
        if self.free_text:
            parts.append(self.free_text)
        return " ".join(parts)


@dataclass
class QueryResult:
    """Result of a CPG query."""
    query: str
    total: int
    matches: list[dict]
    truncated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "total": self.total,
            "matches": self.matches[:50],
            "truncated": self.truncated or self.total > 50,
            **self.extra,
        }
