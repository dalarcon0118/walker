"""application/error_handler.py — ErrorHandler.

SRP: Unica responsabilidad = manejar errores del workflow segun la
estrategia configurada (TASK-009): retry, block+message, log+continue,
compensate, escalate, on_failure.

Extraido de OrchestrationService (god class). El OrchestrationService
queda como fachada y delega aqui.
"""
from __future__ import annotations

import logging
from typing import Any

from libs.shared_domain import ExecutionContext
from ..domain.errors import (
    ErrorType,
    ErrorStrategy,
    DEFAULT_STRATEGIES,
    classify_error,
    WorkflowEngineError,
)


class ErrorHandler:
    """Maneja errores del workflow con estrategia configurable."""

    def __init__(
        self,
        submit_uc: Any,
        transition_uc: Any,
        side_effect_engine: Any,
    ):
        self._submit_uc = submit_uc
        self._transition_uc = transition_uc
        self._side_effect_engine = side_effect_engine

    def classify(self, error: Exception) -> ErrorType:
        """Clasifica un error segun su tipo."""
        return classify_error(error)

    def resolve_strategy(
        self,
        error_type: ErrorType,
        sub_state: Any | None = None,
    ) -> ErrorStrategy:
        """Resuelve la estrategia para un tipo de error.

        Orden de precedencia:
          1. sub_state.retry.on_error[error_type] (config del sub-estado)
          2. DEFAULT_STRATEGIES[error_type] (default global)
          3. ErrorStrategy.ON_FAILURE (ultimo recurso)
        """
        if sub_state and sub_state.retry and sub_state.retry.on_error:
            strat_name = sub_state.retry.on_error.get(error_type.value)
            if strat_name:
                try:
                    return ErrorStrategy(strat_name)
                except ValueError:
                    pass
        return DEFAULT_STRATEGIES.get(error_type, ErrorStrategy.ON_FAILURE)

    def handle(
        self,
        ctx: ExecutionContext,
        error: Exception,
        sub_state: Any | None = None,
        fields: dict | None = None,
    ) -> dict:
        """Maneja un error segun la estrategia configurada."""
        error_type = self.classify(error)
        strategy = self.resolve_strategy(error_type, sub_state)

        error_context = {
            "error_type": error_type.value,
            "strategy": strategy.value,
            "message": str(error),
        }

        # ── BLOCK+MESSAGE: retornar error al LLM ────────────────
        if strategy == ErrorStrategy.BLOCK_MESSAGE:
            return {
                "ok": False,
                "action": "error",
                "error_type": error_type.value,
                "strategy": "block+message",
                "message": str(error),
                "error": str(error),
            }

        # ── LOG+CONTINUE: loggear y continuar ────────────────────
        if strategy == ErrorStrategy.LOG_CONTINUE:
            logging.warning(
                "Error continuado (log+continue): %s - %s",
                error_type.value,
                error,
            )
            return {
                "ok": True,
                "action": "continue",
                "error_type": error_type.value,
                "strategy": "log+continue",
                "warning": str(error),
            }

        # ── RETRY: reintentar (max sub_state.retry.max_attempts) ─
        if strategy == ErrorStrategy.RETRY:
            max_attempts = (
                sub_state.retry.max_attempts if sub_state and sub_state.retry else 3
            )
            for attempt in range(max_attempts):
                try:
                    # Reintentar con LOS MISMOS fields (sin fields el retry
                    # ejecutaria la task con data vacia y sobrescribiria el estado)
                    result = self._submit_uc.execute(ctx, fields=fields or {})
                    result["error_context"] = error_context
                    result["retry_count"] = attempt + 1
                    return result
                except WorkflowEngineError:
                    if attempt < max_attempts - 1:
                        continue
                    return {
                        "ok": False,
                        "action": "error",
                        "error_type": error_type.value,
                        "strategy": "retry_failed",
                        "message": f"Retry agotado tras {max_attempts} intentos: {error}",
                        "original_error": str(error),
                    }
                except Exception as e2:
                    return {
                        "ok": False,
                        "action": "error",
                        "error_type": error_type.value,
                        "strategy": "retry_failed",
                        "message": f"Retry fallo: {e2}",
                        "original_error": str(error),
                    }
            return {
                "ok": False,
                "action": "error",
                "error_type": error_type.value,
                "strategy": "retry_failed",
                "message": f"Retry agotado: {error}",
            }

        # ── COMPENSATE: compensar side-effects ───────────────────
        if strategy == ErrorStrategy.COMPENSATE:
            if sub_state and sub_state.side_effects:
                self._side_effect_engine.compensate(
                    ctx,
                    [],  # No previous results to compensate
                    0,  # Failed at index 0
                )
            return {
                "ok": False,
                "action": "error",
                "error_type": error_type.value,
                "strategy": "compensate",
                "message": f"Compensado tras error: {error}",
            }

        # ── ESCALATE: enviar a REFINE.ESCALATION ────────────────
        if strategy == ErrorStrategy.ESCALATE:
            try:
                transition_result = self._transition_uc.execute(
                    ctx,
                    gate_name="escalate",
                    reason=f"Error {error_type.value}: {error}",
                )
                return {
                    "ok": False,
                    "action": "escalated",
                    "error_type": error_type.value,
                    "strategy": "escalate",
                    "to_node": transition_result.get("to_node", "REFINE"),
                    "message": f"Escalado a REFINE: {error}",
                }
            except Exception as e2:
                return {
                    "ok": False,
                    "action": "error",
                    "error_type": error_type.value,
                    "strategy": "escalate_failed",
                    "message": f"Escalation fallo: {e2}",
                }

        # ── ON_FAILURE: ejecutar como gate de transicion ─────────
        if strategy == ErrorStrategy.ON_FAILURE:
            if sub_state and sub_state.on_failure:
                try:
                    of_result = self._transition_uc.execute(
                        ctx,
                        gate_name=sub_state.on_failure,
                        reason=f"on_failure tras {error_type.value}: {error}",
                    )
                    return {
                        "ok": False,
                        "action": "on_failure",
                        "error_type": error_type.value,
                        "strategy": "on_failure",
                        "to": sub_state.on_failure,
                        "to_node": of_result.get("to_node", ""),
                        "message": f"Redirigido por on_failure: {error}",
                    }
                except Exception as e2:
                    return {
                        "ok": False,
                        "action": "error",
                        "error_type": error_type.value,
                        "strategy": "on_failure_failed",
                        "message": f"on_failure fallo: {e2}",
                    }
            return {
                "ok": False,
                "action": "error",
                "error_type": error_type.value,
                "strategy": "on_failure_no_route",
                "message": str(error),
            }

        return {
            "ok": False,
            "action": "error",
            "error_type": error_type.value,
            "strategy": strategy.value,
            "message": str(error),
        }
