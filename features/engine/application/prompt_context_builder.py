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

        # Cargar sprint-state para placeholders (via SprintStateRepository SSOT)
        from features.spring.infrastructure.repositories import SprintStateRepository

        sp_data = SprintStateRepository().load_dict(chain_dir)
        if sp_data:
            sprint = sp_data.get("sprint", {})
            if "project_id" not in ctx:
                ctx["project_id"] = sprint.get("project_id", "")
            ctx["sprint_goal"] = sprint.get("goal", "")
            tasks_data = sp_data.get("tasks", {})
            if isinstance(tasks_data, dict):
                ctx["task_count"] = len(tasks_data)
                ctx["completed_count"] = sum(
                    1
                    for t in tasks_data.values()
                    if isinstance(t, dict) and t.get("status") == "completed"
                )
            elif isinstance(tasks_data, list):
                ctx["task_count"] = len(tasks_data)
                ctx["completed_count"] = sum(
                    1
                    for t in tasks_data
                    if isinstance(t, dict) and t.get("status") == "completed"
                )
            ctx["session_id"] = os.path.basename(chain_dir)

        # Task info para TASK_SPEC / TASK_REVIEW (desde plan.json)
        plan_path = os.path.join(chain_dir, "plan.json")
        current_idx = (
            sp_data.get("context", {}).get("current_task_index", 0)
            if sp_data
            else 0
        )
        if os.path.exists(plan_path):
            try:
                with open(plan_path) as f:
                    plan = json.load(f)
                tasks = plan.get("tasks", [])
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
            except (json.JSONDecodeError, OSError):
                pass

        # goal_display y task_summary formateados
        goal = str(ctx.get("sprint_goal", ""))
        ctx["goal_display"] = f"Goal: {goal}" if goal else "Goal: Not set"
        done = int(ctx.get("completed_count", 0))
        total = int(ctx.get("task_count", 0))
        ctx["task_summary"] = f"{done}/{total} tasks completed"

        # ── tabla de tareas completa para reportes ────────────────
        if os.path.exists(plan_path):
            try:
                with open(plan_path) as f:
                    plan = json.load(f)
                tasks = plan.get("tasks", [])

                # task_table: formato tabular listo para inyectar
                table_lines = []
                table_lines.append(f"{'ID':<10} {'Status':<12} {'Type':<12} {'Domain':<16} {'Objective'}")
                table_lines.append("-" * 80)
                for t in tasks:
                    tid = t.get("task_id", "")
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
                    tid = t.get("task_id", "")
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
                    tid = t.get("task_id", "")
                    deps = t.get("depends_on", [])
                    if deps:
                        dep_lines.append(f"  {tid} → {', '.join(deps)}")
                ctx["dependency_graph"] = "\n".join(dep_lines) if dep_lines else "  (no dependencies)"

                ctx["total_tasks"] = len(tasks)
                ctx["all_tasks_json"] = json.dumps(tasks, indent=2, ensure_ascii=False)

                # ── Datos de la tarea actual para ANALYZE/REVIEW ──
                if tasks:
                    if task_id:
                        matching = [t for t in tasks if t.get("task_id") == task_id]
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

                    # ── Q&A del ANALYZE desde sub_state_data ─────────
                    ssd = sp_data.get("sub_state_data", {})
                    an_state = ssd.get("ANALYZE.ANALYZE", {}) or ssd.get("ANALYZE", {})
                    an_fields = an_state.get("fields", {}) if isinstance(an_state, dict) else {}
                    ctx["q1"] = an_fields.get("q1", "")
                    ctx["a1"] = an_fields.get("a1", "")
                    ctx["q2"] = an_fields.get("q2", "")
                    ctx["a2"] = an_fields.get("a2", "")
                    ctx["q3"] = an_fields.get("q3", "")
                    ctx["a3"] = an_fields.get("a3", "")

            except (json.JSONDecodeError, OSError):
                ctx["task_table"] = "(error loading plan.json)"
                ctx["risk_table"] = ""
                ctx["dependency_graph"] = ""
        else:
            ctx["task_table"] = "(no plan.json found)"
            ctx["risk_table"] = ""
            ctx["dependency_graph"] = ""
            ctx["total_tasks"] = 0

        return ctx
