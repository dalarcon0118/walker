"""
domain/errors.py — Errores de dominio del Workflow Navigation.

SSOT para todos los errores del dominio.
Tipos semanticos (no genericos) para que el caller sepa exactamente que fallo.

TASK-009: Taxonomia de errores y estrategias on_error.

ESTRATEGIA DE MANEJO DE ERRORES (convencion del dominio):
  Todo `except` DEBE decidir UNA de estas 5 acciones explicitas — NUNCA
  `except Exception: pass` silencioso (anti-patron que oculta fallos).

  1. PROPAGAR  — errores criticos (persistencia de estado, transiciones,
                 contratos). Si no se puede continuar, que el error suba.
  2. BLOQUEAR  — errores de validacion/gate. Retornar ok=False con mensaje
                 claro; el workflow NO avanza hasta que se resuelva.
  3. REINTENTAR — errores transitorios (infraestructura, execution).
                 Usar sub_state.retry con max_attempts/backoff.
  4. COMPENSAR — side-effects con compensation definida (Saga). Revertir
                 el efecto parcial antes de continuar.
  5. LOGGEAR   — operaciones best-effort (tracing, captura de contexto,
                 fallbacks de carga). Registrar SIEMPRE con logger
                 (error/warning/debug segun gravedad) y continuar.

  Regla: si un except no loggea y no propaga y no compensa, es un bug.
  El tracing (TraceLogger.trace) es OBSERVABILIDAD: nunca rompe el flujo,
  pero sus fallos SI se loggean (logger.error) para no perder diagnostico.
"""
from __future__ import annotations

from enum import Enum


# ── Error Taxonomy ─────────────────────────────────────────────────────────

class ErrorType(str, Enum):
    """Taxonomia de errores del workflow engine (TASK-009).

    Cada tipo tiene una estrategia default si no se configura on_error.
    """
    VALIDATION = "validation"           # Datos invalidos, campos faltantes
    EXECUTION = "execution"             # Error en TaskRegistry/ScriptRunner
    TRANSITION = "transition"           # Error en transicion/gate
    STATE = "state"                     # SubState/Nodo no encontrado
    SESSION = "session"                 # Error de session/contexto
    INFRASTRUCTURE = "infrastructure"   # Error de IO/repositorio
    UNKNOWN = "unknown"                 # Error no clasificado


class ErrorStrategy(str, Enum):
    """Estrategias de manejo de errores (TASK-009).

    Se configuran en sub_state.retry.on_error como:
      on_error: {
        "validation": "block+message",
        "execution": "retry",
        "infrastructure": "retry_2x"
      }
    """
    RETRY = "retry"                     # Reintentar con misma config
    RETRY_2X = "retry_2x"               # Reintentar con timeout 2x
    COMPENSATE = "compensate"           # Compensar side-effects
    ESCALATE = "escalate"               # Enviar a REFINE.ESCALATION
    BLOCK_MESSAGE = "block+message"     # Devolver error al LLM
    LOG_CONTINUE = "log+continue"       # Loggear y continuar
    ON_FAILURE = "on_failure"           # Ejecutar transicion on_failure


# ── Error Classifier ──────────────────────────────────────────────────────

def classify_error(error: Exception) -> ErrorType:
    """Clasifica una excepcion segun su tipo.

    Returns:
        ErrorType correspondiente al tipo de excepcion.
    """
    if isinstance(error, ValidationError):
        return ErrorType.VALIDATION
    elif isinstance(error, (GateValidationError, TransitionBlockedError)):
        return ErrorType.TRANSITION
    elif isinstance(error, AutoAdvanceError):
        return ErrorType.EXECUTION
    elif isinstance(error, (NodeNotFoundError, SubStateNotFoundError, GateNotFoundError, TaskNotFoundError)):
        return ErrorType.STATE
    elif isinstance(error, WorkflowEngineError):
        return ErrorType.EXECUTION
    else:
        return ErrorType.UNKNOWN


DEFAULT_STRATEGIES: dict[ErrorType, ErrorStrategy] = {
    ErrorType.VALIDATION: ErrorStrategy.BLOCK_MESSAGE,
    ErrorType.EXECUTION: ErrorStrategy.RETRY,
    ErrorType.TRANSITION: ErrorStrategy.BLOCK_MESSAGE,
    ErrorType.STATE: ErrorStrategy.ESCALATE,
    ErrorType.SESSION: ErrorStrategy.RETRY,
    ErrorType.INFRASTRUCTURE: ErrorStrategy.RETRY_2X,
    ErrorType.UNKNOWN: ErrorStrategy.RETRY,
}


class WorkflowEngineError(Exception):
    """Base de todos los errores del workflow engine."""
    def __init__(self, message: str, context: dict | None = None):
        self.message = message
        self.context = context or {}
        super().__init__(message)


class NodeNotFoundError(WorkflowEngineError):
    """El nodo solicitado no existe en el grafo."""
    def __init__(self, node_id: str):
        super().__init__(f"Nodo '{node_id}' no encontrado en el workflow",
                         {"node_id": node_id})


class SubStateNotFoundError(WorkflowEngineError):
    """El sub-estado solicitado no existe en el nodo."""
    def __init__(self, node_id: str, sub_key: str):
        super().__init__(f"Sub-estado '{sub_key}' no encontrado en nodo '{node_id}'",
                         {"node_id": node_id, "sub_key": sub_key})


class GateNotFoundError(WorkflowEngineError):
    """La gate solicitada no existe para el nodo origen."""
    def __init__(self, node_id: str, gate: str):
        super().__init__(f"Gate '{gate}' no encontrada para nodo '{node_id}'",
                         {"node_id": node_id, "gate": gate})


class TransitionBlockedError(WorkflowEngineError):
    """Transicion bloqueada por precondiciones no cumplidas."""
    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message, {"details": details or []})


class GateValidationError(WorkflowEngineError):
    """La validacion de la gate fallo."""
    def __init__(self, gate: str, stderr: str):
        super().__init__(f"Gate '{gate}' validation failed: {stderr}",
                         {"gate": gate, "stderr": stderr})


class ValidationError(WorkflowEngineError):
    """Error de validacion de datos o estado."""
    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message, {"errors": errors or []})


class TaskNotFoundError(WorkflowEngineError):
    """Tarea no encontrada en TaskRegistry."""
    def __init__(self, message: str = ""):
        super().__init__(message or "Tarea no encontrada en TaskRegistry")


class AutoAdvanceError(WorkflowEngineError):
    """Error durante auto-advance de sub-estado."""
    def __init__(self, node_id: str, sub_key: str, exit_code: int, stderr: str):
        super().__init__(
            f"Auto-advance fallo en '{node_id}.{sub_key}' (exit={exit_code})",
            {"node_id": node_id, "sub_key": sub_key, "exit_code": exit_code, "stderr": stderr}
        )
