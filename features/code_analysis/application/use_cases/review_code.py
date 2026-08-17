"""use_cases/review_code.py — ReviewCodeUseCase.

Ejecuta code review via CPG.

Devuelve DOS informes:
  1. quality: métricas + quality gates + smells + lint (primer informe)
  2. bug_proposals: análisis de bugs del CPG recién indexado (segundo
     informe post-index, para que el agente vea el estado del código)
"""
from __future__ import annotations

import json
import logging
import os

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class ReviewCodeUseCase:
    """Execute code review via CPG."""

    @staticmethod
    def execute(
        chain_dir: str, files: list[str] | None = None, thresholds: dict | None = None
    ) -> dict:
        from features.code_analysis.application.review_service import ReviewUseCase
        from features.project import ProjectService

        # data_dir explícito (SSOT local) — evita que ReviewUseCase resuelva
        # al registro global (bug: devolvía el walker root).
        paths = ProjectService.resolve_paths(chain_dir)
        svc = ReviewUseCase(chain_dir, data_dir=paths.data_dir or None)
        result = svc.review_files(files=files, thresholds=thresholds)
        report = result.to_dict()

        # 2do informe: bug proposals del CPG (si el CPG ya está indexado)
        bug_proposals = _load_bug_proposals(chain_dir)
        report["bug_proposals"] = bug_proposals
        report["second_report"] = {
            "summary": "Bug analysis post-index (desde CPG)",
            "total_findings": bug_proposals.get("total_findings", 0),
            "by_type": bug_proposals.get("by_type", {}),
            "findings": bug_proposals.get("findings", [])[:50],
        }
        return report


def _load_bug_proposals(chain_dir: str) -> dict:
    """Carga bug_proposals.json del CPG del proyecto (si existe).

    Result pattern: fallback explícito con log (nunca silencioso).
    """
    try:
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        data_dir = paths.data_dir or ""
        bp_path = os.path.join(data_dir, "bug_proposals.json")
        if os.path.isfile(bp_path):
            result = _load_bug_proposals_core(bp_path)
            if is_successful(result):
                return result.value_or({})
            error = result.failure()
            logger.warning(
                "bug_proposals.json ilegible (%s: %s); reporte vacio",
                type(error).__name__, error,
            )
    except Exception as e:
        logger.warning(
            "No se pudo resolver bug_proposals.json (%s: %s); reporte vacio",
            type(e).__name__, e,
        )
    return {"total_findings": 0, "by_type": {}, "findings": []}


@safe
def _load_bug_proposals_core(bp_path: str) -> dict:
    """Core puro de carga de bug_proposals (puede lanzar)."""
    with open(bp_path) as f:
        return json.load(f)
