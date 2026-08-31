"""task_execution/context/providers.py — Provider de contexto task_intelligence (ADR-048).

El provider ejecuta TaskIntelligenceService por fase declarada en el nodo,
inyectando el resultado como capa << task_intelligence >> en el prompt del LLM.
No depende de la iniciativa del modelo: la recuperación es determinista.
"""
from __future__ import annotations

import logging
from typing import Any

from shared.common.context import ContextPiece

logger = logging.getLogger(__name__)


class TaskIntelligenceProvider:
    """Capa: task_intelligence — joins con propósito por fase del workflow."""

    LAYER = "task_intelligence"

    def instructions(self, config: dict, ctx: Any) -> str:
        phase = config.get("phase", "IMPLEMENT")
        return (
            f"Layer: task_intelligence (phase={phase}) — contexto inteligente "
            f"de cpg × ckg × delta × trace para la fase actual."
        )

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        chain_dir = getattr(ctx, "chain_dir", "") or ""
        if not chain_dir:
            logger.warning("task_intelligence: sin chain_dir en el contexto")
            return None

        phase = config.get("phase", "IMPLEMENT").upper()
        file = config.get("file", "") or getattr(ctx, "file", "") or ""
        task_id = getattr(ctx, "task_id", "") or ""
        goal = config.get("goal", "") or ""
        scope = config.get("scope", "") or ""

        try:
            from app.orchestration.intelligence_service import (
                build_intelligence_service,
            )

            service = build_intelligence_service(chain_dir)
        except Exception as e:
            logger.warning(
                "task_intelligence: no se pudo construir el servicio (%s: %s)",
                type(e).__name__,
                e,
            )
            return None

        result = self._run_phase(service, phase, file, task_id, goal, scope, chain_dir)
        if result is None:
            return None

        content = self._serialize(phase, result)
        return ContextPiece(
            provider=self.LAYER,
            name=f"task_intelligence:{phase}",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=len(str(content)) // 4,
            priority=40,
        )

    @staticmethod
    def _run_phase(service, phase: str, file: str, task_id: str, goal: str, scope: str, chain_dir: str):
        """Ejecuta la fase correspondiente del TaskIntelligenceService."""
        if phase == "IMPLEMENT":
            outcome = service.task_file_intelligence(
                file=file, task_id=task_id, chain_dir=chain_dir
            )
            return outcome.value_or(None) if hasattr(outcome, "value_or") else outcome
        if phase == "REFINE":
            outcome = service.failure_diagnosis(
                task_id=task_id, chain_dir=chain_dir
            )
            return outcome.value_or(None) if hasattr(outcome, "value_or") else outcome
        if phase == "VERIFY":
            outcome = service.qa_coverage(
                task_id=task_id, chain_dir=chain_dir
            )
            return outcome.value_or(None) if hasattr(outcome, "value_or") else outcome
        if phase == "SPRINT":
            outcome = service.sprint_evidence(
                goal=goal, scope=scope, chain_dir=chain_dir
            )
            return outcome.value_or(None) if hasattr(outcome, "value_or") else outcome
        logger.warning("task_intelligence: fase desconocida '%s'", phase)
        return None

    @staticmethod
    def _serialize(phase: str, dto: Any) -> dict:
        """Serializa el DTO de fase a dict para el prompt."""
        if hasattr(dto, "as_dict"):
            return dto.as_dict()
        if hasattr(dto, "__dict__"):
            return dict(dto.__dict__)
        return {"phase": phase, "data": str(dto)}


def register_providers(registry: Any) -> None:
    """Registra el provider task_intelligence en el ContextProviderRegistry."""
    registry.register("task_intelligence", TaskIntelligenceProvider())
