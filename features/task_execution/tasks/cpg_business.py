"""cpg_business task — Capa de negocio del CPG.

Consulta la capa de negocio derivada por BusinessAnalyzer:
  - pure_functions:      funciones reutilizables sin side-effects
  - business_flows:      cadenas CALLS completas (flujos de negocio)
  - architecture_patterns: TEA, hooks, redux, pure_domain
  - metrics:             acoplamiento, funciones huerfanas

Uso:
  walker <project>/cpg_business                     → todo
  walker <project>/cpg_business '{"file":"win-win"}'→ solo win-win
"""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult


def cpg_business(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    try:
        from features.code_analysis.application.query_service import QueryGraphUseCase
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        q = QueryGraphUseCase(
            chain_dir=chain_dir,
            project_id=paths.project_id,
            data_dir=paths.data_dir if paths.data_dir else None,
        )
        focus = ""
        if isinstance(data, dict):
            focus = data.get("file", "") or data.get("focus", "")
        layer = q.business_layer(focus=focus)
        return TaskResult.success(data=layer)
    except Exception as e:
        return TaskResult.failure(str(e))


def register(registry: Any) -> None:
    registry.register(
        "cpg_business",
        cpg_business,
        metadata={
            "description": "Capa de negocio del CPG: funciones puras, flujos de negocio, patrones arquitectonicos y metricas",
            "usage": "walker <project>/cpg_business '{\"file\":\"<substring>\"}'",
            "example": "walker <project>/cpg_business '{\"file\":\"win-win\"}'",
            "tags": ["cpg", "business", "architecture"],
        },
    )
