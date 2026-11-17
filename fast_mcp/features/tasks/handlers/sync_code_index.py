"""handlers/sync_code_index.py — sincroniza el CPG sin depender de que alguien se acuerde.

Problema medido: el CPG solo se construía en `PROJECT_INIT.SCAN_CODE`, así que
los proyectos dados de alta por `prepare` quedaban con 0 documentos y
`code_search`/`code_context` consultaban un índice vacío.

Idempotente:
  - CPG vacío     → build completo (una vez).
  - CPG con datos → update incremental (solo archivos cambiados).

Arquitectura (R1): este módulo NO importa la feature code_analysis. La función
`sync_project_cpg` la inyecta el composition root vía `register_all_tasks(
cpg_sync=...)`, igual que `sprint_service` para sprint_planning.
"""

from __future__ import annotations

from typing import Any, Callable

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def _make_handler(cpg_sync: Callable[[str], dict[str, Any]] | None):
    """Cierra el handler sobre la función inyectada por composition."""

    def sync_code_index(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        if cpg_sync is None:
            # Fail ruidoso: registrar el action sin dependencia inyectada es un
            # error de wiring, no algo que deba degradarse en silencio.
            return TaskResult.failure(
                "sync_code_index: cpg_sync no inyectado por el composition root."
            )
        chain_dir = data.get("chain_dir") or ctx.chain_dir
        if not chain_dir:
            return TaskResult.failure(
                "sync_code_index: sin chain_dir — no se puede resolver el CPG."
            )
        try:
            payload = cpg_sync(chain_dir)
        except Exception as exc:  # noqa: BLE001 — se reporta como fallo del task
            return TaskResult.failure(f"sync_code_index: {exc}")
        return TaskResult.success(data=payload)

    return sync_code_index


def register(registry, cpg_sync: Callable[[str], dict[str, Any]] | None = None) -> None:
    """Registra el action; `cpg_sync` llega desde el composition root."""
    registry.register(
        "sync_code_index",
        _make_handler(cpg_sync),
        metadata={
            "description": "SYNC: construye el CPG si está vacío, si no lo actualiza incremental",
            "tags": ["cpg", "bootstrap"],
            "scope": "internal",
        },
    )
