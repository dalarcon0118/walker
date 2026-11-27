"""handlers/info_acquisition.py — INFO ACQUISITION (Gollwitzer).

Sub-state deliberativo: adquisición dirigida de información para cerrar los
gaps declarados por STATE_CONSTRUCTION. Cierra el ciclo
STATE_CONSTRUCTION -> INFO_ACQUISITION -> OBSERVE antes del Rubicón (gate).
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def info_acquisition(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Registra la ronda de adquisición de información (gaps resueltos).

    Espera en data: queries (consultas dirigidas), resolved_gaps (ids de gaps
    cerrados). No decide ni planifica — solo registra evidencia adquirida.
    """
    queries = data.get("queries", [])
    resolved_gaps = data.get("resolved_gaps", [])
    task_id = data.get("task_id") or ctx.task_id

    # `acquired` no puede ser constante: una ronda sin consultas y sin gaps
    # cerrados no certifica adquisición (misma regla que SPEC-014 para tests y
    # que review_code con 0 archivos). Antes devolvía True siempre y el paso
    # automático pasaba por vacío.
    acquired = bool(queries or resolved_gaps)
    payload: dict[str, Any] = {
        "task_id": task_id,
        "phase": "info_acquisition",
        "queries": queries,
        "resolved_gaps": resolved_gaps,
        "chain_dir": ctx.chain_dir,
        "acquired": acquired,
    }
    if not acquired:
        payload["reason"] = (
            "ronda sin queries ni gaps cerrados: no certifica adquisición de "
            "información (declará queries o resolved_gaps)"
        )
    return TaskResult.success(data=payload)


def register(registry) -> None:
    """Register info_acquisition (Gollwitzer phase)."""
    registry.register("info_acquisition", info_acquisition, metadata={
        "description": "INFO ACQUISITION: targeted info to close declared gaps (Gollwitzer)",
        "tags": ["gollwitzer", "action-phase"],
        "scope": "internal",
    })