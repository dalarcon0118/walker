"""application/reflection.py — Reflexion-by-context.

Construye un PAQUETE DE EVIDENCIA desde el TraceGraph + estado del workflow
para inyectarlo en REFINE.DIAGNOSE. El agente externo (no el walker) reflexiona
sobre esta evidencia para diagnosticar la causa raíz y elegir el target.

PRINCIPIO: el walker nunca decide — siempre PREPARA contexto/guía.
Este builder no asume inteligencia: entrega los hechos (intentos pasados,
gates fallidos, hint determinista) y deja la reflexión al LLM del agente.

PUREZA: el motor (engine) NO conoce REFINE. Los metadatos de reflexión
(source_node, attempt, error_context) se DERIVAN aquí desde datos que el
motor ya expone de forma genérica:
  - source_node      → eventos TRANSITION del TraceGraph con data.to == "REFINE"
  - attempt          → conteo de entradas a REFINE en el historial de transiciones
  - error_context    → state.context.last_error / last_error_strategy / last_reason
                       (claves genéricas que el motor persiste para CUALQUIER error)
"""
from __future__ import annotations

from features.event_tracing.infrastructure.repository import load_traces

# Máximo de intentos de refine. Declarado en edges.json (REFINE -> SPRINT, gate
# 'escalation': "Refinement limit reached (3 attempts)"). Es config de la FEATURE,
# no del motor.
DEFAULT_MAX_ATTEMPTS = 3


def _fmt_error_detail(ev: dict) -> str:
    """Extrae el detalle legible de un evento ERROR."""
    d = ev.get("data") or {}
    err_type = d.get("error_type", ev.get("event_type", "ERROR"))
    msg = (d.get("message", "") or "").strip()
    return f"{err_type}: {msg}" if msg else err_type


def _fmt_submit_detail(ev: dict) -> str:
    """Extrae el detalle legible de un evento SUBMIT (decisión/evidencia)."""
    d = ev.get("data") or {}
    decision = d.get("decision", "")
    evidence = (d.get("evidence", "") or "").strip()
    if decision:
        return f"decision={decision}" + (f" — {evidence[:80]}" if evidence else "")
    return evidence[:80] if evidence else "(submit sin detalles)"


def _derive_source_node(transitions: list[dict]) -> str:
    """Deriva el nodo que disparó el refine desde el TraceGraph.

    Busca la transición más reciente cuyo destino sea REFINE y devuelve
    su origen (data.from). Sin conocimiento del motor: puro evento trace.
    """
    for ev in reversed(transitions):
        d = ev.get("data") or {}
        if d.get("to") == "REFINE" and d.get("from"):
            return d["from"]
    return ""


def _derive_attempt(transitions: list[dict]) -> int:
    """Deriva el número de intento: cuántas veces se entró a REFINE."""
    return sum(1 for ev in transitions if (ev.get("data") or {}).get("to") == "REFINE")


def _build_hint(
    source_node: str, errors: list[dict], failed_gates: str
) -> str:
    """Hint determinista (sin LLM) para orientar el diagnóstico.

    Reglas simples sobre ORIGEN + ERRORES + GATES. El agente puede
    confirmarlas o descartarlas con su propia investigación.
    """
    hints: list[str] = []
    if errors:
        last = errors[-1]
        hints.append(f"último error registrado: {_fmt_error_detail(last)}")
    if failed_gates:
        hints.append(f"gates fallidos del paso anterior: {failed_gates}")
    if source_node == "IMPLEMENT":
        hints.append(
            "vienes de IMPLEMENT → sospecha de calidad/refactor: target sugerido 'implement'"
        )
    elif source_node == "ANALYZE":
        hints.append(
            "vienes de ANALYZE → análisis incompleto/incorrecto: target sugerido 'analyze'"
        )
    elif source_node == "DESIGN":
        hints.append(
            "vienes de DESIGN → diseño insuficiente: target sugerido 'analyze' o 'implement'"
        )
    elif source_node == "VERIFY":
        hints.append(
            "vienes de VERIFY → tests/QA fallaron: target sugerido 'verify' o 'implement'"
        )
    return " | ".join(hints) or "sin contexto suficiente; investiga en la capa TRACE"


def _compose_error_context(
    last_error: str = "", last_error_strategy: str = "", last_reason: str = ""
) -> str:
    """Compone el error_context desde claves genéricas que el motor ya expone.

    El motor persiste last_error / last_error_strategy (SubmitOrchestrator)
    y last_reason (TransitionUseCase) para CUALQUIER nodo — sin conocer REFINE.
    """
    parts = []
    if last_error:
        parts.append(f"error={last_error}")
    if last_error_strategy and last_error_strategy != "none":
        parts.append(f"strategy={last_error_strategy}")
    if last_reason:
        parts.append(f"reason={last_reason}")
    return " | ".join(parts)


def build_reflection_context(
    chain_dir: str,
    task_id: str = "",
    last_error: str = "",
    last_error_strategy: str = "",
    last_reason: str = "",
    failed_gates: str = "",
) -> dict:
    """Construye el paquete de evidencia para REFINE.DIAGNOSE.

    Deriva los metadatos REFINE (source_node, attempt, error_context) desde
    el TraceGraph y el contexto genérico del estado — el motor no inyecta nada.

    Args:
        chain_dir: ruta del sprint.
        task_id: task que se está refinando.
        last_error: state.context.last_error (persistido genéricamente por el motor).
        last_error_strategy: state.context.last_error_strategy.
        last_reason: state.context.last_reason (reason de la transición).
        failed_gates: gates fallidos del paso anterior (si aplica).

    Returns:
        Dict con el paquete de reflexión listo para formatear.
    """
    # Unicidad garantizada por el repositorio (ADR-WALKER-018): load_traces
    # devuelve eventos deduped. La capa de aplicacion no compensa; solo
    # normaliza el modelo (dataclass -> dict) para el paquete de evidencia.
    events = [ev.to_dict() for ev in load_traces(chain_dir)]
    if task_id:
        task_events = [
            e for e in events if task_id in str(e.get("task_id", ""))
        ]
    else:
        task_events = events
    errors = [e for e in task_events if e.get("event_type") == "ERROR"]
    submits = [e for e in task_events if e.get("event_type") == "SUBMIT"]
    transitions = [e for e in task_events if e.get("event_type") == "TRANSITION"]
    auto_adv = [e for e in task_events if e.get("event_type") == "AUTO_ADVANCE"]

    source_node = _derive_source_node(transitions)
    attempt = _derive_attempt(transitions)
    error_context = _compose_error_context(
        last_error=last_error,
        last_error_strategy=last_error_strategy,
        last_reason=last_reason,
    )

    past_attempts: list[dict] = []
    for ev in errors:
        past_attempts.append(
            {
                "type": "ERROR",
                "timestamp": ev.get("timestamp", ev.get("started_at", "")),
                "detail": _fmt_error_detail(ev),
            }
        )
    for ev in submits:
        past_attempts.append(
            {
                "type": "SUBMIT",
                "timestamp": ev.get("timestamp", ev.get("started_at", "")),
                "detail": _fmt_submit_detail(ev),
            }
        )
    for ev in auto_adv:
        d = ev.get("data") or {}
        past_attempts.append(
            {
                "type": "AUTO_ADVANCE",
                "timestamp": ev.get("timestamp", ev.get("started_at", "")),
                "detail": (
                    f"auto-advance: {d.get('from','')} -> {d.get('to','')}"
                    if d
                    else "auto-advance"
                ),
            }
        )
    past_attempts.sort(key=lambda x: x["timestamp"])

    transition_gates = [
        (ev.get("data") or {}).get("gate", "")
        for ev in transitions
    ]

    return {
        "task_id": task_id,
        "source_node": source_node,
        "attempt": attempt or 1,
        "max_attempts": DEFAULT_MAX_ATTEMPTS,
        "error_context": error_context,
        "failed_gates": failed_gates,
        "past_attempts": past_attempts[-10:],
        "transition_gates": [g for g in transition_gates if g][-5:],
        "hint": _build_hint(source_node, errors, failed_gates),
    }


def format_reflection_context(package: dict) -> str:
    """Formatea el paquete de reflexión como texto estructurado."""
    if not package:
        return "(no reflection context)"

    lines: list[str] = []
    lines.append(f"  task: {package.get('task_id', '')}")
    lines.append(f"  source_node: {package.get('source_node', '')}")
    lines.append(
        f"  attempt: {package.get('attempt')}/{package.get('max_attempts')}"
    )
    if package.get("error_context"):
        lines.append(f"  error_context: {package['error_context'][:200]}")
    if package.get("failed_gates"):
        lines.append(f"  failed_gates: {package['failed_gates']}")

    past = package.get("past_attempts") or []
    if past:
        lines.append("  past_attempts:")
        for p in past:
            ts = (p.get("timestamp") or "")[:19]
            lines.append(f"    [{ts}] {p.get('type')}: {p.get('detail','')[:140]}")

    gates = package.get("transition_gates") or []
    if gates:
        lines.append(f"  gates usadas: {', '.join(gates)}")

    if package.get("hint"):
        lines.append(f"  hint: {package['hint']}")

    return "\n".join(lines)
