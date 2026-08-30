"""features/main/prompts/prompt_context_builder.py — PromptContextBuilder.

INCUMBENCIA DEL HOST: construir el contexto de placeholders para los prompts
del workflow (desde el delta/spring) es un concern del negocio, NO del engine
(ADR-WALKER-067). El engine solo renderiza prompts con la mecánica de su
PromptManager; el contexto lo provee el host (via IPromptCollector o este
builder). Vive en features/main, no en shared.core.engine.
"""
from __future__ import annotations

import json
import os
from typing import Any


class PromptContextBuilder:
    """Construye contexto de placeholders desde el delta (fuente unica)."""

    def build(self, chain_dir: str, task_id: str = "") -> dict:
        """Construye el dict de placeholders para resolver prompts."""
        ctx: dict = {}
        if task_id:
            ctx["task_id"] = task_id

        # project_id desde SessionRecord activo (SSOT)
        try:
            from shared.core.engine.infrastructure.session_store import SessionStore

            record = SessionStore.get_active()
            if record and record.project_id:
                ctx["project_id"] = record.project_id
        except Exception:
            pass

        # Cargar datos del sprint desde el delta (fuente unica)
        spring_data, tasks = _load_spring_data(chain_dir)

        if spring_data:
            if "project_id" not in ctx:
                ctx["project_id"] = spring_data.get("project_id", "")
            ctx["sprint_goal"] = spring_data.get("goal", "")
            ctx["task_count"] = len(tasks)
            ctx["completed_count"] = sum(
                1
                for t in tasks
                if isinstance(t, dict) and t.get("status") == "completed"
            )
            ctx["session_id"] = os.path.basename(chain_dir)

        # Task info para TASK_SPEC / TASK_REVIEW (desde delta — fuente unica)
        current_idx = _load_current_task_index(chain_dir)

        if tasks:
            _fill_task_info(ctx, tasks, current_idx)
            _fill_report_tables(ctx, tasks)
            _fill_queue_ready(ctx, tasks)
            if task_id:
                _fill_current_task_context(ctx, tasks, task_id, chain_dir)

        # goal_display y task_summary formateados
        goal = str(ctx.get("sprint_goal", ""))
        ctx["goal_display"] = f"Goal: {goal}" if goal else "Goal: Not set"
        done = int(ctx.get("completed_count", 0))
        total = int(ctx.get("task_count", 0))
        ctx["task_summary"] = f"{done}/{total} tasks completed"

        if not tasks:
            ctx["task_table"] = "(no tasks in delta)"
            ctx["risk_table"] = ""
            ctx["dependency_graph"] = ""
            ctx["total_tasks"] = 0
            ctx["ready_tasks"] = "  (none)"
            ctx["blocked_tasks"] = ""
            ctx["ready_count"] = 0
            ctx["blocked_count"] = 0

        return ctx


def _load_spring_data(chain_dir: str) -> tuple[dict | None, list[dict]]:
    """Carga spring y tasks desde el delta (fuente unica, host)."""
    try:
        from features.project.application.services.project_service import (
            ProjectService,
        )

        spring_svc = ProjectService.from_chain_dir(chain_dir)
        spring_id = os.path.basename(os.path.normpath(chain_dir))
        spring_svc.ensure_spring_imported(chain_dir, spring_id)
        spring_data = spring_svc.get_spring(spring_id)
        tasks = spring_svc.get_specs_legacy(spring_id)
        return spring_data, tasks
    except Exception:
        pass
    return None, []


def _load_current_task_index(chain_dir: str) -> int:
    """Lee el indice de la tarea actual desde el contexto del spring."""
    current_idx = 0
    try:
        from features.project.application.services.project_service import (
            ProjectService,
        )

        spring_svc = ProjectService.from_chain_dir(chain_dir)
        spring_id = os.path.basename(os.path.normpath(chain_dir))
        spring_svc.ensure_spring_imported(chain_dir, spring_id)
        ctx_state = spring_svc.get_spring_context(spring_id)
        current_idx = int(ctx_state.get("current_task_index", 0))
    except Exception:
        pass
    return current_idx


def _fill_task_info(ctx: dict, tasks: list[dict], current_idx: int) -> None:
    """Placeholders de la tarea actual para TASK_SPEC / TASK_REVIEW."""
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
        ctx["predicates_list"] = "\n".join(f"    • {p}" for p in preds) if preds else ""
        files = t.get("impacted_artifacts", {}).get("files", [])
        ctx["files_list"] = "\n".join(f"    • {f}" for f in files) if files else ""
        ctx["depends_on"] = ", ".join(t.get("depends_on", [])) or "(none)"
        ctx["curvature"] = str(t.get("risks", {}).get("curvature", 0.3))


def _fill_report_tables(ctx: dict, tasks: list[dict]) -> None:
    """Tablas de reporte: task_table, risk_table, dependency_graph, JSON."""
    table_lines = []
    table_lines.append(
        f"{'ID':<10} {'Status':<12} {'Type':<12} {'Domain':<16} {'Objective'}"
    )
    table_lines.append("-" * 80)
    for t in tasks:
        tid = t.get("spec_id", t.get("task_id", ""))
        st = t.get("status", t.get("state", "draft"))
        tp = t.get("type", "")
        dm = t.get("domain", "")
        obj = (t.get("objective", "") or "")[:45]
        table_lines.append(f"{tid:<10} {st:<12} {tp:<12} {dm:<16} {obj}")
    ctx["task_table"] = "\n".join(table_lines)

    risk_lines = []
    risk_lines.append(
        f"{'ID':<10} {'Type':<12} {'Curvature':<12} {'Pre-Churn':<12} {'Commit-Trust':<12}"
    )
    risk_lines.append("-" * 58)
    for t in tasks:
        tid = t.get("spec_id", t.get("task_id", ""))
        tp = t.get("type", "")
        risks = t.get("risks", {})
        cv = risks.get("curvature", "-")
        pc = risks.get("pre_churn", "-")
        ct = risks.get("commit_trust", "-")
        risk_lines.append(
            f"{tid:<10} {tp:<12} {str(cv):<12} {str(pc):<12} {str(ct):<12}"
        )
    ctx["risk_table"] = "\n".join(risk_lines)

    dep_lines = []
    for t in tasks:
        tid = t.get("spec_id", t.get("task_id", ""))
        deps = t.get("depends_on", [])
        if deps:
            dep_lines.append(f"  {tid} → {', '.join(deps)}")
    ctx["dependency_graph"] = (
        "\n".join(dep_lines) if dep_lines else "  (no dependencies)"
    )

    ctx["total_tasks"] = len(tasks)
    ctx["all_tasks_json"] = json.dumps(tasks, indent=2, ensure_ascii=False)


def _fill_queue_ready(ctx: dict, tasks: list[dict]) -> None:
    """Cola DAG del dashboard: ready_tasks y blocked_tasks (placeholders)."""
    completed = {
        t.get("spec_id", t.get("task_id"))
        for t in tasks
        if isinstance(t, dict) and t.get("status") == "completed"
    }

    def _status(t: dict) -> str:
        return str(t.get("status", t.get("state", "draft")))

    def _dep_keys(t: dict) -> list[str]:
        deps = t.get("depends_on") or []
        return [d for d in deps if d]

    def _deps_satisfied(t: dict) -> bool:
        return all(d in completed for d in _dep_keys(t))

    ready: list[dict] = []
    blocked: list[dict] = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if _status(t) == "completed":
            continue
        (ready if _deps_satisfied(t) else blocked).append(t)

    def _fmt(items: list[dict]) -> str:
        if not items:
            return "  (none)"
        lines = []
        for t in items:
            tid = t.get("spec_id", t.get("task_id", "?"))
            st = _status(t)
            deps = ", ".join(_dep_keys(t)) or "—"
            obj = (t.get("objective", "") or "")[:60]
            lines.append(f"  {tid:<10} [{st:<9}] deps: {deps:<20} {obj}")
        return "\n".join(lines)

    ctx["ready_tasks"] = _fmt(ready)
    ctx["blocked_tasks"] = _fmt(blocked)
    ctx["ready_count"] = len(ready)
    ctx["blocked_count"] = len(blocked)


def _fill_current_task_context(
    ctx: dict, tasks: list[dict], task_id: str, chain_dir: str
) -> None:
    """Contexto de la tarea actual para ANALYZE/REVIEW (gates de usuario)."""
    matching = [t for t in tasks if t.get("spec_id", t.get("task_id")) == task_id]
    t_current = matching[0] if matching else tasks[0]
    ctx["task_status"] = t_current.get("status", t_current.get("state", "draft"))
    ctx["result_state"] = t_current.get("result_state", "")
    ctx["preconditions_list"] = (
        "\n".join(f"    • {p}" for p in (t_current.get("preconditions", []) or []))
        or "    (none)"
    )
    ctx["acceptance_criteria_list"] = (
        "\n".join(
            f"    • {a}" for a in (t_current.get("acceptance_criteria", []) or [])
        )
        or "    (none)"
    )
    ctx["quality_gates_list"] = (
        "\n".join(f"    • {q}" for q in (t_current.get("quality_gates", []) or []))
        or "    (none)"
    )
    decisions = t_current.get("decisions", []) or []
    _decision_lines = []
    for _d in decisions:
        if isinstance(_d, dict):
            _decision_lines.append(
                f"    • {_d.get('title', '')}: {_d.get('rationale', '')}"
            )
        elif isinstance(_d, str) and _d.strip():
            _decision_lines.append(f"    • {_d}")
    ctx["decisions_list"] = "\n".join(_decision_lines) or "    (none)"
    ctx["curvature"] = str(t_current.get("risks", {}).get("curvature", ""))
    ctx["pre_churn"] = str(t_current.get("risks", {}).get("pre_churn", ""))
    ctx["commit_trust"] = str(t_current.get("risks", {}).get("commit_trust", ""))

    # ── Q&A del ANALYZE/DESIGN desde sub_state_data (workflow) ──
    ssd = {}
    try:
        from shared.core.engine.infrastructure.repositories import (
            WorkflowStateRepository,
        )

        wf = WorkflowStateRepository().load(chain_dir)
        ssd = wf.sub_state_data or {}
    except Exception:
        pass
    an_entry = (
        ssd.get("DESIGN.DESIGN_ANALYZE")
        or ssd.get("DESIGN_ANALYZE")
        or ssd.get("ANALYZE.ANALYZE")
        or ssd.get("ANALYZE")
    )
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

    # ── StepResult del paso anterior (patrón monádico) ──
    last_step = _read_last_step_result(ssd)
    if last_step:
        ctx.update(last_step)


def _read_last_step_result(sub_state_data: dict) -> dict:
    """Lee el StepResult persistido (checkpoint tipado) del último sub_state_data."""
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
            from shared.core.engine.domain.step_result import StepResult

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
                    "step_task_summary": sr.to_prompt_dict().get(
                        "step_task_summary", ""
                    ),
                    "step_side_effect_summaries": "\n".join(
                        s
                        for s in sr.to_prompt_dict()
                        .get("step_side_effect_summaries", "")
                        .split("\n")
                        if s
                    ),
                }

        # 2. Fallback: prompt_placeholders (compat con guardados previos)
        placeholders = fields.get("prompt_placeholders")
        if isinstance(placeholders, dict):
            step_keys = {k: v for k, v in placeholders.items() if k.startswith("step_")}
            if step_keys:
                return step_keys
    return {}