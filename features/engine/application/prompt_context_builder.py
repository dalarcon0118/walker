"""application/prompt_context_builder.py — PromptContextBuilder.

SRP: Unica responsabilidad = construir el contexto de placeholders para
los prompts del workflow, desde sprint-state y plan.json.

Produce placeholders como: sprint_goal, task_count, completed_count,
task_table, risk_table, dependency_graph, predicates_list, files_list,
q1-a3 (Q&A del ANALYZE), etc.

Extraido de OrchestrationService (god class).
"""
from __future__ import annotations

import json
import os


class PromptContextBuilder:
    """Construye contexto de placeholders desde sprint-state y plan.json."""

    def build(self, chain_dir: str, task_id: str = "") -> dict:
        """Construye el dict de placeholders para resolver prompts."""
        ctx: dict = {}
        if task_id:
            ctx["task_id"] = task_id

        # project_id desde SessionRecord activo (SSOT)
        from ..domain.session import SessionStore

        record = SessionStore.get_active()
        if record and record.project_id:
            ctx["project_id"] = record.project_id

        # Cargar datos del sprint desde el delta (fuente unica)
        spring_svc = None
        spring_data = None
        tasks: list[dict] = []
        try:
            from features.delta_tracking.application.spring_graph_service import SpringGraphService
            spring_svc = SpringGraphService.from_chain_dir(chain_dir)
            if spring_svc:
                spring_id = os.path.basename(os.path.normpath(chain_dir))
                spring_svc.ensure_spring_imported(chain_dir, spring_id)
                spring_data = spring_svc.get_spring(spring_id)
                tasks = spring_svc.get_specs_legacy(spring_id)
        except Exception:
            pass

        if spring_data:
            if "project_id" not in ctx:
                ctx["project_id"] = spring_data.get("project_id", "")
            ctx["sprint_goal"] = spring_data.get("goal", "")
            ctx["task_count"] = len(tasks)
            ctx["completed_count"] = sum(
                1 for t in tasks if isinstance(t, dict) and t.get("status") == "completed"
            )
            ctx["session_id"] = os.path.basename(chain_dir)

        # Task info para TASK_SPEC / TASK_REVIEW (desde delta — fuente unica)
        current_idx = 0
        try:
            from features.spring.infrastructure.repositories import SprintStateRepository
            sp_data = SprintStateRepository().load_dict(chain_dir)
            current_idx = sp_data.get("context", {}).get("current_task_index", 0) if sp_data else 0
        except Exception:
            pass
        if tasks:
            ctx["total_tasks"] = len(tasks)
            ctx["task_index"] = current_idx
            if current_idx < len(tasks):
                t = tasks[current_idx]
                ctx["task_title"] = t.get("objective", "")
                ctx["task_type"] = t.get("type", "")
                ctx["task_domain"] = t.get("domain", "")
                ctx["result_state"] = t.get("result_state", "")
                ctx["task_description"] = t.get("task_description", "")
                preds = t.get("validation", {}).get("predicates", [])
                ctx["predicates_list"] = (
                    "\n".join(f"    • {p}" for p in preds) if preds else ""
                )
                files = t.get("impacted_artifacts", {}).get("files", [])
                ctx["files_list"] = (
                    "\n".join(f"    • {f}" for f in files) if files else ""
                )
                ctx["depends_on"] = ", ".join(t.get("depends_on", [])) or "(none)"
                ctx["curvature"] = str(t.get("risks", {}).get("curvature", 0.3))

        # goal_display y task_summary formateados
        goal = str(ctx.get("sprint_goal", ""))
        ctx["goal_display"] = f"Goal: {goal}" if goal else "Goal: Not set"
        done = int(ctx.get("completed_count", 0))
        total = int(ctx.get("task_count", 0))
        ctx["task_summary"] = f"{done}/{total} tasks completed"

        # ── tabla de tareas completa para reportes ────────────────
        if tasks:
            # task_table: formato tabular listo para inyectar
            table_lines = []
            table_lines.append(f"{'ID':<10} {'Status':<12} {'Type':<12} {'Domain':<16} {'Objective'}")
            table_lines.append("-" * 80)
            for t in tasks:
                tid = t.get("spec_id", t.get("task_id", ""))
                st = t.get("status", t.get("state", "draft"))
                tp = t.get("type", "")
                dm = t.get("domain", "")
                obj = (t.get("objective", "") or "")[:45]
                table_lines.append(f"{tid:<10} {st:<12} {tp:<12} {dm:<16} {obj}")
            ctx["task_table"] = "\n".join(table_lines)

            # risk_table: riesgos por tarea
            risk_lines = []
            risk_lines.append(f"{'ID':<10} {'Type':<12} {'Curvature':<12} {'Pre-Churn':<12} {'Commit-Trust':<12}")
            risk_lines.append("-" * 58)
            for t in tasks:
                tid = t.get("spec_id", t.get("task_id", ""))
                tp = t.get("type", "")
                risks = t.get("risks", {})
                cv = risks.get("curvature", "-")
                pc = risks.get("pre_churn", "-")
                ct = risks.get("commit_trust", "-")
                risk_lines.append(f"{tid:<10} {tp:<12} {str(cv):<12} {str(pc):<12} {str(ct):<12}")
            ctx["risk_table"] = "\n".join(risk_lines)

            # dependency_graph: dependencias entre tareas
            dep_lines = []
            for t in tasks:
                tid = t.get("spec_id", t.get("task_id", ""))
                deps = t.get("depends_on", [])
                if deps:
                    dep_lines.append(f"  {tid} → {', '.join(deps)}")
            ctx["dependency_graph"] = "\n".join(dep_lines) if dep_lines else "  (no dependencies)"

            ctx["total_tasks"] = len(tasks)
            ctx["all_tasks_json"] = json.dumps(tasks, indent=2, ensure_ascii=False)

            # ── Datos de la tarea actual para ANALYZE/REVIEW ──
            if tasks:
                if task_id:
                    matching = [t for t in tasks if t.get("spec_id", t.get("task_id")) == task_id]
                    t_current = matching[0] if matching else tasks[0]
                else:
                    t_current = tasks[0]
                ctx["task_status"] = t_current.get("status", t_current.get("state", "draft"))
                ctx["result_state"] = t_current.get("result_state", "")
                ctx["preconditions_list"] = "\n".join(
                    f"    • {p}" for p in (t_current.get("preconditions", []) or [])
                ) or "    (none)"
                ctx["acceptance_criteria_list"] = "\n".join(
                    f"    • {a}" for a in (t_current.get("acceptance_criteria", []) or [])
                ) or "    (none)"
                ctx["quality_gates_list"] = "\n".join(
                    f"    • {q}" for q in (t_current.get("quality_gates", []) or [])
                ) or "    (none)"
                decisions = t_current.get("decisions", []) or []
                ctx["decisions_list"] = "\n".join(
                    f"    • {d.get('title', '')}: {d.get('rationale', '')}"
                    for d in decisions
                ) or "    (none)"
                ctx["curvature"] = str(t_current.get("risks", {}).get("curvature", ""))
                ctx["pre_churn"] = str(t_current.get("risks", {}).get("pre_churn", ""))
                ctx["commit_trust"] = str(t_current.get("risks", {}).get("commit_trust", ""))

                # ── Q&A del ANALYZE desde sub_state_data (workflow) ──
                ssd = {}
                try:
                    from ..infrastructure.repositories import WorkflowStateRepository
                    wf = WorkflowStateRepository().load(chain_dir)
                    ssd = wf.sub_state_data or {}
                except Exception:
                    pass
                an_entry = ssd.get("ANALYZE.ANALYZE") or ssd.get("ANALYZE")
                an_fields = {}
                if an_entry is not None:
                    an_fields = (
                        an_entry.get("fields", {})
                        if isinstance(an_entry, dict)
                        else (getattr(an_entry, "fields", {}) or {})
                    )
                ctx["q1"] = an_fields.get("q1", "")
                ctx["a1"] = an_fields.get("a1", "")
                ctx["q2"] = an_fields.get("q2", "")
                ctx["a2"] = an_fields.get("a2", "")
                ctx["q3"] = an_fields.get("q3", "")
                ctx["a3"] = an_fields.get("a3", "")

                # ── QUÉ SUCEDIÓ (briefing estándar de user gates) ──
                # Sintetiza el resultado del paso anterior desde los campos
                # del último submit, para que el gate presente al PO qué
                # se hizo antes de pedir la decisión.
                from features.prompts.gate_briefing import what_happened_from_state
                ctx["what_happened"] = what_happened_from_state(an_fields)

                # ── StepResult del paso anterior (patrón monádico) ──
                # Los placeholders step_* (step_quality_passed, step_failed_gates,
                # step_recommend, ...) se leen del último sub_state_data guardado
                # por SubmitUseCase. Así el prompt del siguiente sub-estado ve el
                # resultado tipado del paso anterior sin re-parsear JSON.
                last_step = _read_last_step_result(ssd)
                if last_step:
                    ctx.update(last_step)

        else:
            ctx["task_table"] = "(no tasks in delta)"
            ctx["risk_table"] = ""
            ctx["dependency_graph"] = ""
            ctx["total_tasks"] = 0

        # ── Reflexión para REFINE (SKILL.md: "el engine inyecta source_node,
        #    attempt, error_context") ─────────────────────────────────────
        # El engine puro NO conoce REFINE (doc-18/23/25). El PromptContextBuilder
        # (componente de contexto, doc-5) lee las claves GENÉRICAS que el motor
        # ya persiste para cualquier error (last_error/last_error_strategy/
        # last_reason) y delega en la feature event_tracing para derivar el
        # paquete de evidencia. Así los prompts usan {source_node}, {attempt},
        # {error_context}, {hint} y el LLM no depende de "recordar" una capa.
        try:
            ctx.update(_build_reflection_placeholders(chain_dir, task_id, ctx))
        except Exception:
            pass  # la reflexión es un refuerzo: sin ella el prompt sigue resuelto

        return ctx


def _read_last_step_result(sub_state_data: dict) -> dict:
    """Lee el StepResult persistido (checkpoint tipado) del último sub_state_data.

    SubmitUseCase guarda:
      - step_checkpoint: checkpoint tipado del StepResult (CON tipos)
      - prompt_placeholders: dict step_* para templates

    Este helper:
      1. Reconstruye el StepResult desde el checkpoint con validación
         (preserva bool/list/dict, detecta checkpoints corruptos).
      2. Expone los placeholders step_* (compat con templates).

    Returns:
        dict con las claves step_* (vacío si no hay resultado previo).
    """
    if not isinstance(sub_state_data, dict):
        return {}
    for key, entry in sub_state_data.items():
        if not isinstance(entry, dict):
            continue
        fields = entry.get("fields", {}) or {}
        if not isinstance(fields, dict):
            continue

        # 1. Reconstruir el StepResult desde el checkpoint tipado (validado)
        checkpoint = fields.get("step_checkpoint")
        if isinstance(checkpoint, dict):
            from ..domain.step_result import StepResult
            restored = StepResult.from_checkpoint(checkpoint)
            if restored.ok and restored.data is not None:
                sr = restored.data
                q = sr.signals.get("quality") or {}
                failed = q.get("failed_gates", [])
                return {
                    "step_ok": sr.ok,
                    "step_sub_state": sr.sub_state,
                    "step_advanced_to": sr.advanced_to,
                    "step_node_complete": sr.node_complete,
                    "step_error": sr.error,
                    "step_quality_summary": q.get("summary", ""),
                    "step_quality_passed": q.get("quality_passed"),
                    "step_failed_gates": ", ".join(failed) if failed else "",
                    "step_recommend": q.get("recommend", ""),
                    "step_task_summary": sr.to_prompt_dict().get("step_task_summary", ""),
                    "step_side_effect_summaries": "\n".join(
                        s for s in sr.to_prompt_dict().get("step_side_effect_summaries", "").split("\n") if s
                    ),
                }

        # 2. Fallback: prompt_placeholders (compat con guardados previos)
        placeholders = fields.get("prompt_placeholders")
        if isinstance(placeholders, dict):
            step_keys = {k: v for k, v in placeholders.items() if k.startswith("step_")}
            if step_keys:
                return step_keys
    return {}


def _build_reflection_placeholders(chain_dir: str, task_id: str, ctx: dict) -> dict:
    """Deriva los placeholders de reflexión que el engine inyecta en REFINE.

    Lee del sprint-state (fuente del estado que el motor ya persiste para
    CUALQUIER error: last_error/last_error_strategy/last_reason) y delega
    en build_reflection_context para derivar source_node/attempt desde el
    TraceGraph. Expone claves planas para los templates:
      source_node, attempt, max_attempts, error_context, failed_gates,
      hint, reflection_past_attempts, reflection_transition_gates.

    Returns:
        dict con las claves reflection_* (vacío si no hay datos).
    """
    from features.event_tracing.application.reflection import (
        build_reflection_context,
    )

    # Claves genéricas del estado (sin conocer REFINE): ya vienen en ctx si
    # el execution_ctx las cargó; si no, se leen del sprint-state.
    last_error = ctx.get("last_error", "")
    last_error_strategy = ctx.get("last_error_strategy", "")
    last_reason = ctx.get("last_reason", "")
    failed_gates = ctx.get("step_failed_gates", "")

    if not (last_error or last_error_strategy or last_reason or failed_gates):
        try:
            from features.spring.infrastructure.repositories import SprintStateRepository
            sp = SprintStateRepository().load_dict(chain_dir)
            sp_ctx = sp.get("context", {}) or {}
            last_error = last_error or sp_ctx.get("last_error", "")
            last_error_strategy = last_error_strategy or sp_ctx.get("last_error_strategy", "")
            last_reason = last_reason or sp_ctx.get("last_reason", "")
            failed_gates = failed_gates or ctx.get("step_failed_gates", "")
        except Exception:
            pass

    try:
        package = build_reflection_context(
            chain_dir=chain_dir,
            task_id=task_id,
            last_error=last_error,
            last_error_strategy=last_error_strategy,
            last_reason=last_reason,
            failed_gates=failed_gates,
        )
    except Exception:
        return {}

    return {
        "source_node": package.get("source_node", ""),
        "attempt": package.get("attempt", 1),
        "max_attempts": package.get("max_attempts", 3),
        "error_context": package.get("error_context", ""),
        "failed_gates": package.get("failed_gates", ""),
        "hint": package.get("hint", ""),
        "reflection_past_attempts": _fmt_past_attempts(package.get("past_attempts", [])),
        "reflection_transition_gates": ", ".join(
            package.get("transition_gates", []) or []
        ),
    }


def _fmt_past_attempts(past_attempts: list) -> str:
    """Formatea los intentos pasados como lista de texto para templates."""
    if not past_attempts:
        return ""
    lines = []
    for p in past_attempts:
        ts = (p.get("timestamp") or "")[:19]
        lines.append(f"    [{ts}] {p.get('type')}: {p.get('detail', '')[:140]}")
    return "\n".join(lines)