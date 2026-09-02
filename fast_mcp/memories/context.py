"""fast_mcp/memories/context.py — Resolved context for memory tool handlers.

Every handler receives a MemoryContext instead of re-resolving
ProjectService.resolve_paths() independently. This eliminates the
DRY violation (8+ duplicate resolve_paths calls) and makes dependencies
explicit and testable.

Usage:
    ctx = MemoryContext.resolve(chain_dir)
    if ctx is None:
        return {"error": "No project context"}
    result = read_code_handler(ctx, file="src/main.py")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryContext:
    """Resolved context for a project. Immutable after construction."""

    chain_dir: str
    project_root: str
    project_id: str

    # Lazily resolved services (avoid import-time construction)
    _ckg_factory: Callable[[str], Any] | None = field(default=None, repr=False)
    _cpg_factory: Callable[[str], Any] | None = field(default=None, repr=False)

    @classmethod
    def resolve(cls, chain_dir: str) -> MemoryContext | None:
        """Resolve context from chain_dir. Returns None if unresolvable."""
        if not chain_dir:
            return None
        try:
            from fast_mcp.shared_kernel.project_support import ProjectService

            paths = ProjectService.resolve_paths(chain_dir)
            project_root = str(paths.project_root) if paths.project_root else ""
            project_id = paths.project_id or ""
            if not project_root:
                return None
            return cls(
                chain_dir=chain_dir,
                project_root=project_root,
                project_id=project_id,
            )
        except Exception as e:
            logger.warning(
                "MemoryContext.resolve(%s) failed (%s: %s)",
                chain_dir, type(e).__name__, e,
            )
            return None

    def ckg(self) -> Any | None:
        """Resolve CKG service (lazy)."""
        from fast_mcp.features.cognitive_knowledge import ckg_service

        return ckg_service(self.project_root)

    def cpg(self) -> Any | None:
        """Resolve CPG service (lazy)."""
        from fast_mcp.features.code_analysis.application.services.cpg_service import (
            CpgService,
        )

        return CpgService.from_chain_dir(self.chain_dir)

    def code_analysis_service(self) -> Any:
        """Resolve CodeAnalysisService (lazy)."""
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        return CodeAnalysisService(self.chain_dir)

    def code_context_service(self) -> Any:
        """Resolve CodeContextService (lazy)."""
        from fast_mcp.features.code_analysis.application.services.code_context_service import (
            CodeContextService,
        )

        return CodeContextService(self.chain_dir)
