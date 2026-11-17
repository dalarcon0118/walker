"""code_analysis/application/use_cases/sync_cpg.py — bootstrap idempotente del CPG.

Problema medido (2026-11-16): el CPG solo se construía en `PROJECT_INIT.SCAN_CODE`,
así que los proyectos dados de alta por `prepare` quedaban con 0 documentos y
`code_search`/`code_context` consultaban un índice vacío.

Este use case vive en code_analysis (dueño del CPG) y NO importa nada de la
feature `tasks` (R1). El wiring lo hace el composition root, que inyecta la
función en el handler `sync_code_index` del workflow.
"""

from __future__ import annotations

from typing import Any


def sync_project_cpg(chain_dir: str) -> dict[str, Any]:
    """Construye el CPG si está vacío; si no, aplica el update incremental.

    Args:
        chain_dir: raíz del repo del proyecto (resuelta por el caller).

    Returns:
        dict con `mode` ('full'|'incremental'), `stats_before`, `result`.

    Raises:
        ValueError: sin `chain_dir`.
        RuntimeError: el CPG no es resolvible para ese repo.
    """
    from fast_mcp.features.code_analysis.application.services.cpg_service import (
        CpgService,
    )

    if not chain_dir:
        raise ValueError(
            "sync_project_cpg: sin chain_dir — no se puede resolver el CPG."
        )

    service = CpgService.from_chain_dir(chain_dir)
    if service is None:
        raise RuntimeError(
            f"sync_project_cpg: CPG no resolvible para {chain_dir!r}."
        )

    stats = service.stats() or {}
    has_data = any(
        isinstance(value, int) and value > 0 for value in stats.values()
    )
    result = service.update(refresh=False) if has_data else service.build()

    return {
        "mode": "incremental" if has_data else "full",
        "stats_before": stats,
        "result": result,
        "chain_dir": chain_dir,
    }


__all__ = ["sync_project_cpg"]
