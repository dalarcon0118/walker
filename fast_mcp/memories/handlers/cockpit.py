"""handlers/cockpit.py — Cockpit del desarrollador (ADR-WALKER-084, SPR-COCKPIT).

Vista única (≤30 líneas) que compone `brief` + `session_context` + `plan_diff`
en texto humano, sin JSON. Reusa datos existentes: cero storage nuevo.

Contrato:
  - `handle_cockpit(ctx, **kwargs)` devuelve `{"type": "cockpit", "text": str, "lines": int}`.
  - `handle_decisions_view(ctx, **kwargs)` devuelve `{"type": "decisions_view", "text": str, "lines": int, "decisions": list}`.
  - `text` es ≤30 líneas y ≤1200 chars.
  - Si una fuente cae, la sección se omite y se declara en `missing`.
  - `degraded` es True si alguna fuente cayó.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.memories.context import MemoryContext


def handle_cockpit(ctx: MemoryContext, **kwargs: Any) -> dict[str, Any]:
    """Cockpit del desarrollador: una vista, una pantalla."""
    body = kwargs.get("_payload", kwargs) or {}
    session_id = str(body.get("session_id") or "")
    file = str(body.get("file") or "")
    files = [str(f) for f in (body.get("files") or []) if f]
    task_id = str(body.get("task_id") or "")

    missing: list[str] = []
    degraded = False
    lines: list[str] = []

    # ── 1. Brief: sprint, tarea, paso, verificación, decisiones ──
    brief: dict[str, Any] = {}
    try:
        from fast_mcp.memories.handlers.knowledge import handle_brief

        brief = handle_brief(
            ctx,
            _payload={"session_id": session_id, "file": file, "files": files, "task_id": task_id},
        )
        if brief.get("error"):
            missing.append(f"brief: {brief['error']}")
            degraded = True
    except Exception as exc:  # noqa: BLE001
        missing.append(f"brief: {type(exc).__name__}: {exc}")
        degraded = True

    gates = brief.get("gates") or {}
    step = brief.get("step") or {}
    plan = brief.get("plan") or {}
    self_state = brief.get("self") or {}
    decisions = brief.get("decisions") or []
    impact = brief.get("impact") or {}

    sprint = str(gates.get("sprint") or "")
    task = str(gates.get("task") or "")
    scope = list(gates.get("scope") or [])
    next_action = str(gates.get("next") or "")
    position = str(step.get("position") or "")
    exec_type = str(step.get("execution") or "")

    if sprint:
        lines.append(f"━━━ {sprint} ━━━")
    else:
        lines.append("━━━ (sin sprint) ━━━")
    if task:
        scope_str = f"{len(scope)} archivos" if scope else "sin alcance"
        criteria = len(plan.get("acceptance_criteria") or []) if isinstance(plan, dict) else 0
        lines.append(f"Tarea: {task} · alcance: {scope_str} · criterios: {criteria}")

    if position:
        lines.append(f"Paso: {position}" + (f" ({exec_type})" if exec_type else ""))
    if next_action:
        lines.append(f"Próximo: {next_action[:80]}")

    writes = self_state.get("writes", 0)
    verified = self_state.get("verified")
    denials = (self_state.get("denials") or {}).get("total", 0)
    v_str = "verde" if verified is True else ("rojo" if verified is False else "sin verif")
    lines.append(f"Verificación: {writes} writes · {v_str} · {denials} denials")

    gaps = self_state.get("gaps") or []
    if gaps:
        lines.append("Gaps:")
        for g in gaps[:3]:
            detail = g.get("detail", g) if isinstance(g, dict) else str(g)
            remedy = g.get("remedy", "") if isinstance(g, dict) else ""
            lines.append(f"  · {detail}" + (f" → {remedy}" if remedy else ""))

    targets = files or ([file] if file else []) or scope
    matched = [d for d in decisions if d.get("matched")]
    if matched:
        lines.append("Decisiones que aplican:")
        for d in matched[:3]:
            lines.append(f"  · {d.get('id', '?')} [{d.get('state', '?')}] {str(d.get('title', ''))[:60]}")
    elif decisions:
        lines.append(f"Decisiones (sin match de archivo): {len(decisions)}")

    if impact.get("callers") is not None and targets:
        lines.append(f"Impacto: {impact.get('callers', 0)} llamadores · riesgo {impact.get('risk', '?')}")

    # ── 2. Session context ──
    session_ctx: dict[str, Any] = {}
    if session_id:
        try:
            from fast_mcp.memories.handlers.knowledge import handle_session_context

            session_ctx = handle_session_context(ctx, _payload={"session_id": session_id})
            if session_ctx.get("error") or session_ctx.get("trace_error"):
                missing.append(f"session_context: {session_ctx.get('error') or session_ctx.get('trace_error')}")
                degraded = True
        except Exception as exc:  # noqa: BLE001
            missing.append(f"session_context: {type(exc).__name__}: {exc}")
            degraded = True
    else:
        missing.append("session_context: sin session_id")
        degraded = True

    session = session_ctx.get("session") or {}
    sprint = session_ctx.get("sprint") or {}
    tasks = session_ctx.get("tasks") or []

    if sprint.get("goal"):
        lines.append(f"Sprint goal: {str(sprint['goal'])[:70]}")
    if tasks:
        done = sum(1 for t in tasks if str(t.get("status", "")).lower() in ("completed", "done"))
        lines.append(f"Tareas: {done}/{len(tasks)} completadas")

    # ── 3. Plan diff ──
    if session_id:
        try:
            from fast_mcp.memories.handlers.knowledge import handle_plan_diff

            diff = handle_plan_diff(ctx, _payload={"session_id": session_id, "task_id": task_id})
            if not diff.get("error"):
                unverified = diff.get("unverified") or []
                expanded = diff.get("scope_expanded") or []
                if unverified:
                    lines.append(f"Criterios sin predicado: {len(unverified)}")
                if expanded:
                    lines.append(f"Archivos ampliados: {len(expanded)}")
                missing.extend(diff.get("missing") or [])
        except Exception as exc:  # noqa: BLE001
            missing.append(f"plan_diff: {type(exc).__name__}: {exc}")
            degraded = True

    if missing:
        lines.append(f"Falta: {'; '.join(missing[:3])}")

    text = "\n".join(lines[:30])
    if len(text) > 1200:
        text = text[:1197] + "…"

    return {
        "type": "cockpit",
        "text": text,
        "lines": text.count("\n") + 1,
        "missing": missing,
        "degraded": degraded or bool(missing),
    }


def handle_decisions_view(ctx: MemoryContext, **kwargs: Any) -> dict[str, Any]:
    """Vista humana: decisiones CKG que aplican a un archivo (SPR-COCKPIT SPEC-005)."""
    body = kwargs.get("_payload", kwargs) or {}
    file = str(body.get("file") or "")
    files = [str(f) for f in (body.get("files") or []) if f]
    session_id = str(body.get("session_id") or "")
    targets = files or ([file] if file else [])

    missing: list[str] = []
    degraded = False

    brief: dict[str, Any] = {}
    try:
        from fast_mcp.memories.handlers.knowledge import handle_brief

        brief = handle_brief(
            ctx,
            _payload={"session_id": session_id, "file": file, "files": files},
        )
        if brief.get("error"):
            missing.append(f"brief: {brief['error']}")
            degraded = True
    except Exception as exc:  # noqa: BLE001
        missing.append(f"brief: {type(exc).__name__}: {exc}")
        degraded = True

    decisions = list(brief.get("decisions") or [])
    lines: list[str] = []

    if file:
        lines.append(f"Decisiones que aplican a: {file}")
    elif files:
        lines.append(f"Decisiones que aplican a: {', '.join(files[:3])}")
    else:
        lines.append("Decisiones del proyecto")

    matched = [d for d in decisions if d.get("matched")]
    unmatched = [d for d in decisions if not d.get("matched")]

    if matched:
        lines.append(f"  {len(matched)} matchean el archivo:")
        for d in matched:
            applies = ", ".join(d.get("applies_to") or [])
            lines.append(f"  · {d.get('id', '?')} [{d.get('state', '?')}] {str(d.get('title', ''))[:70]}")
            if applies:
                lines.append(f"    applies_to: {applies}")
            if d.get("supersedes"):
                lines.append(f"    supersedes: {', '.join(d['supersedes'][:3])}")
    if unmatched:
        lines.append(f"  {len(unmatched)} del área (sin match de archivo):")
        for d in unmatched[:5]:
            lines.append(f"  · {d.get('id', '?')} [{d.get('state', '?')}] {str(d.get('title', ''))[:70]}")

    if not decisions:
        lines.append("  (ninguna decisión recuperada para este ancla)")

    if missing:
        lines.append(f"Falta: {'; '.join(missing[:2])}")

    text = "\n".join(lines[:30])
    if len(text) > 1200:
        text = text[:1197] + "…"

    return {
        "type": "decisions_view",
        "text": text,
        "lines": text.count("\n") + 1,
        "decisions": decisions,
        "matched_count": len(matched),
        "missing": missing,
        "degraded": degraded or bool(missing),
    }


def handle_what_changed(ctx: MemoryContext, **kwargs: Any) -> dict[str, Any]:
    """Vista humana: qué cambió en la sesión (SPR-COCKPIT SPEC-006).

    Compone `session_context` + `plan_diff` en texto humano: archivos tocados,
    tareas, criterios sin predicado, archivos fuera del alcance, verificación.
    """
    body = kwargs.get("_payload", kwargs) or {}
    session_id = str(body.get("session_id") or "")
    task_id = str(body.get("task_id") or "")

    missing: list[str] = []
    degraded = False
    lines: list[str] = []

    if not session_id:
        return {"type": "what_changed", "text": "session_id requerido", "lines": 1,
                "missing": ["session_id"], "degraded": True}

    lines.append(f"Cambios en la sesión: {session_id}")

    # 1. session_context
    ctx_data: dict[str, Any] = {}
    try:
        from fast_mcp.memories.handlers.knowledge import handle_session_context

        ctx_data = handle_session_context(ctx, _payload={"session_id": session_id})
        if ctx_data.get("error") or ctx_data.get("trace_error"):
            missing.append(f"session_context: {ctx_data.get('error') or ctx_data.get('trace_error')}")
            degraded = True
    except Exception as exc:  # noqa: BLE001
        missing.append(f"session_context: {type(exc).__name__}: {exc}")
        degraded = True

    files = list(ctx_data.get("files") or [])
    tasks = list(ctx_data.get("tasks") or [])
    flight = ctx_data.get("flight_state") or {}
    sprint = ctx_data.get("sprint") or {}

    if sprint.get("goal"):
        lines.append(f"Sprint: {str(sprint['goal'])[:60]} ({sprint.get('status', '?')})")
    if tasks:
        done = sum(1 for t in tasks if str(t.get("status", "")).lower() in ("completed", "done"))
        lines.append(f"Tareas: {done}/{len(tasks)} completadas")
    lines.append(f"Archivos tocados: {len(files)}")
    for f in files[:8]:
        lines.append(f"  · {f}")
    if len(files) > 8:
        lines.append(f"  … y {len(files) - 8} más")

    writes = flight.get("writes", 0)
    verified = flight.get("verified")
    denials = (flight.get("denials") or {}).get("total", 0)
    v_str = "verde" if verified is True else ("rojo" if verified is False else "sin verif")
    lines.append(f"Verificación: {writes} writes · {v_str} · {denials} denials")

    # 2. plan_diff
    try:
        from fast_mcp.memories.handlers.knowledge import handle_plan_diff

        diff = handle_plan_diff(ctx, _payload={"session_id": session_id, "task_id": task_id})
        if not diff.get("error"):
            unverified = diff.get("unverified") or []
            expanded = diff.get("scope_expanded") or []
            missing_from_plan = diff.get("spec_files_missing_from_plan") or []
            if unverified:
                lines.append(f"Criterios sin predicado: {len(unverified)}")
                for u in unverified[:3]:
                    lines.append(f"  · {str(u)[:70]}")
            if expanded:
                lines.append(f"Archivos fuera del alcance: {len(expanded)}")
                for f in expanded[:5]:
                    lines.append(f"  · {f}")
            if missing_from_plan:
                lines.append(f"Archivos de la spec no tocados: {len(missing_from_plan)}")
            missing.extend(diff.get("missing") or [])
    except Exception as exc:  # noqa: BLE001
        missing.append(f"plan_diff: {type(exc).__name__}: {exc}")
        degraded = True

    if missing:
        lines.append(f"Falta: {'; '.join(missing[:3])}")

    text = "\n".join(lines[:30])
    if len(text) > 1200:
        text = text[:1197] + "…"

    return {
        "type": "what_changed",
        "text": text,
        "lines": text.count("\n") + 1,
        "files": files,
        "missing": missing,
        "degraded": degraded or bool(missing),
    }


def handle_session_summary(ctx: MemoryContext, **kwargs: Any) -> dict[str, Any]:
    """Vista humana: resumen de una sesión (SPR-COCKPIT SPEC-007).

    Objetivo, archivos, tests, decisiones, próximo. Compone `session_context`
    + `brief` + `plan_diff`.
    """
    body = kwargs.get("_payload", kwargs) or {}
    session_id = str(body.get("session_id") or "")
    task_id = str(body.get("task_id") or "")
    file = str(body.get("file") or "")

    missing: list[str] = []
    degraded = False
    lines: list[str] = []

    if not session_id:
        return {"type": "session_summary", "text": "session_id requerido", "lines": 1,
                "missing": ["session_id"], "degraded": True}

    lines.append(f"Resumen de sesión: {session_id}")

    # 1. session_context
    ctx_data: dict[str, Any] = {}
    try:
        from fast_mcp.memories.handlers.knowledge import handle_session_context

        ctx_data = handle_session_context(ctx, _payload={"session_id": session_id})
        if ctx_data.get("error") or ctx_data.get("trace_error"):
            missing.append(f"session_context: {ctx_data.get('error') or ctx_data.get('trace_error')}")
            degraded = True
    except Exception as exc:  # noqa: BLE001
        missing.append(f"session_context: {type(exc).__name__}: {exc}")
        degraded = True

    session = ctx_data.get("session") or {}
    sprint = ctx_data.get("sprint") or {}
    tasks = list(ctx_data.get("tasks") or [])
    files = list(ctx_data.get("files") or [])
    flight = ctx_data.get("flight_state") or {}
    trace = ctx_data.get("trace") or {}

    goal = str(session.get("goal") or sprint.get("goal") or "")
    if goal:
        lines.append(f"Objetivo: {goal[:80]}")
    if sprint.get("spring_id"):
        lines.append(f"Sprint: {sprint['spring_id']} ({sprint.get('status', '?')})")
    if tasks:
        done = sum(1 for t in tasks if str(t.get("status", "")).lower() in ("completed", "done"))
        lines.append(f"Tareas: {done}/{len(tasks)} completadas")
        for t in tasks[:3]:
            lines.append(f"  · {t.get('spec_id', '?')} [{t.get('status', '?')}] {str(t.get('objective', ''))[:50]}")
    lines.append(f"Archivos: {len(files)}")

    writes = flight.get("writes", 0)
    verified = flight.get("verified")
    denials = (flight.get("denials") or {}).get("total", 0)
    v_str = "verde" if verified is True else ("rojo" if verified is False else "sin verif")
    lines.append(f"Verificación: {writes} writes · {v_str} · {denials} denials")
    touched = flight.get("touched") or []
    if touched:
        lines.append(f"Touchados: {', '.join(str(t) for t in touched[:5])}")

    if trace.get("count"):
        lines.append(f"Trace: {trace['count']} eventos · {', '.join(trace.get('types', [])[:5])}")

    # 2. brief: decisiones + próximo
    try:
        from fast_mcp.memories.handlers.knowledge import handle_brief

        brief = handle_brief(ctx, _payload={"session_id": session_id, "file": file})
        if brief.get("error"):
            missing.append(f"brief: {brief['error']}")
            degraded = True
        else:
            decisions = brief.get("decisions") or []
            lines.append(f"Decisiones: {len(decisions)}")
            gates = brief.get("gates") or {}
            if gates.get("next"):
                lines.append(f"Próximo: {str(gates['next'])[:80]}")
    except Exception as exc:  # noqa: BLE001
        missing.append(f"brief: {type(exc).__name__}: {exc}")
        degraded = True

    # 3. plan_diff: criterios sin predicado
    try:
        from fast_mcp.memories.handlers.knowledge import handle_plan_diff

        diff = handle_plan_diff(ctx, _payload={"session_id": session_id, "task_id": task_id})
        if not diff.get("error"):
            unverified = diff.get("unverified") or []
            if unverified:
                lines.append(f"Criterios sin predicado: {len(unverified)}")
            missing.extend(diff.get("missing") or [])
    except Exception as exc:  # noqa: BLE001
        missing.append(f"plan_diff: {type(exc).__name__}: {exc}")
        degraded = True

    if missing:
        lines.append(f"Falta: {'; '.join(missing[:3])}")

    text = "\n".join(lines[:30])
    if len(text) > 1200:
        text = text[:1197] + "…"

    return {
        "type": "session_summary",
        "text": text,
        "lines": text.count("\n") + 1,
        "missing": missing,
        "degraded": degraded or bool(missing),
    }
