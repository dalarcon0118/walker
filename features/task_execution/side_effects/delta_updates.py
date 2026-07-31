"""DeltaGraph side-effects: index_delta, reindex_delta.

Usa DeltaGraphService (features.delta_tracking) que a su vez usa
CognitiveGraph como almacenamiento nativo con grafo + fulltext search.
"""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext
from features.project import ProjectService
from features.delta_tracking import DeltaGraphService


def index_delta(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Indexa el sprint actual en el indice delta de CognitiveGraph."""
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available", "indexed_tasks": 0}
    try:
        paths = ProjectService.resolve_paths(chain_dir)
        svc = DeltaGraphService(paths.project_root)
        count = svc.index_sprint(chain_dir)
        return {"ok": True, "indexed_tasks": count}
    except Exception as e:
        return {"ok": False, "error": str(e), "indexed_tasks": 0}


def reindex_delta(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Re-indexa todos los sprints en el indice delta de CognitiveGraph."""
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available", "reindexed_tasks": 0}
    try:
        paths = ProjectService.resolve_paths(chain_dir)
        svc = DeltaGraphService(paths.project_root)
        count = svc.index_all()
        return {"ok": True, "reindexed_tasks": count}
    except Exception as e:
        return {"ok": False, "error": str(e), "reindexed_tasks": 0}


def register(engine: Any) -> None:
    engine.register("index_delta", index_delta)
    engine.register("reindex_delta", reindex_delta)
