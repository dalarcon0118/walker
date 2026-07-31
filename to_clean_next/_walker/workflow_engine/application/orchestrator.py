"""
application/orchestrator.py — OrchestrationService: entry point unificado.

Integra:
  - SubmitDataUseCase (pipeline: validate → persist → TaskRegistry → SideEffectEngine → auto-advance)
  - TransitionUseCase (inter-node con pre/post contracts)
  - ValidateCurrentStateUseCase (diagnóstico)
  - WorkflowRepository + SprintStateRepository

SRP: Unifica step/submit/transition/status como entry point único del engine.
SSOT: Toda operación del workflow pasa por este servicio.
"""

from __future__ import annotations

from typing import Any

import json
import os

from ..domain.models import (
    ExecutionContext,
    ExecutionType,
    SubState,
    WorkflowNode,
)
from ..domain.services import ValidationService
from ..domain.errors import (
    WorkflowEngineError,
    ErrorType,
    ErrorStrategy,
    classify_error,
    DEFAULT_STRATEGIES,
)
from ..infrastructure.repositories import (
    WorkflowRepository,
    SprintStateRepository,
    ScriptRunner,
)
from ..infrastructure.side_effects import SideEffectEngine
from ..infrastructure.task_registry import InMemoryTaskRegistry
from ..infrastructure.trace_log import TraceLogger
from ..domain.registry import TaskRegistry
from ..domain.session import SessionStore
from ..context import (
    ContextComposer,
    ContextProviderRegistry,
    create_default_registry,
)
from ...contracts import ContractValidator
from .context_resolver import ContextResolver
from .use_cases import (
    SubmitDataUseCase,
    TransitionUseCase,
    ValidateCurrentStateUseCase,
)


class _ComposerContext:
    """ExecutionContext liviano para el ContextComposer.

    Existe para evitar acoplar el composer al ExecutionContext real,
    que tiene muchas más dependencias. Fase 1: mínimo necesario.
    """

    def __init__(
        self, chain_dir: str = "", task_id: str = "", baggage: dict | None = None
    ):
        self.chain_dir = chain_dir
        self.task_id = task_id
        self.baggage = baggage or {}


class OrchestrationService:
    """Entry point unificado del workflow engine.

    Métodos públicos:
        step(ctx):          Evalúa ExecutionType y decide acción
        submit(ctx, fields): Ejecuta pipeline completo de submit
        transition(ctx, gate): Transiciona entre nodos con contracts
        status(ctx):        Snapshot del estado actual

    Uso desde CLI:
        svc = OrchestrationService()
        result = svc.step(exec_ctx)  # auto → ejecuta, manual → espera submit
        result = svc.submit(exec_ctx, {"decision": "approve"})
        result = svc.transition(exec_ctx, "task_complete")
        snapshot = svc.status(exec_ctx)
    """

    def __init__(
        self,
        workflow_repo: WorkflowRepository | None = None,
        state_repo: SprintStateRepository | None = None,
        script_runner: ScriptRunner | None = None,
        task_registry: TaskRegistry | None = None,
        contract_validator: type[ContractValidator] | None = None,
        side_effect_engine: SideEffectEngine | None = None,
        event_dispatcher: Any = None,
        event_log: Any = None,
        tracer: TraceLogger | None = None,
    ):
        self._workflow_repo = workflow_repo or WorkflowRepository()
        self._state_repo = state_repo or SprintStateRepository()
        self._script_runner = script_runner or ScriptRunner()
        self._task_registry = task_registry or InMemoryTaskRegistry()
        self._contract_validator = contract_validator or ContractValidator
        self._side_effect_engine = side_effect_engine or SideEffectEngine(
            task_registry=self._task_registry,
        )
        self._event_dispatcher = event_dispatcher
        self._event_log = event_log
        self._tracer = tracer or TraceLogger()

        # Context Composer (Fase 1: opt-in, backwards compat)
        _def_reg = create_default_registry()
        self._context_registry: ContextProviderRegistry | None = _def_reg
        self._context_composer: ContextComposer | None = (
            ContextComposer(_def_reg) if _def_reg else None
        )

        # Prompt Manager: centraliza todo lo relacionado a prompts
        self._prompt_manager: Any | None = None

        # Use cases internos (lazy init)
        self._submit_uc: SubmitDataUseCase | None = None
        self._transition_uc: TransitionUseCase | None = None
        self._validate_uc: ValidateCurrentStateUseCase | None = None

    # ── Properties (lazy) ───────────────────────────────────────────────

    @property
    def prompt_manager(self):
        """PromptManager: único responsable de prompts, templates y contexto."""
        if self._prompt_manager is None:
            from prompt import PromptManager

            self._prompt_manager = PromptManager
        return self._prompt_manager

    @property
    def submit_uc(self) -> SubmitDataUseCase:
        if self._submit_uc is None:
            self._submit_uc = SubmitDataUseCase(
                workflow_repo=self._workflow_repo,
                state_repo=self._state_repo,
                script_runner=self._script_runner,
                task_registry=self._task_registry,
                contract_validator=self._contract_validator,
                side_effect_engine=self._side_effect_engine,
                event_dispatcher=self._event_dispatcher,
                event_log=self._event_log,
            )
        return self._submit_uc

    @property
    def transition_uc(self) -> TransitionUseCase:
        if self._transition_uc is None:
            self._transition_uc = TransitionUseCase(
                workflow_repo=self._workflow_repo,
                state_repo=self._state_repo,
                script_runner=self._script_runner,
                event_dispatcher=self._event_dispatcher,
                event_log=self._event_log,
            )
        return self._transition_uc

    @property
    def validate_uc(self) -> ValidateCurrentStateUseCase:
        if self._validate_uc is None:
            self._validate_uc = ValidateCurrentStateUseCase(
                workflow_repo=self._workflow_repo,
                state_repo=self._state_repo,
            )
        return self._validate_uc

    # ── Context Composer (lazy) ─────────────────────────────────────

    @property
    def context_composer(self) -> ContextComposer | None:
        """ContextComposer (Fase 1: opt-in). Retorna None si no hay registry."""
        return self._context_composer

    # ── Prompt ID Resolution ────────────────────────────────────────────

    def resolve_prompt(
        self,
        prompt_id: str,
        chain_dir: str,
        task_id: str = "",
        context_config: dict | None = None,
        execution_ctx: Any | None = None,
        extra_data: dict | None = None,
    ) -> str:
        """Resuelve prompt_id usando PromptManager (módulo centralizado).

        Si context_config está presente, usa compose() (provider-based).
        Si no, usa resolve() (template-based).
        
        Args:
            extra_data: datos adicionales inyectados sobre collect_all() (prioridad alta)
        """
        try:
            pm = self.prompt_manager(chain_dir)
            ctx = {"task_id": task_id, "node_id": prompt_id.split("/")[-1]}
            if execution_ctx:
                ctx.update(getattr(execution_ctx, "baggage", {}) or {})

            if context_config:
                composer_ctx = _ComposerContext(
                    chain_dir=chain_dir,
                    task_id=task_id or "",
                    baggage=getattr(execution_ctx, "baggage", {})
                    if execution_ctx
                    else {},
                )
                return pm.compose(
                    node_config=context_config,
                    prompt_template=prompt_id,
                    ctx=composer_ctx,
                    chain_dir=chain_dir,
                )

            return pm.resolve(prompt_id, context=ctx, extra_data=extra_data)
        except Exception as e:
            return f"[error resolving prompt '{prompt_id}': {e}]"

    def _resolve_status_prompt(self, sub_state, ctx) -> str:
        """Resuelve el prompt para status: usa prompt_id si existe, si no on_entry_message.
        
        Inyecta sprint context como extra_data para que templates {variables} 
        se resuelvan con datos reales del STATE layer.
        """
        if sub_state.prompt_id:
            sprint_ctx = self._build_prompt_context(ctx.chain_dir, ctx.task_id)
            resolved = self.resolve_prompt(
                sub_state.prompt_id, 
                ctx.chain_dir, 
                ctx.task_id,
                extra_data=sprint_ctx,
            )
            if resolved and not resolved.startswith("[error"):
                return resolved
        return sub_state.on_entry_message or ""

    # ── Node Briefing ─────────────────────────────────────────────

    @staticmethod
    def _build_node_briefing(
        node: WorkflowNode | None, sub_state: SubState | None
    ) -> str:
        """Construye el briefing del nodo actual para el LLM.

        Es metadata del flujo de ejecución — no es un provider de datos.
        Se inyecta como sección fija en el mensaje antes del contexto.
        """
        lines = ["=== NODE BRIEFING ==="]
        if node:
            label = node.label or node.node_id
            lines.append(f"Node: {node.node_id} ({label})")
            if node.description:
                lines.append(f"Purpose: {node.description}")
        if sub_state:
            ss_label = sub_state.label or sub_state.key
            lines.append(f"Current step: {sub_state.key} — {ss_label}")
            if sub_state.on_success:
                lines.append(f'→ On success: proceed to "{sub_state.on_success}"')
            if sub_state.on_failure:
                lines.append(f'→ On failure: revert to "{sub_state.on_failure}"')
            if sub_state.side_effects:
                se_list = [se.type for se in sub_state.side_effects]
                lines.append(f"Side effects: {', '.join(se_list)}")
            else:
                lines.append("Side effects: (none)")
        return "\n".join(lines)

    def _build_prompt_context(
        self,
        chain_dir: str,
        task_id: str = "",
    ) -> dict:
        """Construye contexto de placeholders desde sprint-state y sesión."""
        ctx: dict = {}
        if task_id:
            ctx["task_id"] = task_id

        # project_id desde SessionRecord activo (SSOT)
        from ..domain.session import SessionStore

        record = SessionStore.get_active()
        if record and record.project_id:
            ctx["project_id"] = record.project_id

        # Cargar sprint-state para placeholders
        sp_path = os.path.join(chain_dir, "sprint-state.json")
        if os.path.exists(sp_path):
            try:
                with open(sp_path) as f:
                    sp_data = json.load(f)
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
            except (json.JSONDecodeError, OSError):
                pass

        # Task info para TASK_SPEC / TASK_REVIEW (desde plan.json)
        plan_path = os.path.join(chain_dir, "plan.json")
        current_idx = (
            sp_data.get("context", {}).get("current_task_index", 0)
            if os.path.exists(sp_path)
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
                    # Campos de especificación (vacíos si no se han llenado)
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
        # plan.json tiene la spec completa de cada tarea
        plan_path = os.path.join(chain_dir, "plan.json")
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
                
                # task_count total
                ctx["total_tasks"] = len(tasks)
                
                # all_tasks_json raw
                ctx["all_tasks_json"] = json.dumps(tasks, indent=2, ensure_ascii=False)
                
                # ── Datos de la tarea actual para ANALYZE/REVIEW ──
                if tasks:
                    # Buscar por task_id (prioridad) o fallback a primera tarea
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
                    def _fmt_decision(d):
                        if isinstance(d, dict):
                            return f"    • {d.get('title', d.get('label', ''))}: {d.get('rationale', d.get('description', ''))}"
                        return f"    • {d}"
                    ctx["decisions_list"] = "\n".join(_fmt_decision(d) for d in decisions) or "    (none)"
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

    # ── Core API ────────────────────────────────────────────────────────

    def step(
        self,
        ctx: ExecutionContext,
    ) -> dict:
        """Evalúa el ExecutionType del sub-estado actual y decide la acción.

        Si el sub-estado tiene prompt_id, lo resuelve automaticamente
        y lo incluye en el mensaje. Placeholders {var} se reemplazan
        con datos reales del sprint-state.

        Returns:
            dict con:
                action: str — "execute" | "await_submit" | "manual" | "error"
                execution_type: str
                sub_state: str
                message: str — prompt resuelto / on_entry_message
                resolved_prompt: bool — True si message viene de prompt_id
                result: dict — resultado si action=execute
        """
        workflow = self._workflow_repo.load(ctx.chain_dir)
        state = self._state_repo.load(ctx.chain_dir)
        node = workflow.get_node(state.current_node)
        if not node:
            return {
                "action": "error",
                "message": f"Nodo '{state.current_node}' no existe",
                "execution_type": "unknown",
                "sub_state": state.current_sub_state,
            }

        sub_state = node.find_sub_state(state.current_sub_state)
        if not sub_state:
            return {
                "action": "error",
                "message": f"Sub-estado '{state.current_sub_state}' no existe en '{state.current_node}'",
                "execution_type": "unknown",
                "sub_state": state.current_sub_state,
            }

        # ── Resolver contexto via ContextResolver ──────────────────
        try:
            session = SessionStore.load(ctx.session_id) if ctx.session_id else None
            resolver = ContextResolver()
            resolved_ctx = resolver.resolve_for_sub_state(
                state, node, sub_state, session
            )
            # Inyectar contexto resuelto en baggage para uso posterior
            ctx.baggage.update(resolved_ctx)
        except Exception as e:
            return {
                "action": "error",
                "message": f"[context] {e}",
                "execution_type": "unknown",
                "sub_state": state.current_sub_state,
            }

        execution = sub_state.execution

        # ── Node Briefing (metadata del flujo para el LLM) ─────────
        node_briefing = self._build_node_briefing(node, sub_state)

        # ── Trazar entrada a sub-estado ─────────────────────────────
        self._tracer.trace(
            ctx.chain_dir,
            "NODE_ENTRY",
            data={
                "node": state.current_node,
                "sub_state": state.current_sub_state,
                "execution_type": execution.value,
                "sub_state_index": state.sub_state_index,
            },
            task_id=ctx.task_id,
            session_id=ctx.session_id,
        )

        # ── AUTO / BOOTSTRAP: ejecutar pipeline completo ────────────
        if execution in (ExecutionType.AUTO, ExecutionType.BOOTSTRAP):
            try:
                result = self.submit_uc.execute(ctx)
                # Trazar ejecucion auto
                task_data = result.get("task_data", {})
                self._tracer.trace_auto(
                    ctx.chain_dir,
                    state.current_sub_state,
                    stdout=str(task_data.get("stdout", "")),
                    stderr=str(task_data.get("stderr", "")),
                    exit_code=result.get("exit_code", 0),
                    task_id=ctx.task_id,
                )
                return {
                    "action": "execute",
                    "execution_type": execution.value,
                    "sub_state": state.current_sub_state,
                    "result": result,
                    "message": result.get("advanced_to", "auto-completed"),
                }
            except WorkflowEngineError as e:
                # TASK-009: Manejar error segun estrategia configurada
                error_result = self._handle_error(ctx, e, sub_state)
                error_result.update(
                    {
                        "execution_type": execution.value,
                        "sub_state": state.current_sub_state,
                    }
                )
                # Trazar error de ejecucion
                self._tracer.trace_error(
                    ctx.chain_dir,
                    type(e).__name__,
                    str(e),
                    task_id=ctx.task_id,
                )
                return error_result

        # ── LLM_TASK: resolver prompt_id o mostrar on_entry_message ─
        elif execution == ExecutionType.LLM_TASK:
            if sub_state.prompt_id:
                # Context Composer (opt-in): si el sub-estado tiene context config
                context_config = getattr(sub_state, "context", None)
                sprint_ctx = self._build_prompt_context(ctx.chain_dir, ctx.task_id)
                if context_config:
                    resolved = self.resolve_prompt(
                        sub_state.prompt_id,
                        ctx.chain_dir,
                        task_id=ctx.task_id,
                        context_config=context_config,
                        execution_ctx=ctx,
                        extra_data=sprint_ctx,
                    )
                else:
                    resolved = self.resolve_prompt(
                        sub_state.prompt_id,
                        ctx.chain_dir,
                        task_id=ctx.task_id,
                        extra_data=sprint_ctx,
                    )
                # ── Inject node briefing ──────────────────────
                if node_briefing:
                    resolved = f"{node_briefing}\n\n---\n\n{resolved}"
                return {
                    "action": "await_submit",
                    "execution_type": execution.value,
                    "sub_state": state.current_sub_state,
                    "message": resolved,
                    "resolved_prompt": True,
                    "required_fields": sub_state.required_fields,
                }
            prompt = sub_state.prompt or sub_state.on_entry_message or ""
            if node_briefing:
                prompt = f"{node_briefing}\n\n---\n\n{prompt}"
            return {
                "action": "await_submit",
                "execution_type": execution.value,
                "sub_state": state.current_sub_state,
                "message": prompt,
                "resolved_prompt": False,
                "required_fields": sub_state.required_fields,
            }

        # ── USER_INPUT: resolver prompt_id o mostrar on_entry_message ─
        elif execution == ExecutionType.USER_INPUT:
            options = [
                {"value": o.value, "label": o.label, "description": o.description}
                for o in (sub_state.options or [])
            ]
            if sub_state.prompt_id:
                sprint_ctx = self._build_prompt_context(ctx.chain_dir, ctx.task_id)
                context_config = getattr(sub_state, "context", None)
                resolved = self.resolve_prompt(
                    sub_state.prompt_id,
                    ctx.chain_dir,
                    task_id=ctx.task_id,
                    context_config=context_config,
                    execution_ctx=ctx,
                    extra_data=sprint_ctx,
                )
                # ── Inject node briefing ──────────────────────
                if node_briefing:
                    resolved = f"{node_briefing}\n\n---\n\n{resolved}"
                return {
                    "action": "await_submit",
                    "execution_type": execution.value,
                    "sub_state": state.current_sub_state,
                    "message": resolved,
                    "resolved_prompt": True,
                    "options": options,
                    "required_fields": sub_state.required_fields,
                }
            msg = sub_state.on_entry_message or ""
            if node_briefing:
                msg = f"{node_briefing}\n\n---\n\n{msg}"
            return {
                "action": "await_submit",
                "execution_type": execution.value,
                "sub_state": state.current_sub_state,
                "message": msg,
                "resolved_prompt": False,
                "options": options,
                "required_fields": sub_state.required_fields,
            }

        # ── MANUAL: resolver prompt_id o mostrar on_entry_message ──
        else:
            # Verificar auto-transition: si el sub-estado no necesita más
            # datos y hay exactamente una gate disponible, transicionar.
            needs_input = bool(sub_state.required_fields)
            state_after = self._state_repo.load(ctx.chain_dir)
            wf_after = self._workflow_repo.load(ctx.chain_dir)
            node_after = wf_after.get_node(state_after.current_node)
            gates = node_after.gates if node_after else []
            can_auto = not needs_input and len(gates) == 1

            if can_auto:
                gate_name = gates[0].name
                self._tracer.trace(
                    ctx.chain_dir,
                    "AUTO_TRANSITION",
                    data={"gate": gate_name},
                    task_id=ctx.task_id,
                )
                return self.transition(ctx, gate_name)

            if sub_state.prompt_id:
                sprint_ctx = self._build_prompt_context(ctx.chain_dir, ctx.task_id)
                context_config = getattr(sub_state, "context", None)
                resolved = self.resolve_prompt(
                    sub_state.prompt_id,
                    ctx.chain_dir,
                    task_id=ctx.task_id,
                    context_config=context_config,
                    execution_ctx=ctx,
                    extra_data=sprint_ctx,
                )
                if node_briefing:
                    resolved = f"{node_briefing}\n\n---\n\n{resolved}"
                return {
                    "action": "manual",
                    "execution_type": execution.value,
                    "sub_state": state.current_sub_state,
                    "message": resolved,
                    "resolved_prompt": True,
                    "required_fields": sub_state.required_fields,
                }
            msg = sub_state.on_entry_message or ""
            if node_briefing:
                msg = f"{node_briefing}\n\n---\n\n{msg}"
            return {
                "action": "manual",
                "execution_type": execution.value,
                "sub_state": state.current_sub_state,
                "message": msg,
                "resolved_prompt": False,
                "required_fields": sub_state.required_fields,
            }

    def submit(
        self,
        ctx: ExecutionContext,
        fields: dict[str, Any] | None = None,
    ) -> dict:
        """Ejecuta pipeline completo de submit.

        Pipeline:
          1. ContractValidator.validate_input() contra input_schema
          2. Persistir datos
          3. TaskRegistry.execute() si sub-estado tiene action
          4. ContractValidator.validate_output() contra output_schema
          5. SideEffectEngine.execute() con side-effects del sub-estado
          6. Auto-advance SOLO si execution=auto
          7. Disparar eventos via EventDispatcher
          8. Disparar eventos on:{} por side-effects emit_event

        Args:
            ctx: ExecutionContext con session_id, chain_dir, task_id.
            fields: Datos a submitir (required_fields del sub-estado).

        Returns:
            Resultado del SubmitDataUseCase.execute().
        """
        import time
        _start_ts = time.time()

        # ── Capturar sub-estado ORIGEN antes de ejecutar (puede auto-avanzar) ─
        _before_state = self._state_repo.load(ctx.chain_dir)
        _orig_node = _before_state.current_node
        _orig_sub = _before_state.current_sub_state
        _orig_workflow = self._workflow_repo.load(ctx.chain_dir)
        _orig_node_def = _orig_workflow.get_node(_orig_node)
        _orig_sub_def = _orig_node_def.find_sub_state(_orig_sub) if _orig_node_def else None
        _orig_exec_type = _orig_sub_def.execution.value if _orig_sub_def else "manual"
        _orig_action = _orig_sub_def.action if _orig_sub_def else ""

        # ctx positional para que execute() lo reconozca como chain_dir
        # TASK-009: try/except con estrategia de error
        try:
            result = self.submit_uc.execute(ctx, fields=fields or {})
        except WorkflowEngineError as e:
            duration_ms = int((time.time() - _start_ts) * 1000)
            result = self._handle_error(ctx, e, None)
            # Persistir contexto de error
            state = self._state_repo.load(ctx.chain_dir)
            state.context["last_error"] = result.get("error_type", "unknown")
            state.context["last_error_strategy"] = result.get("strategy", "none")
            # ── Registrar NodeExecution con error (usando sub-estado ORIGEN) ──
            state.record_node_execution(
                node=_orig_node,
                sub_state=_orig_sub,
                execution_type=_orig_exec_type,
                action=_orig_action,
                submitted_fields=fields or {},
                task_ok=False,
                error=result.get("message", str(e)),
                error_type=result.get("error_type", "unknown"),
                duration_ms=duration_ms,
            )
            self._state_repo.save(ctx.chain_dir, state)

            # ── Trazar error ───────────────────────────────────────
            self._tracer.trace_error(
                ctx.chain_dir,
                result.get("error_type", "unknown"),
                result.get("message", ""),
                task_id=ctx.task_id,
            )
            return result

        # ── Cargar state para tracing (puede haber auto-avanzado) ────
        duration_ms = int((time.time() - _start_ts) * 1000)
        state = self._state_repo.load(ctx.chain_dir)

        # ── Registrar NodeExecution (usando sub-estado ORIGEN, no post-advance) ─
        state.record_node_execution(
            node=_orig_node,
            sub_state=_orig_sub,
            execution_type=_orig_exec_type,
            action=_orig_action,
            submitted_fields=fields or {},
            task_ok=result.get("task_executed", False),
            task_result=result.get("task_result", ""),
            side_effects=result.get("side_effects", []),
            duration_ms=duration_ms,
        )
        self._state_repo.save(ctx.chain_dir, state)

        # ── Trazar submit con decision/evidence ──────────────────────
        decision = fields.get("decision", "") if fields else ""
        evidence = fields.get("evidence", "") if fields else ""
        self._tracer.trace_submit(
            ctx.chain_dir,
            f"{state.current_node}.{state.current_sub_state}",
            decision,
            evidence,
            task_id=ctx.task_id,
        )

        # ── Trazar auto-advance si ocurrio ──────────────────────────
        advanced_to = result.get("advanced_to", "")
        if advanced_to:
            self._tracer.trace(
                ctx.chain_dir,
                "AUTO_ADVANCE",
                data={"from": state.current_sub_state, "to": advanced_to},
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )

        # ── Trazar side-effects ejecutados ──────────────────────────
        side_effects = result.get("side_effects", [])
        for se in side_effects:
            self._tracer.trace(
                ctx.chain_dir,
                "SIDE_EFFECT",
                data={
                    "type": se.get("type", "?"),
                    "ok": se.get("ok", False),
                    "result": se.get("result", {}),
                },
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )

        # ── Disparar eventos on:{} por side-effects emit_event ──
        event_count = self._dispatch_events_from_side_effects(ctx, result)
        if event_count > 0:
            self._tracer.trace(
                ctx.chain_dir,
                "EVENT_DISPATCH",
                data={"events_dispatched": event_count},
                task_id=ctx.task_id,
                session_id=ctx.session_id,
            )

        return result

    def advance_sub_state(self, ctx: ExecutionContext) -> dict:
        """Avanza al siguiente sub-estado dentro del mismo nodo.

        No requiere gate. Usa on_success del sub-estado actual para
        determinar el destino. Persiste y retorna el nuevo estado.
        """
        from .use_cases import AdvanceSubStateUseCase
        uc = AdvanceSubStateUseCase(
            workflow_repo=self._workflow_repo,
            state_repo=self._state_repo,
        )
        return uc.execute(ctx)

    def transition(
        self,
        ctx: ExecutionContext,
        gate: str,
        reason: str = "",
        skip_preconditions: bool = False,
    ) -> dict:
        """Transiciona entre nodos con validación de pre/post contracts.

        ANTES de la transicion: lee el transform del edge (si existe)
        DESPUES de la transicion: ejecuta transform + eventos on:{}

        Args:
            ctx: ExecutionContext con chain_dir, task_id.
            gate: Nombre de la gate (ej: "task_complete", "analysis_approved").
            reason: Razón de la transición (opcional).
            skip_preconditions: Saltar validación de precondiciones (emergencia).

        Returns:
            Resultado del TransitionUseCase.execute().
        """
        import time
        _start_ts = time.time()

        # Capturar nodo origen antes de la transicion
        state_before = self._state_repo.load(ctx.chain_dir)
        from_node = f"{state_before.current_node}.{state_before.current_sub_state}"
        from_sub_state = state_before.current_sub_state

        # ── Leer transform ANTES de la transicion (source node) ──
        transform_config = self._read_edge_transform(ctx, gate)

        # ctx positional para que execute() lo reconozca como chain_dir
        result = self.transition_uc.execute(
            ctx,
            gate_name=gate,
            reason=reason,
            skip_preconditions=skip_preconditions,
        )

        # ── Registrar NodeExecution para la gate (ELM/Saga Log) ──────
        duration_ms = int((time.time() - _start_ts) * 1000)
        state = self._state_repo.load(ctx.chain_dir)
        state.record_node_execution(
            node=state_before.current_node,
            sub_state=from_sub_state,
            execution_type="transition",
            gate=gate,
            submitted_fields={"reason": reason} if reason else {},
            task_ok=result.get("ok", False),
            task_result={
                "from_node": state_before.current_node,
                "to_node": result.get("to_node", ""),
                "to_sub_state": result.get("to_sub_state", ""),
            },
            duration_ms=duration_ms,
        )
        self._state_repo.save(ctx.chain_dir, state)

        # ── Trazar transicion ────────────────────────────────────────
        to_node = result.get("to_node", "") or result.get("to", "")
        self._tracer.trace_transition(
            ctx.chain_dir,
            gate,
            from_node,
            to_node,
            task_id=ctx.task_id,
        )

        # ── Ejecutar edge transform si existe (DESPUES de transition) ─
        if transform_config:
            transform_result = self._execute_edge_transform(
                ctx,
                transform_config,
            )
            if transform_result:
                result["transform"] = transform_result

        # ── Disparar eventos on:{} correlacionados con la gate ──
        self._dispatch_event(ctx, gate, result)

        return result

    def status(
        self,
        ctx: ExecutionContext,
    ) -> dict:
        """Snapshot del estado actual + workflow info.

        Returns:
            dict con:
                node: str
                sub_state: str
                sub_state_index: int
                execution_type: str
                required_fields: list[str]
                prompt: str
                history: list[str]
                node_complete: bool
                available_gates: list[dict]
                validation: dict — resultado de ValidateCurrentStateUseCase
        """
        workflow = self._workflow_repo.load(ctx.chain_dir)
        state = self._state_repo.load(ctx.chain_dir)
        node = workflow.get_node(state.current_node)
        sub_state: SubState | None = None
        if node:
            sub_state = node.find_sub_state(state.current_sub_state)

        # Validar estado
        validation = self.validate_uc.execute(ctx.chain_dir)

        # Gates disponibles
        available_gates = []
        if node:
            for g in node.gates:
                available_gates.append(
                    {
                        "name": g.name,
                        "target": f"{g.target_node}.{g.target_sub_state or '*'}"
                        if g.target_sub_state
                        else g.target_node,
                        "description": g.description,
                    }
                )

        # ── Helpers para formato compacto ─────────────────────────
        _MAX_FIELD_VAL = 120  # max chars per submitted_field value
        _MAX_RESULT = 300     # max chars for task_result
        _MAX_FIELDS = 5       # max submitted fields to show

        def _summarize_fields(d: dict) -> dict:
            """Resume submitted_fields: acorta valores largos, limita cantidad."""
            out = {}
            for i, (k, v) in enumerate(d.items()):
                if i >= _MAX_FIELDS:
                    out[f"...+{len(d) - _MAX_FIELDS} more"] = True
                    break
                if isinstance(v, str) and len(v) > _MAX_FIELD_VAL:
                    out[k] = v[:_MAX_FIELD_VAL] + "..."
                elif isinstance(v, (list, dict)):
                    s = str(v)
                    out[k] = s[:_MAX_FIELD_VAL] + "..." if len(s) > _MAX_FIELD_VAL else v
                else:
                    out[k] = v
            return out

        def _truncate_result(r) -> str | dict:
            """Trunca task_result: strings largos o dicts con valores largos."""
            if r is None:
                return ""
            if isinstance(r, str):
                return r[:_MAX_RESULT] + "..." if len(r) > _MAX_RESULT else r
            if isinstance(r, dict):
                out = {}
                for k, v in r.items():
                    if isinstance(v, str) and len(v) > _MAX_FIELD_VAL:
                        out[k] = v[:_MAX_FIELD_VAL] + "..."
                    else:
                        out[k] = v
                return out
            if isinstance(r, (list, tuple)):
                s = str(r)
                return s[:_MAX_RESULT] + "..." if len(s) > _MAX_RESULT else s
            return str(r)

        # Últimas ejecuciones (node_executions del sprint-state)
        last_executions = []
        for ne in state.node_executions[-10:]:
            last_executions.append({
                "node": ne.node,
                "sub_state": ne.sub_state,
                "execution_type": ne.execution_type,
                "action": ne.action,
                "task_ok": ne.task_ok,
                "gate": ne.gate,
                "error": ne.error,
                "error_type": ne.error_type,
                "duration_ms": ne.duration_ms,
                "completed_at": ne.completed_at[11:19] if ne.completed_at and len(ne.completed_at) > 19 else (ne.completed_at or ""),
                "submitted_fields": _summarize_fields(ne.submitted_fields),
                "task_result": _truncate_result(ne.task_result),
                "side_effects": [
                    {"type": se.get("type", "?"), "ok": se.get("ok", False)}
                    for se in (ne.side_effects or [])
                ],
            })

        return {
            "node": state.current_node,
            "sub_state": state.current_sub_state,
            "sub_state_index": state.sub_state_index,
            "execution_type": sub_state.execution.value if sub_state else "unknown",
            "required_fields": sub_state.required_fields if sub_state else [],
            "prompt": self._resolve_status_prompt(sub_state, ctx) if sub_state else "",
            "prompt_id": sub_state.prompt_id if sub_state else "",
            "history": list(state.history),
            "node_complete": ValidationService.is_node_complete(state, workflow)
            if node
            else True,
            "available_gates": available_gates,
            "validation": validation,
            "task_id": ctx.task_id,
            "session_id": ctx.session_id,
            "node_executions": last_executions,
            "node_executions_total": len(state.node_executions),
        }

    # ── Error Handling (TASK-009) ───────────────────────────────────────

    def _classify_error(self, error: Exception) -> ErrorType:
        """Clasifica un error segun su tipo."""
        return classify_error(error)

    def _resolve_error_strategy(
        self,
        error_type: ErrorType,
        sub_state: SubState | None = None,
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

    def _handle_error(
        self,
        ctx: ExecutionContext,
        error: Exception,
        sub_state: SubState | None = None,
    ) -> dict:
        """Maneja un error segun la estrategia configurada.

        Estrategias:
          - retry: Reintentar submit con misma config
          - retry_2x: Reintentar con timeout 2x
          - compensate: Compensar side-effects del sub-estado actual
          - escalate: Enviar a REFINE.ESCALATION
          - block+message: Retornar error al LLM
          - log+continue: Loggear y continuar (retorna ok=True)
          - on_failure: Ejecutar transicion a on_failure del sub-estado
        """
        error_type = self._classify_error(error)
        strategy = self._resolve_error_strategy(error_type, sub_state)

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
        elif strategy == ErrorStrategy.LOG_CONTINUE:
            import logging

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
        elif strategy == ErrorStrategy.RETRY:
            max_attempts = (
                sub_state.retry.max_attempts if sub_state and sub_state.retry else 3
            )
            for attempt in range(max_attempts):
                try:
                    result = self.submit_uc.execute(ctx)
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
        elif strategy == ErrorStrategy.COMPENSATE:
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
        elif strategy == ErrorStrategy.ESCALATE:
            try:
                transition_result = self.transition_uc.execute(
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
        elif strategy == ErrorStrategy.ON_FAILURE:
            if sub_state and sub_state.on_failure:
                try:
                    of_result = self.transition_uc.execute(
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

    # ── Edge Transform (TASK-007) ──────────────────────────────────────

    def _read_edge_transform(
        self,
        ctx: ExecutionContext,
        gate_name: str,
    ) -> dict | None:
        """Lee la configuracion de transform del edge ANTES de la transicion.

        Se ejecuta en el nodo fuente (source), antes de que transition
        cambie el estado al nodo destino.

        Returns:
            Dict con transform config o None si no hay transform.
        """
        workflow = self._workflow_repo.load(ctx.chain_dir)
        state = self._state_repo.load(ctx.chain_dir)
        node = workflow.get_node(state.current_node)
        if not node:
            return None
        for g in node.gates:
            if g.name == gate_name and g.transform:
                return dict(g.transform)
        return None

    def _execute_edge_transform(
        self,
        ctx: ExecutionContext,
        transform: dict,
    ) -> dict | None:
        """Ejecuta transform del edge.

        Los transforms se definen en edges.json con:
          transform: {
            "action": "nombre_accion",      # TaskRegistry action
            "input": ["NODE.SUB.field"],    # Selectores de sub_state_data
            "output": "NODE.context.field"   # Destino en contexto
          }

        Args:
            ctx: ExecutionContext.
            transform: Configuracion del transform (ya leida del source node).

        Returns:
            Dict con resultado del transform o None si no hay accion.
        """
        action = transform.get("action", "")
        input_selectors = transform.get("input", [])
        output = transform.get("output", "")

        if not action:
            return None

        state = self._state_repo.load(ctx.chain_dir)

        # ── Preparar datos de input desde sub_state_data ──────────
        input_data = {}
        for selector in input_selectors:
            parts = selector.split(".")
            if len(parts) >= 2:
                node_key = parts[0]
                sub_key = parts[1]
                field = parts[2] if len(parts) >= 3 else None

                # Buscar en sub_state_data
                data_key = f"{node_key}.{sub_key}"
                ss_data = state.get_sub_state_data(data_key)
                if field == "*":
                    # Wildcard: todas las fields del sub-estado
                    val = dict(ss_data.fields)
                elif field:
                    val = ss_data.fields.get(field, "")
                else:
                    val = dict(ss_data.fields)
                input_data[selector] = val

        # ── Ejecutar accion via TaskRegistry ──────────────────────
        try:
            exec_ctx = ExecutionContext.create(
                session_id=ctx.session_id,
                chain_dir=ctx.chain_dir,
                task_id=ctx.task_id,
                trace_id=ctx.trace_id,
            )
            task_result = self._task_registry.execute(action, exec_ctx, input_data)

            if not task_result.ok:
                return {
                    "action": action,
                    "ok": False,
                    "error": str(task_result.error)
                    if task_result.error
                    else "Unknown error",
                }

            # ── Persistir resultado en contexto ────────────────────
            if output and task_result.data:
                output_field = output.split(".")[-1]
                state.context[output_field] = str(task_result.data)[:2000]
                self._state_repo.save(ctx.chain_dir, state)

            return {
                "action": action,
                "ok": True,
                "input_selectors": input_selectors,
                "output": output,
                "result": str(task_result.data)[:500] if task_result.data else None,
            }
        except Exception as e:
            return {
                "action": action,
                "ok": False,
                "error": str(e),
            }

    # ── Event Dispatch (TASK-006: on:{}) ────────────────────────────────

    def _dispatch_events_from_side_effects(
        self,
        ctx: ExecutionContext,
        result: dict,
    ) -> int:
        """Escanea side-effects en el resultado de submit() y dispara eventos on:{}.

        Por cada side-effect type=emit_event, busca el evento en
        workflow.json 'on' y ejecuta su handler (run o transition).

        Returns:
            Numero de eventos despachados.
        """
        side_effects = result.get("side_effects", [])
        if not side_effects:
            return 0

        count = 0
        for se in side_effects:
            if se.get("type") != "emit_event":
                continue
            event_name = se.get("result", {}).get("event", "")
            if event_name:
                dispatch_result = self._dispatch_event(ctx, event_name, result)
                if dispatch_result:
                    count += 1
        return count

    def _dispatch_event(
        self,
        ctx: ExecutionContext,
        event_name: str,
        result: dict | None = None,
    ) -> dict | None:
        """Busca y ejecuta el handler de un evento on:{}.

        Los eventos se definen en workflow.json on:{} con dos tipos:
          - {"run": "action_name"}: Ejecuta accion via TaskRegistry
          - {"transition": "gate_name"}: Aplica transicion

        Args:
            ctx: ExecutionContext actual.
            event_name: Nombre del evento (ej: "task_approved").
            result: Resultado previo para metadata.

        Returns:
            Resultado del handler o None si no hay handler.
        """
        workflow = self._workflow_repo.load(ctx.chain_dir)
        handler = workflow.find_event_handler(event_name)
        if not handler:
            return None

        # Metadata del evento (incluye nodo actual real)
        state = self._state_repo.load(ctx.chain_dir)
        metadata = {
            "event": event_name,
            "task_id": ctx.task_id,
            "node": f"{state.current_node}.{state.current_sub_state}",
            "timestamp": ctx.started_at,
            "session_id": ctx.session_id,
        }

        # ── Handler type: run (via TaskRegistry) ────────────────────
        if "run" in handler:
            action = handler["run"]
            try:
                exec_ctx = ExecutionContext.create(
                    session_id=ctx.session_id,
                    chain_dir=ctx.chain_dir,
                    task_id=ctx.task_id,
                    trace_id=ctx.trace_id,
                )
                task_result = self._task_registry.execute(action, exec_ctx, metadata)
                return {
                    "event": event_name,
                    "handler_type": "run",
                    "action": action,
                    "ok": task_result.ok,
                    "result": str(task_result.data)[:500] if task_result.data else None,
                }
            except Exception as e:
                return {
                    "event": event_name,
                    "handler_type": "run",
                    "action": action,
                    "ok": False,
                    "error": str(e),
                }

        # ── Handler type: transition (via TransitionUseCase) ────────
        elif "transition" in handler:
            gate = handler["transition"]
            try:
                transition_result = self.transition_uc.execute(
                    ctx,
                    gate_name=gate,
                )
                return {
                    "event": event_name,
                    "handler_type": "transition",
                    "gate": gate,
                    "ok": True,
                    "target": transition_result.get("to_node", ""),
                }
            except Exception as e:
                return {
                    "event": event_name,
                    "handler_type": "transition",
                    "gate": gate,
                    "ok": False,
                    "error": str(e),
                }

        return None

    # ── Helpers ─────────────────────────────────────────────────────────

    def create_context(
        self,
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
    ) -> ExecutionContext:
        """Crea un ExecutionContext para las operaciones del servicio."""
        import uuid

        return ExecutionContext.create(
            session_id=session_id or f"sess-{uuid.uuid4().hex[:8]}",
            chain_dir=chain_dir,
            task_id=task_id,
        )
