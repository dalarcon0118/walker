"""cognitive_knowledge._composition — Composition root for CKG.

Single construction point: consumers don't decide how to resolve the graph
or which repository to use. If the constructor signature changes tomorrow,
only this module is touched.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def ckg_service(
    data_dir: str = "",
    repo: Any | None = None,
) -> Any | None:
    """Build CodeKnowledgeGraph for the given project.

    Args:
        data_dir: Canonical memory DATA directory (resolved by the caller
              via ProjectPaths.data_dir or paths module).
        repo: Injected repository (tests/alternatives) — defaults to
              CognitiveCkgRepository.

    Returns:
        CodeKnowledgeGraph or None if no data_dir.
    """
    if not data_dir:
        return None
    from fast_mcp.features.cognitive_knowledge.application.ckg import (
        CodeKnowledgeGraph,
    )

    return CodeKnowledgeGraph(data_dir, repo=repo)


def ckg_repository(data_dir: str = "", graph_factory: Any = None) -> Any | None:
    """Factory for CKG repository (CognitiveCkgRepository by default).

    Single construction point: consumers receive the CkgRepository port
    and never import concrete infrastructure.

    Args:
        data_dir: Canonical memory data directory (resolved by the caller
                  via ProjectPaths.data_dir or paths module).
        graph_factory: Optional injection; defaults to sqlite-backed CognitiveGraph.
    """
    if not data_dir:
        return None
    from fast_mcp.features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import (
        CognitiveCkgRepository,
    )

    return CognitiveCkgRepository(data_dir, graph_factory=graph_factory)


def ckg_analysis_adapter(chain_dir: str = "") -> Any | None:
    """Factory for CKG analysis adapter (ICkgServicePort default).

    Encapsulates internal composition (CognitiveCkgRepository + WalkerCkgAdapter)
    so tasks import only from the package root.

    Returns:
        WalkerCkgAdapter or None. Never raises (convention-001: log on failure).
    """
    if not chain_dir:
        return None
    try:
        from fast_mcp.features.cognitive_knowledge.infrastructure.adapters.walker_ckg_adapter import (
            WalkerCkgAdapter,
        )
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        repo = ckg_repository(str(paths.data_dir))
        if repo is None:
            logger.warning(
                "cognitive_knowledge: ckg_analysis_adapter no repo for %r",
                chain_dir,
            )
            return None
        return WalkerCkgAdapter(repo)
    except Exception as e:
        logger.warning(
            "cognitive_knowledge: could not build CKG adapter (%s: %s)",
            type(e).__name__,
            e,
        )
        return None
