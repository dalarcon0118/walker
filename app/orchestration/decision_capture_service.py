"""app/orchestration/decision_capture_service.py — DecisionCaptureService.

Cross-feature orchestrator para capture_decision (ADR-051 Fase 4 G10).

Orquesta 2 features: project (path resolution) × cognitive_knowledge (CKG).
El side-effect decision_capture.py se reduce a un thin adapter.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class DecisionCaptureService:
    """Captura decisiones del PO en el CKG al completar un nodo de decisión.

    Orquesta ProjectService (paths) y CkgApplicationService (CKG).
    No contiene lógica de negocio propia — solo coordina.
    """

    @staticmethod
    def capture_decision(
        chain_dir: str,
        decision: str,
        rationale: str,
        sub_state: str,
        task_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Captura una decisión explícita en el CKG."""
        if not chain_dir:
            return {"ok": False, "error": "chain_dir not available"}

        if not decision:
            return {"ok": True, "captured": False, "reason": "sin decision que capturar"}

        try:
            from features.project import ProjectService
            from features.cognitive_knowledge.application.services.ckg_service import CkgApplicationService

            paths = ProjectService.resolve_paths(chain_dir)
            project_root = str(paths.project_root)

            # CICLO DE VIDA: detectar si esta decision reemplaza a una anterior
            supersedes = _detect_superseded(chain_dir, decision, sub_state)

            node = {
                "type": "decision",
                "label": f"decision:{decision}",
                "statement": (
                    f"Decision '{decision}' tomada en {sub_state}"
                    + (f": {rationale}" if rationale else "")
                ),
                "metadata": {
                    "decision": decision,
                    "rationale": rationale,
                    "sub_state": sub_state,
                    "task_id": task_id,
                    "source": f"session:{session_id}",
                },
            }
            if supersedes:
                node["supersedes"] = supersedes

            result = CkgApplicationService.save_nodes(chain_dir, [node])
            if is_successful(result):
                result = result.value_or({})
                result.setdefault("supersedes_detected", supersedes)
            else:
                logger.warning(
                    "decision_capture: save_nodes fallo (%s: %s)",
                    type(result.failure()).__name__,
                    result.failure(),
                )
                result = {}
            return {"ok": True, "captured": True, "result": result}
        except Exception as e:
            logger.warning(
                "decision_capture: capture_decision fallo (%s: %s)", type(e).__name__, e,
            )
            return {"ok": False, "error": str(e), "captured": False}


@safe
def _detect_superseded_core(chain_dir: str, decision: str, sub_state: str) -> list[str]:
    """Core puro: detecta decisiones activas previas sobre el sub_state (puede lanzar)."""
    from features.cognitive_knowledge.application.services.ckg_service import CkgApplicationService

    supersedes: list[str] = []
    if not sub_state or sub_state == "?" or not decision:
        return supersedes
    previas = CkgApplicationService.query_active(chain_dir, type_name="decision")
    for old in previas:
        meta = old.get("metadata", {}) or {}
        if meta.get("sub_state") != sub_state:
            continue
        old_decision = meta.get("decision", "")
        if old_decision and old_decision != decision:
            old_id = old.get("_id", "")
            if old_id:
                supersedes.append(old_id)
    return supersedes


def _detect_superseded(chain_dir: str, decision: str, sub_state: str) -> list[str]:
    """Detecta decisiones ACTIVAS previas sobre el mismo sub_state que esta reemplaza.

    Regla: una decision nueva sobre el mismo punto de decision (sub_state)
    con VALOR DISTINTO al previo indica que el PO cambio de opinion → la
    nueva reemplaza a la anterior.
    """
    result = _detect_superseded_core(chain_dir, decision, sub_state)
    if is_successful(result):
        return result.value_or([])
    logger.warning(
        "decision_capture: deteccion de superseded fallo (%s: %s)",
        type(result.failure()).__name__, result.failure(),
    )
    return []
