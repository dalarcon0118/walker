"""code_search task — búsqueda full-text en CKG.

Wrapper DELGADO sobre CkgApplicationService.gather_context para búsqueda
de código/conocimiento indexado. Sin lógica de negocio adicional: delega
en el Application Service y devuelve el resultado crudo con metadata.
"""
from __future__ import annotations

from typing import Any

from returns.pipeline import is_successful
from shared.common.shared_domain import ExecutionContext, TaskResult


def code_search(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Busca conocimiento indexado en CKG por texto libre.

    Args:
        ctx: contexto de ejecución con chain_dir.
        data: dict con `query` (requerido) y `max_results` (opcional, default 20).

    Returns:
        TaskResult con `results`, `query` y `count`.
    """
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure(
            "chain_dir not available",
            data={"next_step": "Run from a project sprint or pass project_id"},
        )

    query = data.get("query", "").strip()
    if not query:
        return TaskResult.failure(
            "query is required",
            data={"next_step": "Usage: code_search '{\"query\":\"auth middleware\"}'"},
        )

    max_results = int(data.get("max_results", 20))

    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )

    result = CkgApplicationService.gather_context(chain_dir, query=query)
    if not is_successful(result):
        error = result.failure()
        return TaskResult.failure(
            f"code_search failed: {error}",
            data={"query": query, "count": 0, "results": []},
        )

    results = result.value_or([])
    return TaskResult.success(
        data={
            "query": query,
            "count": len(results),
            "results": results[:max_results],
        }
    )


def register(registry: Any) -> None:
    registry.register(
        "code_search",
        code_search,
        metadata={
            "description": "Búsqueda full-text en CKG sobre código y conocimiento indexado.",
            "usage": 'walker <project>/code_search \'{"query":"auth middleware"}\'',
            "example": 'walker <project>/code_search \'{"query":"auth middleware","max_results":10}\'',
            "tags": ["code", "search", "ckg", "knowledge"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Texto de búsqueda full-text (requerido).",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Límite de resultados (default 20).",
                    },
                },
                "required": ["query"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer"},
                    "results": {"type": "array"},
                },
                "required": ["query", "count", "results"],
            },
        },
    )
