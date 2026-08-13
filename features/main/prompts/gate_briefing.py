"""prompts/gate_briefing.py — Briefing estándar para USER GATES.

Arma el bloque "QUÉ SUCEDIÓ / POR QUÉ / SIGUIENTE PASO" que todos los
user gates deben presentar al PO. Combina:
  - datos del paso anterior (state.last_step_fields)
  - evidencia cruda (motor de evidencia)
  - opciones de decisión del gate

Los templates de gate (.prompt.json) incluyen este briefing vía
placeholders {gate_briefing} o por composición.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


def _load_template() -> str:
    """Carga el template gate-briefing.md desde el root canónico de templates.

    Root único: features/prompts/templates/ (subpath gates/).
    Fallbacks: ubicación legacy (templates/gate-briefing.md) y constante.
    """
    tpl_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    candidates = [
        os.path.join(tpl_dir, "gates", "gate-briefing.md"),
        os.path.join(tpl_dir, "gate-briefing.md"),
    ]
    for path in candidates:
        result = _read_gate_template_core(path)
        if is_successful(result):
            return result.value_or("")
        logger.debug(
            "gate template %s ilegible (%s); probando siguiente",
            path, result.failure(),
        )
    return _FALLBACK_TEMPLATE


_FALLBACK_TEMPLATE = (
    "🎯 GATE — REVISIÓN {gate_name}\n"
    "Task: {task_id} — {task_title}\n\n"
    "📋 QUÉ SUCEDIÓ\n{what_happened}\n\n"
    "🔍 POR QUÉ / EVIDENCIA\n{evidence}\n\n"
    "➡️ TU SIGUIENTE PASO\n{options}\n"
)


def _fmt(v: Any, max_len: int = 400) -> str:
    if v is None:
        return "(sin datos)"
    s = str(v)
    return s[:max_len] + "..." if len(s) > max_len else s


def _options_from_gates(gates: list[dict]) -> str:
    """Arma las opciones de decisión desde las gates disponibles."""
    if not gates:
        return "  (no hay gates disponibles)"
    lines = []
    for g in gates:
        name = g.get("name", "?")
        target = g.get("target", "")
        desc = g.get("description", "")
        lines.append(f"  . {name} → {desc} [gate: {name}]")
        if target:
            lines.append(f"      destino: {target}")
    return "\n".join(lines)


def render_gate_briefing(
    *,
    gate_name: str = "",
    task_id: str = "",
    task_title: str = "",
    node: str = "",
    sub_state: str = "",
    sprint_goal: str = "",
    what_happened: str = "",
    evidence: str = "",
    options: str = "",
    gates: list[dict] | None = None,
) -> str:
    """Renderiza el briefing estándar de un user gate.

    Si `options` está vacío pero se pasan `gates`, las arma desde ahí.
    """
    data = {
        "gate_name": gate_name or "REVISIÓN",
        "task_id": task_id or "?",
        "task_title": task_title or "",
        "node": node or "",
        "sub_state": sub_state or "",
        "sprint_goal": sprint_goal or "",
        "what_happened": _fmt(what_happened),
        "evidence": _fmt(evidence, max_len=1200),
        "options": options or _options_from_gates(gates or []),
    }
    template = _load_template()
    result = _render_gate_core(template, data)
    if is_successful(result):
        return result.value_or(template)
    # Degradacion observable: el briefing crudo (sin render) es mejor que
    # romper el gate; el fallo queda registrado (convention-001).
    logger.warning(
        "render del gate briefing fallo (%s); se devuelve el template crudo",
        result.failure(),
    )
    return template


def what_happened_from_state(last_fields: dict | None) -> str:
    """Sintetiza 'QUÉ SUCEDIÓ' desde los campos del submit anterior.

    Detecta patrones comunes:
      - Q&A del ANALYZE (q1/a1...) → resume respuestas
      - decision + feedback (gates de aprobación)
      - intent (transiciones XOR)
      - task_data / analysis (gathers)
    """
    lf = last_fields or {}
    if not lf:
        return "(no hay resultado del paso anterior)"

    # Caso Q&A del ANALYZE 6→3
    qs = [lf.get(f"q{i}") for i in (1, 2, 3)]
    as_ = [lf.get(f"a{i}") for i in (1, 2, 3)]
    if any(qs) or any(as_):
        lines = ["Se completó el análisis 6→3 de la tarea:"]
        for i in (1, 2, 3):
            q = lf.get(f"q{i}")
            a = lf.get(f"a{i}")
            if q:
                lines.append(f"  Q{i}: {_fmt(q, 200)}")
            if a:
                lines.append(f"  A{i}: {_fmt(a, 300)}")
        return "\n".join(lines)

    # Caso decisión (gate de aprobación anterior)
    if lf.get("decision"):
        decision = lf["decision"]
        feedback = lf.get("feedback", "")
        line = f"Decisión registrada: {decision}"
        if feedback:
            line += f" — {_fmt(feedback, 200)}"
        return line

    # Caso intent (XOR)
    if lf.get("intent"):
        return f"Intención del paso: {lf['intent']}"

    # Caso data/analysis
    for key in ("task_data", "analysis", "result"):
        if lf.get(key):
            return f"{key}: {_fmt(lf[key], 300)}"

    # Genérico
    pairs = [f"{k}: {_fmt(v, 120)}" for k, v in list(lf.items())[:5]]
    return "\n".join(pairs) if pairs else "(sin resultado)"


# ── Cores @safe (Result pattern, convention-001) ─────────────────

@safe
def _read_gate_template_core(path: str) -> str:
    """Core puro: lee el template de gate briefing (puede lanzar)."""
    with open(path) as f:
        return f.read()


@safe
def _render_gate_core(template: str, data: dict) -> str:
    """Core puro: renderiza el briefing (puede lanzar)."""
    from features.main.prompts.renderer import TemplateRenderer

    return TemplateRenderer().render(template, data)
