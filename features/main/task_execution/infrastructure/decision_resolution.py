"""task_execution/infrastructure/decision_resolution.py — Resolución de decisiones.

Glue del task layer (ADR-051 G4): traduce el payload de capture_task a la
lista de decisiones a persistir — explícitas (payload.decisions/decision),
de campo, o detectadas automáticamente desde archivos de arquitectura via
el side-effect decision_detector. NO persiste nada: la persistencia es de
CkgApplicationService.capture_decisions.

Result pattern (convention-001): degrada con fallback explícito + log.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


@safe
def _detect_decisions_core(files: list) -> list:
    """Core puro: detecta decisiones desde archivos de arquitectura (puede lanzar)."""
    from features.main.task_execution.side_effects.decision_detector import (
        detect_decision_from_changes,
    )

    return detect_decision_from_changes(files)


def resolve_decision_list(payload: dict, files: list, objective: str) -> list:
    """Resuelve la lista de decisiones a capturar.

    Orden: explícitas (payload.decisions) → campo 'decision' → detección
    automática desde archivos de arquitectura (objective como base).
    """
    decisions = payload.get("decisions", []) or []
    if not decisions and payload.get("decision"):
        decisions = [
            payload.get("decision")
            if isinstance(payload["decision"], dict)
            else {"statement": payload["decision"]}
        ]

    if not decisions:
        detected_result = _detect_decisions_core(files or [])
        if is_successful(detected_result):
            detected = detected_result.value_or([])
            if detected:
                decisions = [
                    {
                        "statement": (
                            f"ADR: {objective} — afecta {det['area']} "
                            f"({len(det['files'])} archivos de arquitectura)"
                        ),
                        "rationale": (
                            f"Detectado automaticamente desde files de {det['area']}. "
                            f"Archivos: {', '.join(det['files'][:5])}"
                        ),
                    }
                    for det in detected
                ]
        else:
            logger.warning(
                "capture_task: deteccion de decisiones fallo (%s)",
                detected_result.failure(),
            )
    return decisions
