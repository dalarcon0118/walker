"""application/error_handler.py — ErrorHandler.

SRP: Unica responsabilidad = manejar errores del workflow segun la
estrategia configurada (TASK-009): retry, block+message, log+continue,
compensate, escalate, on_failure.

Refactorizado para usar Result pattern (dry-python/returns) internamente.
El API externo sigue retornando dicts para compatibilidad.
"""

from __future__ import annotations

import logging
from typing import Any

from returns.result import safe
from returns.pipeline import is_successful

from libs.shared_domain import ExecutionContext
from ..domain.errors import (
    ErrorType,
    ErrorStrategy,
    DEFAULT_STRATEGIES,
    classify_error,
    WorkflowEngineError,
)


# ── Puente de nombres: excepciones del engine ↔ claves de la plantilla ──
_ERROR_CLASS_BRIDGE: dict[str, str] = {
    "AutoAdvanceError": "TaskFailedError",
    "TaskNotFoundError": "TaskFailedError",
    "ValidationError": "SchemaViolation",
    "TransitionBlockedError": "PreconditionError",
    "GateValidationError": "PreconditionError",
    "SubStateNotFoundError": "PreconditionError",
    "NodeNotFoundError": "PreconditionError",
}


class ErrorHandler:
    """Maneja errores del workflow con estrategia configurable."""

    def __init__(
        self,
        submit_uc: Any,
        transition_uc: Any,
        side_effect_engine: Any,
        workflow_repo: Any = None,
        state_repo: Any = None,
    ):
        self._submit_uc = submit_uc
        self._transition_uc = transition_uc
        self._side_effect_engine = side_effect_engine
        self._workflow_repo = workflow_repo
        self._state_repo = state_repo

    def classify(self, error: Exception) -> ErrorType:
        """Clasifica un error segun su tipo."""
        return classify_error(error)

    def resolve_strategy(
        self,
        error_type: ErrorType,
        sub_state: Any | None = None,
        error: Exception | None = None,
    ) -> ErrorStrategy:
        """Resuelve la estrategia para un tipo de error."""
        if sub_state and sub_state.retry and sub_state.retry.on_error:
            on_error = sub_state.retry.on_error
            strat_name = on_error.get(error_type.value)
            if not strat_name and error is not None:
                class_name = type(error).__name__
                strat_name = on_error.get(class_name)
            if not strat_name and error is not None:
                bridge_key = _ERROR_CLASS_BRIDGE.get(type(error).__name__)
                if bridge_key:
                    strat_name = on_error.get(bridge_key)
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
        """Maneja un error segun la estrategia configurada.

        Returns:
            dict con ok, action, error_type, strategy, message, etc.
        """
        error_type = self.classify(error)
        strategy = self.resolve_strategy(error_type, sub_state, error=error)

        error_context = {
            "error_type": error_type.value,
            "strategy": strategy.value,
            "message": str(error),
        }

        # ── BLOCK+MESSAGE: retornar error al LLM ────────────────
        if strategy == ErrorStrategy.BLOCK_MESSAGE:
            return self._block_message(error, error_type, error_context)

        # ── LOG+CONTINUE: loggear y continuar ────────────────────
        if strategy == ErrorStrategy.LOG_CONTINUE:
            return self._log_continue(error, error_type)

        # ── RETRY: reintentar con Result pattern ─────────────────
        if strategy == ErrorStrategy.RETRY:
            return self._retry(ctx, error, sub_state, fields, error_type, error_context)

        # ── COMPENSATE: compensar side-effects ───────────────────
        if strategy == ErrorStrategy.COMPENSATE:
            return self._compensate(ctx, error, sub_state, error_type)

        # ── ESCALATE: enviar a REFINE.ESCALATION ────────────────
        if strategy == ErrorStrategy.ESCALATE:
            return self._escalate(ctx, error, error_type)

        # ── ON_FAILURE: saltar al sub-estado indicado ─────────────
        if strategy == ErrorStrategy.ON_FAILURE:
            return self._on_failure(ctx, error, sub_state, error_type)

        return {
            "ok": False,
            "action": "error",
            "error_type": error_type.value,
            "strategy": strategy.value,
            "message": str(error),
        }

    # ── Helpers privados (cada estrategia es un método SRP) ──────────

    def _block_message(
        self, error: Exception, error_type: ErrorType, error_context: dict
    ) -> dict:
        """BLOCK+MESSAGE: retornar error al LLM con contexto."""
        details = error.context if isinstance(error, WorkflowEngineError) else {}
        msg = str(error)
        if details:
            if "details" in details and isinstance(details["details"], list):
                msg += " — Fallaron: " + "; ".join(details["details"])
            if (
                "errors" in details
                and isinstance(details["errors"], list)
                and details["errors"]
            ):
                msg += " — Errores: " + "; ".join(str(e) for e in details["errors"])
            if "stderr" in details and details["stderr"]:
                msg += f" — Stderr: {details['stderr'][:300]}"
        return {
            "ok": False,
            "action": "error",
            "error_type": error_type.value,
            "strategy": "block+message",
            "message": msg,
            "error": msg,
            "details": details,
        }

    def _log_continue(self, error: Exception, error_type: ErrorType) -> dict:
        """LOG+CONTINUE: loggear y continuar."""
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

    def _retry(
        self,
        ctx: ExecutionContext,
        error: Exception,
        sub_state: Any | None,
        fields: dict | None,
        error_type: ErrorType,
        error_context: dict,
    ) -> dict:
        """RETRY: reintentar con @safe para convertir excepciones en Failure."""
        max_attempts = (
            sub_state.retry.max_attempts if sub_state and sub_state.retry else 3
        )

        @safe
        def _execute_retry(attempt_num: int) -> dict:
            """Ejecuta el submit con @safe para capturar excepciones."""
            result = self._submit_uc.execute(ctx, fields=fields or {})
            result["error_context"] = error_context
            result["retry_count"] = attempt_num + 1
            return result

        for attempt in range(max_attempts):
            result = _execute_retry(attempt)

            if is_successful(result):
                return result.value_or({})

        # Todos los reintentos fallaron
        return {
            "ok": False,
            "action": "error",
            "error_type": error_type.value,
            "strategy": "retry_failed",
            "message": f"Retry agotado tras {max_attempts} intentos: {error}",
            "original_error": str(error),
        }

    def _compensate(
        self,
        ctx: ExecutionContext,
        error: Exception,
        sub_state: Any | None,
        error_type: ErrorType,
    ) -> dict:
        """COMPENSATE: compensar side-effects."""
        if sub_state and sub_state.side_effects:
            self._side_effect_engine.compensate(ctx, [], 0)
        return {
            "ok": False,
            "action": "error",
            "error_type": error_type.value,
            "strategy": "compensate",
            "message": f"Compensado tras error: {error}",
        }

    def _escalate(
        self, ctx: ExecutionContext, error: Exception, error_type: ErrorType
    ) -> dict:
        """ESCALATE: enviar a REFINE.ESCALATION."""

        @safe
        def _do_escalate() -> dict:
            return self._transition_uc.execute(
                ctx,
                gate_name="escalate",
                reason=f"Error {error_type.value}: {error}",
            )

        result = _do_escalate()
        if is_successful(result):
            transition_result = result.value_or({})
            return {
                "ok": False,
                "action": "escalated",
                "error_type": error_type.value,
                "strategy": "escalate",
                "to_node": transition_result.get("to_node", "REFINE"),
                "message": f"Escalado a REFINE: {error}",
            }
        else:
            e2 = result.failure()
            return {
                "ok": False,
                "action": "error",
                "error_type": error_type.value,
                "strategy": "escalate_failed",
                "message": f"Escalation fallo: {e2}",
            }

    def _on_failure(
        self,
        ctx: ExecutionContext,
        error: Exception,
        sub_state: Any | None,
        error_type: ErrorType,
    ) -> dict:
        """ON_FAILURE: saltar al sub-estado indicado."""
        if not sub_state or not sub_state.on_failure:
            return {
                "ok": False,
                "action": "error",
                "error_type": error_type.value,
                "strategy": "on_failure_no_route",
                "message": str(error),
            }

        @safe
        def _do_jump() -> dict:
            from ..domain.services import AdvanceService
            from ..infrastructure.repositories import (
                WorkflowRepository,
                WorkflowStateRepository,
            )

            wf_repo = self._workflow_repo or WorkflowRepository()
            st_repo = self._state_repo or WorkflowStateRepository()
            graph = wf_repo.load(ctx.chain_dir)
            state = st_repo.load(ctx.chain_dir)

            jumped = AdvanceService.jump_to_sub_state(
                state, graph, sub_state.on_failure
            )
            if jumped is not None:
                st_repo.save(ctx.chain_dir, jumped)
                return {
                    "ok": False,
                    "action": "on_failure",
                    "error_type": error_type.value,
                    "strategy": "on_failure",
                    "to": sub_state.on_failure,
                    "to_node": jumped.current_node,
                    "message": f"Redirigido a {sub_state.on_failure} por on_failure: {error}",
                }

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

        result = _do_jump()
        if is_successful(result):
            return result.value_or({})
        else:
            e2 = result.failure()
            return {
                "ok": False,
                "action": "error",
                "error_type": error_type.value,
                "strategy": "on_failure_failed",
                "message": f"on_failure fallo: {e2}",
            }
