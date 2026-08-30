"""use_cases/check_cpg_health.py — CheckCpgHealthUseCase.

Check CPG health: node count and status.
SSOT: solo code_analysis accede al CPG via CognitiveCpgRepository (puerto,
del índice — ADR-WALKER-055 F1: sin parseo crudo en la capa de aplicación).
"""
from __future__ import annotations

import logging

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class CheckCpgHealthUseCase:
    """Check CPG health: node count and status.

    SRP: Single source of truth for CPG health data.
         No other feature module should read CPG files directly.
    """

    @staticmethod
    def execute(projects_data_dir: str) -> dict:
        """Check CPG status in the given data directory.

        Args:
            projects_data_dir: Path to .cognitive/projects/<pid>/data/

        Returns:
            dict with keys: status (str), node_count (int)
        """
        from features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )

        repo = CognitiveCpgRepository(projects_data_dir, "")
        if not repo.exists():
            return {"status": "not scanned", "node_count": 0}

        result = _load_cpg_core(repo)
        if is_successful(result):
            data = result.value_or({})
            total = len(data.get("nodes", {}))
            return {"status": f"{total} nodes", "node_count": total}
        error = result.failure()
        logger.warning(
            "No se pudo leer CPG en %s (%s: %s); se reporta no escaneado",
            projects_data_dir, type(error).__name__, error,
        )
        return {"status": "not scanned", "node_count": 0}


@safe
def _load_cpg_core(repo) -> dict:
    """Core puro de carga del CPG vía repository (puede lanzar)."""
    return repo.load()