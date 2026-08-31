"""domain/values.py — Value Objects del dominio CKG.

VOs que representan conceptos del dominio de cognitive_knowledge sin identidad propia.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True)
class CkgNodeId:
    """Identificador de nodo en el CKG."""
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("CkgNodeId no puede ser vacio")


@dataclass(frozen=True)
class CkgEdgeId:
    """Identificador de edge en el CKG."""
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("CkgEdgeId no puede ser vacio")


@dataclass(frozen=True)
class SearchFilters:
    """Filtros de busqueda en el CKG."""
    query: str = ""
    node_type: str = ""
    file: str = ""
    task_id: str = ""
    max_results: int = 20
    include_superseded: bool = False

    def __post_init__(self) -> None:
        if self.max_results < 1:
            raise ValueError("SearchFilters.max_results debe ser >= 1")


@dataclass(frozen=True)
class KnowledgeScope:
    """Alcance de conocimiento: project, sprint, task."""
    value: str

    def __post_init__(self) -> None:
        valid = {"project", "sprint", "task", "global"}
        if self.value not in valid:
            raise ValueError(f"KnowledgeScope invalido: {self.value}. Validos: {sorted(valid)}")
