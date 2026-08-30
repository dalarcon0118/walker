"""app/orchestration/advance_sprint_task_service.py — AdvanceSprintTaskService.

Cross-feature orchestrator para advance_sprint_task (ADR-051 Fase 4 G10).

Orquesta Delta (SpringService). El side-effect planning.py se reduce a un thin adapter.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AdvanceSprintTaskService:
    """Avanza el índice de la spec actual en el sprint."""

    @staticmethod
    def advance_sprint_task(chain_dir: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Avanza el índice del sprint en el DeltaGraph."""
        params = params or {}
        sprint_dir = chain_dir
        if not sprint_dir:
            return {"ok": False, "error": "chain_dir not available"}

        try:
            from features.delta_tracking import spring_service

            spring_svc = spring_service(chain_dir=sprint_dir)
            if not spring_svc:
                return {"ok": False, "error": "cannot resolve SpringService"}

            spring_id = spring_svc.get_spring_id(sprint_dir)

            # ── Resolver total de specs ANTES de avanzar (para clamp) ──
            total_specs = 0
            specs = []
            try:
                spring_svc.ensure_spring_imported(sprint_dir, spring_id)
                specs = spring_svc.get_specs_legacy(spring_id)
            except Exception as e:
                logger.warning(
                    "advance_sprint_task: no pudo cargar specs (%s: %s); specs vacías",
                    type(e).__name__, e,
                )
                specs = []
            total_specs = len(specs)

            # ── Contexto actual desde el DELTA (nodo spring) ──
            ctx_state = spring_svc.get_spring_context(spring_id)
            next_idx = ctx_state.get("current_task_index", 0) + 1

            # ── Clamp al rango de specs ──
            current_idx = (
                min(next_idx, max(total_specs - 1, 0)) if total_specs > 0 else next_idx
            )
            context_updates = {
                "current_task_index": current_idx,
                "current_spec_index": current_idx,
            }
            all_done = current_idx >= total_specs - 1 if total_specs > 0 else False
            next_spec_id = ""
            if total_specs > 0:
                next_spec_id = specs[current_idx].get(
                    "spec_id", specs[current_idx].get("task_id", "")
                )
            if next_spec_id:
                context_updates["task_id"] = next_spec_id
            try:
                spring_svc.set_spring_context(spring_id, **context_updates)
                return {
                    "ok": True,
                    "current_task_index": current_idx,
                    "next_spec_id": next_spec_id,
                    "all_done": all_done,
                }
            except Exception as e:
                logger.warning(
                    "advance_sprint_task: set_spring_context fallo (%s: %s)",
                    type(e).__name__,
                    e,
                )
                return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(
                "advance_sprint_task: advance_sprint_task fallo (%s: %s)",
                type(e).__name__, e,
            )
            return {"ok": False, "error": str(e)}
