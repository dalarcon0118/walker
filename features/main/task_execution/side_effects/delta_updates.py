"""DeltaGraph side-effects: index_delta, reindex_delta.

Usa las funciones puras de index_service (application) con CognitiveGraph
como almacenamiento nativo con grafo + fulltext search. El wrapper legacy
DeltaGraphService fue eliminado (SSOT #5).

Result pattern (convention-001): ante fallo se degrada con fallback explícito
+ log; nunca except silencioso.
"""
from __future__ import annotations
import logging
from typing import Any
from shared.common.shared_domain import ExecutionContext
from core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING
from features.project import ProjectService

logger = logging.getLogger(__name__)


def _build_graph(project_root: str) -> Any:
    """Resuelve el orquestador CognitiveGraph vía DI (key "cognitive_graph")."""
    from core.engine.bootstrap import get_module_service

    graph_factory = get_module_service("cognitive_graph")
    return graph_factory(project_root)


@as_side_effect(scope=SCOPE_TRACKING)
def index_delta(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Indexa el sprint actual en el indice delta de CognitiveGraph."""
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available", "indexed_tasks": 0}
    try:
        paths = ProjectService.resolve_paths(chain_dir)
        cg = _build_graph(paths.project_root)
        from features.delta_tracking import SpringService

        spring_svc = SpringService.from_chain_dir(chain_dir)
        if not spring_svc:
            return {"ok": False, "error": "SpringService no disponible", "indexed_tasks": 0}
        count = spring_svc.index_sprint(chain_dir, cg)
        return {"ok": True, "indexed_tasks": count}
    except Exception as e:
        logger.warning(
            "delta_updates: index_delta fallo (%s: %s)", type(e).__name__, e,
        )
        return {"ok": False, "error": str(e), "indexed_tasks": 0}


@as_side_effect(scope=SCOPE_TRACKING)
def reindex_delta(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Re-indexa todos los sprints en el indice delta de CognitiveGraph."""
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available", "reindexed_tasks": 0}
    try:
        paths = ProjectService.resolve_paths(chain_dir)
        cg = _build_graph(paths.project_root)
        from features.delta_tracking import SpringService

        spring_svc = SpringService.from_chain_dir(chain_dir)
        if not spring_svc:
            return {"ok": False, "error": "SpringService no disponible", "reindexed_tasks": 0}
        count = spring_svc.index_all(str(paths.project_root), cg)
        return {"ok": True, "reindexed_tasks": count}
    except Exception as e:
        logger.warning(
            "delta_updates: reindex_delta fallo (%s: %s)", type(e).__name__, e,
        )
        return {"ok": False, "error": str(e), "reindexed_tasks": 0}


def register(engine: Any) -> None:
    engine.register("index_delta", index_delta)
    engine.register("reindex_delta", reindex_delta)
