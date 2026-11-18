"""application/prompt_context.py — datos que los templates esperan y nadie llenaba.

Los `.prompt.json` usan `{what_happened}`, `{step_quality_*}`, `{task_table}`,
`{risk_table}`, `{predicates}`, `{decisions_list}` y las variables de
reflexión (`{source_node}`, `{attempt}`, `{error_context}`, ...). El
`PromptManager` solo renderiza lo que recibe: si nadie arma esas claves, el
placeholder queda vacío y el agente ve un briefing con secciones en blanco
(medido 2026-11-18 en todos los gates `user_input`).

Este módulo arma el `extra_data` desde datos REALES:
  * el último paso ejecutado (node_executions) → `what_happened` y calidad
  * la spec y el sprint (DeltaGraph) → `task_table`, `risk_table`, ...
  * el TraceGraph → reflexión de REFINE
  * el CKG → decisiones relacionadas

Todo es fail-open: si una fuente no responde, la clave se omite y el template
mantiene su placeholder vacío en vez de inventar contenido.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_WHAT_HAPPENED_MAX = 600
_LIST_LIMIT = 10
_DECISION_FILTERS = {"type": "decision"}


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        try:
            return dict(value.to_dict())
        except Exception:  # noqa: BLE001
            return {}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _executions(state: Any) -> list:
    execs = getattr(state, "node_executions", None)
    if execs is None and isinstance(state, dict):
        execs = state.get("node_executions")
    return list(execs or [])


def _execution_data(execution: Any) -> dict:
    """`task_result.data` si existe; si no, los campos enviados."""
    entry = _as_dict(execution)
    result = entry.get("task_result")
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, dict) and data:
            return data
    fields = entry.get("submitted_fields")
    return fields if isinstance(fields, dict) else {}


def _first(data: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def last_step_summary(state: Any) -> str:
    """`what_happened`: qué hizo el paso anterior, desde su resultado real."""
    execs = _executions(state)
    if not execs:
        return ""
    entry = _as_dict(execs[-1])
    data = _execution_data(execs[-1])
    action = str(entry.get("action") or "").strip()
    where = ".".join(
        str(part) for part in (entry.get("node"), entry.get("sub_state")) if part
    )
    detail = _first(
        data,
        ("summary", "statement", "message", "fact", "decision", "status", "result"),
    )
    if not detail and isinstance(data.get("result"), dict):
        detail = _first(data["result"], ("summary", "message", "status"))
    error = str(entry.get("error") or "").strip()
    ok = entry.get("task_ok")
    head = action or where or "paso"
    if ok is False or error:
        head = f"{head} (falló)"
    parts = [f"{head} — {where}" if where and action else head]
    if detail:
        parts.append(detail)
    if error:
        parts.append(f"error: {error}")
    files = data.get("files")
    if isinstance(files, list) and files:
        parts.append("archivos: " + ", ".join(str(f) for f in files[:_LIST_LIMIT]))
    text = " · ".join(parts)
    return text[:_WHAT_HAPPENED_MAX]


def quality_context(state: Any) -> dict[str, Any]:
    """Calidad del paso anterior (`step_quality_*` / `step_failed_gates`).

    Sale de la última ejecución que reportó `passed` o `findings` (por ejemplo
    CODE_REVIEW_SCAN). Sin ese dato, no se inventa un "OK".
    """
    for execution in reversed(_executions(state)):
        data = _execution_data(execution)
        if "passed" not in data and "findings" not in data:
            continue
        passed = data.get("passed")
        findings = data.get("findings") or []
        listed = []
        for finding in findings if isinstance(findings, list) else []:
            if isinstance(finding, dict):
                listed.append(
                    str(
                        finding.get("message")
                        or finding.get("detail")
                        or finding.get("rule")
                        or finding
                    )
                )
            else:
                listed.append(str(finding))
        return {
            "step_quality_passed": "sí" if passed else "no",
            "step_quality_summary": _first(data, ("summary", "message", "status"))
            or ("sin hallazgos" if passed else "revisión con hallazgos"),
            "step_failed_gates": "; ".join(listed[:_LIST_LIMIT]) or "(ninguno)",
            "step_recommend": "continuar"
            if passed
            else "corregir los hallazgos antes de avanzar",
        }
    return {}


def spec_context(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Datos de la tarea para `task_table` / `risk_table` / `predicates`."""
    if not spec:
        return {}
    out: dict[str, Any] = {}
    rows = [
        f"Tarea: {spec.get('spec_id', '')} · tipo: {spec.get('spec_type', '')} · "
        f"dominio: {spec.get('domain') or '(sin dominio)'} · estado: {spec.get('status', '')}",
        f"Objetivo: {spec.get('objective') or '(sin objetivo)'}",
    ]
    problem = str(spec.get("problem_statement") or "").strip()
    if problem:
        rows.append(f"Problema: {problem}")
    files = spec.get("files") or []
    if files:
        rows.append("Archivos:\n" + "\n".join(f"  - {f}" for f in files[:_LIST_LIMIT]))
    acceptance = spec.get("acceptance_criteria") or []
    if acceptance:
        rows.append(
            "Criterios:\n" + "\n".join(f"  - {a}" for a in acceptance[:_LIST_LIMIT])
        )
    out["task_table"] = "\n".join(rows)

    risks = spec.get("risks")
    risk_items: list[str] = []
    if isinstance(risks, dict):
        risk_items = [f"{k}: {v}" for k, v in risks.items() if v]
    elif isinstance(risks, list):
        risk_items = [str(r) for r in risks if r]
    out["risk_table"] = (
        "Riesgos:\n" + "\n".join(f"  - {r}" for r in risk_items[:_LIST_LIMIT])
        if risk_items
        else "Riesgos: (ninguno declarado)"
    )

    items = _predicate_items(spec.get("predicates"))
    if items:
        out["predicates"] = "\n".join(f"  - {p}" for p in items[:_LIST_LIMIT])
    return out


def _predicate_items(predicates: Any) -> list[str]:
    items: list[str] = []
    if isinstance(predicates, dict):
        raw = predicates.get("items") or []
    elif isinstance(predicates, list):
        raw = predicates
    else:
        raw = []
    for predicate in raw:
        if isinstance(predicate, dict):
            items.append(
                f"{predicate.get('type')}: "
                f"{predicate.get('path') or predicate.get('target') or ''}"
            )
        elif predicate:
            items.append(str(predicate))
    return items


def decisions_context(chain_dir: str, task_id: str) -> dict[str, Any]:
    """`decisions_list`: decisiones del CKG (de la tarea si se puede filtrar)."""
    try:
        from fast_mcp.composition import ckg_for

        graph = ckg_for(chain_dir)
        if graph is None:
            return {}
        nodes = graph.query(_DECISION_FILTERS)
        # Una sola query: filtrar en Python evita el fallback EAGER por
        # `metadata.spec_id` (materializa todo el read model y loguea).
        if task_id:
            scoped = [
                n for n in nodes
                if str((n.get("metadata") or {}).get("spec_id") or "") == task_id
            ]
            nodes = scoped or nodes
        lines = []
        for node in (nodes or [])[:_LIST_LIMIT]:
            node_id = node.get("_id") or node.get("id") or ""
            label = str(node.get("label") or node.get("statement") or "")[:100]
            lines.append(f"  - {node_id}: {label}".rstrip(": "))
        return {"decisions_list": "\n".join(lines)} if lines else {}
    except Exception as exc:  # noqa: BLE001 — el briefing no depende del CKG
        logger.debug("prompt_context: decisiones no disponibles (%s)", exc)
        return {}


def reflection_context(
    chain_dir: str,
    task_id: str,
    failed_gates: str = "",
    last_error: str = "",
) -> dict[str, Any]:
    """Variables de REFINE desde el TraceGraph (paquete de reflexión)."""
    try:
        from fast_mcp.composition import build_trace_service

        service = build_trace_service(chain_dir)
        if service is None:
            return {}
        package = service.build_reflection_context(
            chain_dir,
            task_id=task_id,
            last_error=last_error,
            failed_gates=failed_gates,
        )
    except Exception as exc:  # noqa: BLE001 — el briefing no depende del trace
        logger.debug("prompt_context: reflexión no disponible (%s)", exc)
        return {}
    if not isinstance(package, dict) or not package:
        return {}
    past = package.get("past_attempts") or []
    formatted = "\n".join(f"  - {str(p)[:160]}" for p in past[-5:])
    return {
        "source_node": package.get("source_node", ""),
        "attempt": package.get("attempt", ""),
        "max_attempts": package.get("max_attempts", ""),
        "error_context": package.get("error_context", ""),
        "failed_gates": package.get("failed_gates", ""),
        "hint": package.get("hint", ""),
        "reflection_past_attempts": formatted,
    }


def sprint_extra(specs: list[dict], spring: dict, task_id: str) -> dict[str, Any]:
    """Datos del sprint y de la spec para los templates de SPRINT/IMPLEMENT."""
    in_flow = ("specified", "approved")
    ready = [s for s in specs if str(s.get("status")) in in_flow and not s.get("depends_on")]
    blocked = [s for s in specs if str(s.get("status")) in in_flow and s.get("depends_on")]
    extra: dict[str, Any] = {
        "sprint_goal": spring.get("goal", ""),
        "goal_display": f"Goal: {spring.get('goal', '')}",
        "task_count": len(specs),
        "completed_count": sum(1 for s in specs if str(s.get("status")) == "completed"),
        "ready_tasks": "\n".join(
            f"- {s.get('spec_id')}: {str(s.get('objective', ''))[:70]}"
            for s in ready[:_LIST_LIMIT]
        )
        or "(ninguna)",
        "blocked_tasks": "\n".join(
            f"- {s.get('spec_id')}: depende de {s.get('depends_on')}"
            for s in blocked[:_LIST_LIMIT]
        )
        or "(ninguna)",
        "task_id": task_id,
    }
    spec = next((s for s in specs if str(s.get("spec_id")) == task_id), None)
    if spec:
        extra.update(
            {
                "task_title": spec.get("objective", ""),
                "task_description": spec.get("problem_statement")
                or spec.get("objective", ""),
                "task_type": spec.get("spec_type", ""),
                "task_domain": spec.get("domain", ""),
                "task_status": spec.get("status", ""),
                "result_state": spec.get("result_state", ""),
                "depends_on": ", ".join(spec.get("depends_on") or []) or "(ninguna)",
                "acceptance_criteria_list": "\n".join(
                    f"- {x}" for x in (spec.get("acceptance_criteria") or [])
                ),
                "files_list": "\n".join(f"- {x}" for x in (spec.get("files") or [])),
                "preconditions_list": "\n".join(
                    f"- {x}" for x in (spec.get("preconditions") or [])
                ),
                "quality_gates_list": "\n".join(
                    f"- {x}" for x in (spec.get("quality_gates") or [])
                ),
                "predicates_list": "\n".join(
                    f"- {p}" for p in _predicate_items(spec.get("predicates"))
                ),
            }
        )
        extra.update(spec_context(spec))
    return extra


def build_prompt_extra(state: Any, chain_dir: str, session_id: str) -> dict[str, Any]:
    """Todo el `extra_data` que los templates pueden usar, sin inventar nada."""
    extra: dict[str, Any] = {}
    extra.update(quality_context(state))
    happened = last_step_summary(state)
    if happened:
        extra["what_happened"] = happened

    task_id = ""
    try:
        from fast_mcp.composition import build_sprint_service

        service = build_sprint_service(chain_dir)
        if service is not None:
            spring_id = ""
            if session_id:
                record = service.get_session(session_id) or {}
                spring_id = str(record.get("spring_id") or "")
            if not spring_id:
                spring_id = service.get_spring_id(chain_dir)
            spring = (service.get_spring(spring_id) if spring_id else None) or {}
            specs = (
                [s for s in (service.get_specs(spring_id) or []) if isinstance(s, dict)]
                if spring_id
                else []
            )
            ctx_data = getattr(state, "context", {}) or {}
            task_id = str(
                (ctx_data.get("task_id") if isinstance(ctx_data, dict) else "")
                or getattr(state, "task_id", "")
                or ""
            )
            extra.update(sprint_extra(specs, spring, task_id))
    except Exception as exc:  # noqa: BLE001 — el prompt no depende del sprint
        logger.debug("prompt_context: sprint no disponible (%s)", exc)

    if task_id:
        extra.update(decisions_context(chain_dir, task_id))
    extra.update(
        reflection_context(
            chain_dir,
            task_id,
            failed_gates=str(extra.get("step_failed_gates") or ""),
        )
    )
    return extra
