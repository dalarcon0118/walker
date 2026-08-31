"""Knowledge tasks — thin adapters to cognitive_knowledge Application Service.

ADR-WALKER-048: los tasks consumen Application Services, nunca Use Cases
directos (INV-013).
"""
from __future__ import annotations
from typing import Any
from shared.core.engine.domain.execution import ExecutionContext, TaskResult
from shared.common.result_types import is_successful


def session_init(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"session_id": ctx.session_id, "ok": True})


def collect_knowledge(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )

    result = CkgApplicationService.collect_knowledge(chain_dir, data, task_id=ctx.task_id)
    if not is_successful(result):
        return TaskResult.failure(str(result.failure()))
    return TaskResult.success(data={"task_id": data.get("task_id", ctx.task_id), **result.value_or({})})


def analyze_capas(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"task_id": ctx.task_id, "analizado": True})


def review_analysis(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"task_id": ctx.task_id, "revisado": True})


def register(registry: Any) -> None:
    # Acciones del workflow (bootstrap, etc.) — internas. save_to_ckg vive en
    # kg.py (publica, fusionada con el formato nodes del workflow).
    # SPEC-014 Fase 1: close_sprint tiene UN solo registro — el handler real
    # vive en planning.py (_sprint_task); knowledge.py no registra el stub.
    for name, fn, desc in [
        ("session_init", session_init, "Initialize session"),
        ("collect_knowledge", collect_knowledge, "Collect and persist knowledge"),
        ("analyze_capas", analyze_capas, "Analyze project layers"),
        ("review_analysis", review_analysis, "Review analysis"),
    ]:
        registry.register(name, fn, metadata={"description": desc, "tags": ["knowledge"], "scope": "internal"})
