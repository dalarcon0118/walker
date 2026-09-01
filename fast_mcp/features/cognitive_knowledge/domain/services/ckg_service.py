"""domain/services/ckg_service.py — Domain port for CKG services.

Defines the contract for CKG operations (ICkgService).
Implemented by WalkerCkgAdapter in infrastructure/adapters/.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ICkgService(ABC):
    """Contract for main CKG operations."""

    @abstractmethod
    def save_knowledge(self, nodes: list[dict], project_id: str = "") -> dict[str, Any]:
        """Save knowledge nodes to the CKG."""
        ...

    @abstractmethod
    def search_knowledge(self, query: str, max_results: int = 20) -> list[dict[str, Any]]:
        """Search knowledge by intent."""
        ...

    @abstractmethod
    def get_active_knowledge(self, type_name: str = "") -> list[dict[str, Any]]:
        """Get active (non-superseded) nodes."""
        ...

    @abstractmethod
    def supersede_decision(self, old_node_id: str, new_node_id: str, statement: str = "") -> dict[str, Any]:
        """Mark a decision as superseded."""
        ...

    @abstractmethod
    def get_knowledge_for_files(self, files: list[str], depth: int = 2) -> list[dict[str, Any]]:
        """Get knowledge relevant to specific files."""
        ...
