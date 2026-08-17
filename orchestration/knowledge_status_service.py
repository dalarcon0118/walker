"""orchestration/knowledge_status_service.py — KnowledgeStatusService.

Cross-feature orchestrator para update_knowledge_status (ADR-051 Fase 4 G10).

Orquesta 2 features: delta_tracking (SpringService) × cognitive_knowledge (CkgStatusUpdater).
El side-effect knowledge_status.py se reduce a un thin adapter.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class KnowledgeStatusService:
    """Actualiza el status de knowledge en el nodo spring del delta.

    Orquesta Delta (SpringService) y CognitiveKnowledge (CkgStatusUpdater).
    No contiene lógica de negocio propia — solo coordina.
    """

    @staticmethod
    def update_knowledge_status(chain_dir: str, event: str = "") -> dict[str, Any]:
        """Actualiza el status de knowledge en el spring del delta."""
        if not chain_dir:
            return {"ok": False, "error": "chain_dir not available"}

        try:
            from features.delta_tracking import SpringService
            from features.cognitive_knowledge.domain.services import CkgStatusUpdater

            spring_svc = SpringService.from_chain_dir(chain_dir)
            if not spring_svc:
                return {"ok": False, "error": "SpringService no disponible"}

            spring_id = spring_svc.get_spring_id(chain_dir)
            spring = spring_svc.ensure_spring_imported(chain_dir, spring_id)
            if not spring:
                return {"ok": False, "error": "spring not found in delta"}

            current = spring.get("knowledge_status", "up_to_date")

            if event == "implementation_start":
                new_status = CkgStatusUpdater.on_implementation_start(current)
            elif event == "knowledge_captured":
                new_status = CkgStatusUpdater.on_knowledge_captured(current)
            elif event == "code_indexed":
                new_status = CkgStatusUpdater.on_code_indexed(current)
            else:
                return {
                    "ok": False,
                    "error": f"Unknown event '{event}'",
                    "previous_status": current,
                }

            spring_svc.update_spring(spring_id, {"knowledge_status": new_status})
            return {"ok": True, "previous_status": current, "status": new_status}
        except Exception as e:
            logger.warning(
                "knowledge_status: update_knowledge_status fallo (%s: %s)",
                type(e).__name__, e,
            )
            return {"ok": False, "error": str(e)}
