"""CKG — Code Knowledge Graph (software development knowledge).

Lazy facade (PEP 562 — ADR-WALKER-053): implementation lives in
application/ and infrastructure/; this init does NOT eagerly import anything
(circular dependency with cpg_service). Public API:

    from fast_mcp.features.cognitive_knowledge import (
        CkgApplicationService,
        ckg_service,
        ckg_repository,
        ckg_analysis_adapter,
    )

Composition root: ckg_service() centralizes graph construction
(patrol mirror of code_analysis.cpg_service). Consumers import from the
package root, NEVER from internal paths (application/, infrastructure/).
"""
from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    "CkgApplicationService": "fast_mcp.features.cognitive_knowledge.application.services.ckg_service",
    "ckg_service": "fast_mcp.features.cognitive_knowledge._composition",
    "ckg_repository": "fast_mcp.features.cognitive_knowledge._composition",
    "ckg_analysis_adapter": "fast_mcp.features.cognitive_knowledge._composition",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {name!r} not found")
    import importlib

    return getattr(importlib.import_module(target), name)


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_EXPORTS))
