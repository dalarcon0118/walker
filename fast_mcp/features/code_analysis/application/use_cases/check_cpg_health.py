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
        from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )

        repo = CognitiveCpgRepository(projects_data_dir, "")
        if not repo.exists():
            return {
                "status": "not scanned",
                "node_count": 0,
                "cyclo": {"known": 0, "total": 0, "status": "unavailable"},
            }

        result = _load_cpg_core(repo)
        if is_successful(result):
            data = result.value_or({})
            nodes = data.get("nodes", {}) or {}
            total = len(nodes)
            # P1.2: la cobertura de cyclo se reporta como partial, no como
            # "unavailable" ni 0. Es una propiedad del extractor (JS no tiene
            # cyclo), no del índice: fingir cualquiera de las dos engaña.
            callables = [
                n for n in nodes.values()
                if str(n.get("type") or n.get("kind") or "") in ("function", "method")
            ]
            known = sum(1 for n in callables if n.get("cyclo") is not None)
            cyclo_total = len(callables)
            if cyclo_total == 0 or known == 0:
                cyclo_status = "unavailable"
            elif known < cyclo_total:
                cyclo_status = "partial"
            else:
                cyclo_status = "full"
            return {
                "status": f"{total} nodes",
                "node_count": total,
                "cyclo": {
                    "known": known,
                    "total": cyclo_total,
                    "status": cyclo_status,
                },
            }
        error = result.failure()
        logger.warning(
            "No se pudo leer CPG en %s (%s: %s); se reporta no escaneado",
            projects_data_dir, type(error).__name__, error,
        )
        # No se pudo leer: se declara no escaneado, nunca un 0 silencioso.
        return {
            "status": "not scanned",
            "node_count": 0,
            "cyclo": {"known": 0, "total": 0, "status": "unavailable"},
        }


@safe
def _load_cpg_core(repo) -> dict:
    """Core puro de carga del CPG vía repository (puede lanzar)."""
    return repo.load()