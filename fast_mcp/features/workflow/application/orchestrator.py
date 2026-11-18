"""application/orchestrator.py — OrchestrationService: unified entry point.

Collapsed from 4 orchestrators (1,471 LOC) + god class (645 LOC) into
a single service with 4 methods (~250 LOC).

Methods:
    submit(ctx, fields)     → dict  (validate → persist → trace)
    transition(ctx, gate)   → dict  (validate gate → apply → persist → trace)
    status(ctx)             → dict  (read-only snapshot)
    help(ctx)               → dict  (current state + available tools)

Composition root OBLIGATORIO: all dependencies injected via constructor.
No resolve_port(), no service locator, no lazy imports.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from datetime import datetime, timezone
from typing import Any

from returns.pipeline import is_successful

from ..domain.entities import WorkflowGraph, WorkflowState
from ..domain.enums import ExecutionType
from ..domain.errors import (
    GateNotFoundError,
    NodeNotFoundError,
    TransitionBlockedError,
    WorkflowEngineError,
)
from ..domain.events import WorkflowEvent
from ..domain.execution import ExecutionContext
from ..domain.services.advance import AdvanceService
from ..domain.services.block_resolver import BlockResolver
from ..domain.services.transition import TransitionService
from ..domain.services.validation import ValidationService
from .bundle import EngineBundle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Score mínimo para que un skill cuente como candidato de un gate `required`.
# Medido: sin umbral, VERIFY.QA en un entorno sin skills de review matcheaba
# `frontend-design` con score 2 (por la palabra "quality") y bloqueaba el gate
# pidiendo cargar un skill de diseño para revisar código. Con >=3 ese gate pasa
# como "sin candidatos" y en un repo con skills de review (game_reset) sigue
# eligiendo `code-review-checklist` (score 6).
MIN_SKILL_SCORE_REQUIRED = 3


def declared_required_fields(sub_state: Any) -> list[str]:
    """Campos que el sub-estado exige, de UNA sola fuente derivada.

    Hoy convivían tres: `required_fields`, `input_schema.required` y
    `output_schema.required`. `workflow_state` mostraba `[]` mientras `advance`
    reclamaba `decision`, y el agente terminaba deduciendo el payload del
    prompt. Se unifican en el mismo orden de precedencia que usa `advance`.
    """
    required = list(getattr(sub_state, "required_fields", None) or [])
    if required:
        return required
    for schema_name in ("input_schema", "output_schema"):
        schema = getattr(sub_state, schema_name, None) or {}
        required = list(schema.get("required") or [])
        if required:
            return required
    return []


def missing_declared_fields(state: WorkflowState, sub_state: Any) -> list[str]:
    sub_data = state.get_sub_state_data(state.current_sub_state)
    return [f for f in declared_required_fields(sub_state) if not sub_data.is_field_complete(f)]


def expected_payload(sub_state: Any) -> dict[str, Any]:
    """Contrato de campos: tipo, obligatoriedad y enum, listo para armar payload."""
    out: dict[str, Any] = {}
    for schema_name in ("input_schema", "output_schema"):
        schema = getattr(sub_state, schema_name, None) or {}
        for name, spec in (schema.get("properties") or {}).items():
            if not isinstance(spec, dict):
                continue
            field = {"type": spec.get("type", "string")}
            if spec.get("description"):
                field["description"] = str(spec["description"])[:200]
            if spec.get("enum"):
                field["enum"] = list(spec["enum"])
            out[name] = field
    for name in declared_required_fields(sub_state):
        out.setdefault(name, {"type": "string"})["required"] = True
    return out


def next_action_for(state: WorkflowState, graph: WorkflowGraph) -> dict[str, Any] | None:
    """La llamada exacta que desbloquea el paso actual.

    Es lo que evita adivinar el protocolo: sin esto el agente descubre que
    existe `transition` cuando `advance` ya falló con "node complete".
    """
    node = graph.get_node(state.current_node)
    if node is None:
        return None
    sub_state = node.find_sub_state(state.current_sub_state)
    if sub_state is None:
        return None

    missing = missing_declared_fields(state, sub_state)
    if missing:
        return {
            "tool": "submit",
            "reason": "missing_fields",
            "missing": missing,
            "payload": {field: f"<{field}>" for field in missing},
            "detail": "Registrá los campos con submit; sin ellos el paso no avanza.",
        }

    last = state.sub_state_index >= len(node.sub_states) - 1
    if last:
        gates = [g.name for g in ValidationService.get_available_gates(state, graph)]
        if gates:
            return {
                "tool": "transition",
                "gates": gates,
                "detail": "El nodo está completo: elegí un gate para seguir.",
            }
    return {
        "tool": "advance",
        "detail": "El paso está satisfecho: avanzá para ejecutar/continuar.",
    }


_DECISION_TOKENS = ("refine", "cancel", "reject", "approve", "accept", "proceed")


def decision_map_for(gates: list[Any]) -> dict[str, str]:
    """De cada decisión al gate que dispara, derivado del grafo.

    Fuente primaria: `contract.pre` del gate (`ckg_has_decision('approve')`).
    Fallback: el nombre del gate (`needs_refine` → `refine`). Se expone para
    que `submit` cierre el gate solo y el agente deje de encadenar
    submit → advance → transition.
    """
    import re as _re

    out: dict[str, str] = {}
    for gate in gates:
        name = str(getattr(gate, "name", "") or "")
        contract = getattr(gate, "contract", None) or {}
        pre = str(contract.get("pre") or "") if isinstance(contract, dict) else ""
        found = _re.search(r"ckg_has_decision\('([^']+)'\)", pre)
        if found:
            out.setdefault(found.group(1), name)
            continue
        lowered = name.lower()
        for token in _DECISION_TOKENS:
            if token in lowered:
                out.setdefault(token, name)
                break
    return out


def extract_user_message(template: str, position: str) -> dict[str, Any] | None:
    """Texto que hay que MOSTRAR al humano, separado de las instrucciones.

    Los templates mezclan instrucciones para el agente con la pregunta y las
    opciones. El agente decidía qué relatar (y el plugin tuvo que implementar su
    propio extractor). Acá se devuelve el bloque de opciones ya listo.
    """
    import re as _re

    if not template:
        return None
    lines = template.splitlines()
    start = None
    for index in range(len(lines) - 1, -1, -1):
        if _re.match(r"^\s*(options|opciones)\s*:", lines[index], _re.IGNORECASE):
            start = index
            break
    if start is None:
        return None
    option_line = _re.compile(r"^\s*(?:[-•.]|\d+[.)])\s+\S|^\s*\w+\s*(?:—|->|→)")
    picked: list[str] = [lines[start].rstrip()]
    for line in lines[start + 1:]:
        # Las líneas de comando (`walker <proj>/submit ...`, incluso con un
        # prefijo como "→ Submit:") son para el AGENTE, no para el humano.
        if _re.search(r"\bwalker\b.*/", line):
            continue
        if not line.strip():
            picked.append("")
            continue
        # Fuera del bloque de opciones (prosa, separadores) se corta: el resto
        # del template es instrucción, no mensaje para el usuario.
        if not option_line.match(line):
            break
        picked.append(line.rstrip())
    text = "\n".join(picked).strip()
    if not text:
        return None
    return {"position": position, "text": text}


def prompt_for_view(prompt: dict | None, view: str) -> dict | None:
    """Recorta el payload del prompt según la vista.

    Medido 2026-11-18: el objeto `prompt` que devolvía `workflow_state` pesaba
    3,79× el template renderizado porque incluía `data` (32-34 claves) con
    hechos que ya están en el texto o en los campos de primer nivel. Ese es el
    distractor más caro: Chroma mide que un distractor cercano ya baja el
    rendimiento. En `light` se omite `data`; el template se conserva porque es
    la instrucción del paso.
    """
    if not isinstance(prompt, dict):
        return prompt
    if str(view or "").lower() == "full":
        return prompt
    return {key: value for key, value in prompt.items() if key != "data"}


def merge_action_fields(state: WorkflowState, sub_key: str, fields: dict[str, Any] | None) -> dict[str, Any]:
    """Campos registrados del sub-estado + los nuevos (los nuevos ganan).

    Un `llm_task` produce su salida desde los campos enviados. `advance` corre
    la acción del sub-estado actual otra vez: si usa solo `fields` (vacío en
    ese camino), la acción se ejecuta dos veces y la segunda pisa la primera
    (medido 2026-11-18: VERIFY.MONITOR perdió plan/deviations y quedó
    `compliant=true` sin desviaciones).
    """
    stored = state.get_sub_state_data(sub_key) if hasattr(state, "get_sub_state_data") else None
    stored_fields = dict(getattr(stored, "fields", None) or {})
    return {**stored_fields, **(fields or {})}


class OrchestrationService:
    """Unified entry point for workflow operations.

    The engine is ANEMIC: it does NOT know who calls it or where
    its dependencies come from. The HOST constructs this service
    via composition.py and injects all dependencies.

    INV-S2-02: __init__ receives all deps; no resolve_port() calls.
    INV-S2-03: every method returns dict, never raises.
    """

    def __init__(
        self,
        bundle: EngineBundle,
    ):
        self._bundle = bundle

    # ── Helpers ─────────────────────────────────────────────────────────

    def _load_graph(self) -> WorkflowGraph | None:
        """Load graph from store. Returns None on error."""
        try:
            return self._bundle.workflow_store.load_graph()
        except Exception:
            return None

    def _load_state(self) -> WorkflowState | None:
        """Load state from store. Returns None on error."""
        try:
            return self._bundle.workflow_store.load_state(self._bundle.chain_dir)
        except Exception:
            return None

    def _save_state(self, state: WorkflowState) -> bool:
        """Persist state. Returns True on success."""
        try:
            self._bundle.workflow_store.save_state(self._bundle.chain_dir, state)
            return True
        except Exception:
            return False

    def _trace(self, event: WorkflowEvent) -> None:
        """Best-effort trace. Never fails."""
        try:
            self._bundle.tracer.trace_event(event)
        except Exception:
            pass

    def _trace_submit(
        self, ctx: ExecutionContext, state: WorkflowState, fields: dict | None
    ) -> None:
        decision = fields.get("decision", "") if fields else ""
        evidence = fields.get("evidence", "") if fields else ""
        self._bundle.tracer.trace_submit(
            self._bundle.chain_dir,
            f"{state.current_node}.{state.current_sub_state}",
            decision,
            evidence,
            task_id=ctx.task_id,
        )

    def _trace_transition(
        self,
        ctx: ExecutionContext,
        gate: str,
        from_node: str,
        to_node: str,
    ) -> None:
        self._bundle.tracer.trace_transition(
            self._bundle.chain_dir,
            gate,
            from_node,
            to_node,
            task_id=ctx.task_id,
        )

    def _resolve_current_prompt(
        self, state: WorkflowState, graph: WorkflowGraph, ctx: Any
    ) -> dict | None:
        """Resuelve el prompt del sub-estado actual (carga dinámica por nodo).

        Si el sub-estado es `execution == llm_task` y hay un prompt_manager
        inyectado, devuelve el prompt LLM (template del nodo). NUNCA levanta:
        devuelve None o un dict con 'error' si no resuelve (no rompe el flujo).
        """
        pm = getattr(self._bundle, "prompt_manager", None)
        if pm is None:
            return None
        node = graph.get_node(state.current_node)
        if node is None:
            return None
        sub = node.find_sub_state(state.current_sub_state)
        if sub is None:
            return None
        exec_type = getattr(sub, "execution", None)
        exec_value = getattr(exec_type, "value", exec_type) if exec_type else ""
        sub_fields = {
            "prompt": getattr(sub, "prompt", "") or "",
            "prompt_id": getattr(sub, "prompt_id", "") or "",
            "on_entry_message": getattr(sub, "on_entry_message", "") or "",
            "required_tools": getattr(sub, "required_tools", None),
            "skill_intent": getattr(sub, "skill_intent", None) or {},
        }
        # Todo el contexto que los templates esperan (what_happened, calidad
        # del paso, tablas de la tarea, decisiones, reflexión) se arma en una
        # unidad aparte: el renderer solo pinta lo que recibe.
        from .prompt_context import build_prompt_extra

        extra: dict[str, Any] = {
            "revision": state.revision,
            **build_prompt_extra(
                state,
                self._bundle.chain_dir,
                str(getattr(ctx, "session_id", "") or ""),
            ),
        }

        try:
            resolved = pm.resolve_for_sub_state(
                node_id=state.current_node,
                sub_state_key=state.current_sub_state,
                execution=exec_value,
                fields=sub_fields,
                extra_data=extra,
            )
        except Exception as e:
            return {"error": str(e)}
        resolved["node"] = state.current_node
        resolved["sub_state"] = state.current_sub_state
        return resolved

    # ── submit ──────────────────────────────────────────────────────────

    def submit(
        self,
        ctx: ExecutionContext,
        fields: dict[str, Any] | None = None,
    ) -> dict:
        """Execute the submit pipeline for the current sub-state.

        Pipeline:
            1. Load state + graph
            2. Reconcile index (INV-P3-01)
            3. Check required_tools gate
            4. Record submit data (candidato, aún sin persistir)
            5. Validate required_fields sobre el resultado
            6. Record NodeExecution
            7. Increment revision (optimistic concurrency)
            8. Save state
            9. Trace submit
            10. Return result
        """
        try:
            return self._submit_impl(ctx, fields or {})
        except Exception as e:
            return {"ok": False, "error": str(e), "action": "submit_error"}

    def _submit_impl(
        self, ctx: ExecutionContext, fields: dict[str, Any]
    ) -> dict:
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        # Auto-initialize: if state has no current_node, set to graph's initial_state
        if not state.current_node and graph.initial_state:
            from dataclasses import replace
            init_node = graph.get_node(graph.initial_state)
            init_sub = init_node.sub_states[0].key if init_node and init_node.sub_states else ""
            state = replace(
                state,
                current_node=graph.initial_state,
                current_sub_state=init_sub,
            )

        # 1. Reconcile index (INV-P3-01)
        state, warnings = state.reconcile_index(graph)

        # 2. Get current sub-state definition
        node = graph.get_node(state.current_node)
        if not node:
            return {
                "ok": False,
                "error": f"Current node '{state.current_node}' not in graph",
            }
        sub_state = node.find_sub_state(state.current_sub_state)
        if not sub_state:
            return {
                "ok": False,
                "error": f"Sub-state '{state.current_sub_state}' not in node '{state.current_node}'",
            }

        # 3. Check required_tools gate
        if sub_state.required_tools:
            gate_check = self._check_required_tools_gate(ctx, sub_state)
            if not gate_check.get("ok", False):
                return gate_check

        # 3b. Skill gate (skill_intent.policy == "required")
        skill_check = self._check_skill_gate(ctx, sub_state, state)
        if skill_check is not None and not skill_check.get("ok", False):
            return skill_check

        # 4. Aplicar los campos ANTES de validar.
        # Validar primero hacía imposible completar un sub-estado con
        # `required_fields`: el gate pedía el campo que este mismo submit iba a
        # registrar, así que toda transición con campos obligatorios devolvía
        # "Missing required fields" y el workflow quedaba trabado (medido
        # 2026-11-17 con `preconditions` en ANALYZE.ANALYZE).
        # La atomicidad se mantiene: si la validación falla, no se persiste.
        candidate = state.record_submit(state.current_sub_state, fields) if fields else state

        # 5. Validate required_fields sobre el estado resultante
        errors = ValidationService.check_transition_preconditions(candidate, graph, "")
        if errors:
            return {
                "ok": False,
                "error": "Missing required fields",
                "errors": errors,
                "action": "validation_failed",
            }
        state = candidate

        # 6. Execute sub_state.action via TaskRegistry (Sprint 5 — engine anémico)
        task_result, task_ok, task_field = self._execute_action(
            ctx, sub_state, state, fields
        )
        if task_result is not None and not task_ok:
            # Task failed → fail-loud, no auto-advance (G-W6 never empty success).
            error_msg = (
                task_field
                or f"Action '{sub_state.action}' failed"
            )
            state = state.record_node_execution(
                node=state.current_node,
                sub_state=state.current_sub_state,
                execution_type=sub_state.execution.value if sub_state else ExecutionType.MANUAL.value,
                action=sub_state.action if sub_state else "",
                submitted_fields=fields,
                task_ok=False,
                task_result={"error": error_msg, "action": sub_state.action},
                error=error_msg,
                error_type="task_failed",
            )
            if not self._save_state(state):
                return {"ok": False, "error": "Failed to persist state"}
            return {
                "ok": False,
                "action": "task_failed",
                "error": error_msg,
                "task_action": sub_state.action if sub_state else "",
            }

        # 6b. Record NodeExecution
        state = state.record_node_execution(
            node=state.current_node,
            sub_state=state.current_sub_state,
            execution_type=sub_state.execution.value if sub_state else ExecutionType.MANUAL.value,
            action=sub_state.action if sub_state else "",
            submitted_fields=fields,
            task_ok=task_ok,
            task_result=task_result,
        )

        # 6c. Recordar la tarea declarada (si la acción la devolvió)
        state = self._remember_task_id(state, task_result)

        # 6d. Side effects (contrato con SideEffectEngine). Defensivo: sin
        # `apply_side_effects` es no-op; nunca propaga; si falla un efecto no
        # opcional se devuelve failure sin guardar estado.
        try:
            engine = getattr(self._bundle, "side_effects", None)
            apply_fn = getattr(engine, "apply_side_effects", None)
            if callable(apply_fn):
                eff = apply_fn(sub_state, state, self._bundle.chain_dir, ctx=ctx, fields=fields)
                if isinstance(eff, dict) and eff.get("ok") is False:
                    return {
                        "ok": False,
                        "action": "side_effect_failed",
                        "error": eff.get("error"),
                    }
        except Exception as e:  # noqa: BLE001 — fail-open
            logger.warning("side_effect failed (fail-open): %s", e)

        # 7. Save state (repository handles CAS + revision bump)
        if not self._save_state(state):
            return {"ok": False, "error": "Failed to persist state"}

        # 9. Trace submit
        self._trace_submit(ctx, state, fields)

        result = {
            "ok": True,
            "node": state.current_node,
            "sub_state": state.current_sub_state,
            "position": f"{state.current_node}.{state.current_sub_state}",
            "revision": state.revision,
            "data": fields,
            # El agente no debe adivinar el paso siguiente ni pagar un
            # `workflow_state` extra para saberlo.
            "next_action": next_action_for(state, graph),
        }

        # 9b. Si la decisión mapea a un gate y el nodo terminó acá, se cruza en
        # la MISMA llamada. Antes eran tres (submit → advance → transition) y
        # el protocolo se descubría por error (medido 2026-11-18).
        decision = str((fields or {}).get("decision") or "")
        node_now = graph.get_node(state.current_node)
        at_last = bool(node_now) and state.sub_state_index >= len(node_now.sub_states) - 1
        if decision and at_last:
            gate_name = decision_map_for(
                ValidationService.get_available_gates(state, graph)
            ).get(decision)
            if gate_name:
                moved = self.transition(ctx, gate_name, reason=f"submit:{decision}")
                if isinstance(moved, dict) and moved.get("ok"):
                    moved["submitted"] = True
                    moved["decision"] = decision
                    return moved
                result["gate_error"] = str((moved or {}).get("error") or "")
        return result

    def remember_task(self, ctx: ExecutionContext, task_id: str) -> bool:
        """Persiste el `task_id` de la tarea declarada en el estado del workflow.

        `implement_task` se llama como TOOL, no como acción de un nodo, así que
        el motor no se entera de la tarea salvo que alguien se lo diga. Esto es
        ese puente: a partir de acá, toda acción recibe el id sin que el
        llamador lo repita.
        """
        task_id = str(task_id or "").strip()
        if not task_id:
            return False
        state = self._load_state()
        if state is None:
            return False
        if (state.context or {}).get("task_id") == task_id:
            return True
        state = state.with_context("task_id", task_id)
        return bool(self._save_state(state))

    @staticmethod
    def _remember_task_id(state: WorkflowState, task_result: Any) -> WorkflowState:
        """Guarda el `task_id` que devolvió una acción (p. ej. implement_task).

        Sin esto el id vivía solo en la respuesta: la siguiente acción de la
        tarea no lo conocía y `verify` caía a la heurística de «la más reciente».
        """
        data = (task_result or {}).get("data") if isinstance(task_result, dict) else None
        tid = str((data or {}).get("task_id", "") or "").strip()
        if tid and tid != (state.context or {}).get("task_id"):
            return state.with_context("task_id", tid)
        return state

    def advance(
        self,
        ctx: ExecutionContext,
        fields: dict[str, Any] | None = None,
    ) -> dict:
        """Avanza al SIGUIENTE sub-estado del nodo actual.

        Faltaba: `sub_state_index` no se modificaba en ningún lado del motor, así
        que cada nodo quedaba clavado en su primer sub-estado para siempre
        (medido 2026-11-17: los 7 proyectos registrados en `START.LIST`). Los
        prompts ya decían "usa advance()" y la tool no existía.

        Orden: graba los campos (candidato), valida los requeridos del sub-estado
        actual, ejecuta la acción si el sub-estado es `auto`, y mueve el índice.
        Si el nodo se terminó, NO inventa una transición: devuelve los gates
        disponibles (esa decisión es del nodo siguiente).
        """
        try:
            return self._advance_impl(ctx, fields or {})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e), "action": "advance_error"}

    def _advance_impl(self, ctx: ExecutionContext, fields: dict[str, Any]) -> dict:
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        # Auto-init igual que `submit`: un proyecto sin estado arranca en el
        # sub-estado inicial del grafo.
        if not state.current_node and graph.initial_state:
            from dataclasses import replace
            init_node = graph.get_node(graph.initial_state)
            init_sub = init_node.sub_states[0].key if init_node and init_node.sub_states else ""
            state = replace(
                state,
                current_node=graph.initial_state,
                current_sub_state=init_sub,
            )

        state, _ = state.reconcile_index(graph)
        node = graph.get_node(state.current_node)
        if not node:
            return {"ok": False, "error": f"Current node '{state.current_node}' not in graph"}
        sub_state = node.find_sub_state(state.current_sub_state)
        if not sub_state:
            return {
                "ok": False,
                "error": f"Sub-state '{state.current_sub_state}' not in node '{state.current_node}'",
            }

        # 1. Grabar antes de validar (mismo deadlock que tenía `submit`).
        candidate = state.record_submit(state.current_sub_state, fields) if fields else state
        errors = ValidationService.check_transition_preconditions(candidate, graph, "")
        if errors:
            return {
                "ok": False,
                "error": "Missing required fields",
                "errors": errors,
                "action": "validation_failed",
            }

        # 1b. `advance` NO puede cruzar una decisión del usuario. Sin esto,
        # SPRINT.PLAN_APPROVAL avanzaba a TASK_SPEC sin `decision` (medido
        # 2026-11-18) y el agente decidía por el usuario. Se leen los campos
        # del `input_schema.required` cuando el sub-estado no declara
        # `required_fields` propios (hoy la mayoría no los declara).
        exec_type = getattr(sub_state, "execution", None)
        exec_value = getattr(exec_type, "value", exec_type) or ""
        if exec_value == ExecutionType.USER_INPUT.value:
            required = list(getattr(sub_state, "required_fields", None) or [])
            if not required:
                required = list((getattr(sub_state, "input_schema", {}) or {}).get("required") or [])
            if not required:
                # En algunos `user_input` (p. ej. DESIGN_REVIEW) la decisión
                # está declarada en el output_schema, no en el input.
                required = list((getattr(sub_state, "output_schema", {}) or {}).get("required") or [])
            sub_data = candidate.get_sub_state_data(state.current_sub_state)
            missing = [f for f in required if not sub_data.is_field_complete(f)]
            if missing:
                return {
                    "ok": False,
                    "action": "user_input_required",
                    "error": (
                        f"{state.current_node}.{state.current_sub_state} es una decisión "
                        f"del usuario: faltan {missing}. Mostrale la pregunta y esperá su "
                        f"respuesta; no decidas por él."
                    ),
                    "required": required,
                    "missing": missing,
                }

        # 2. Ejecutar la acción del sub-estado (los `auto` corren sin el modelo).
        #    Un `llm_task` produce su salida DESDE los campos enviados. Si
        #    `advance` corre la acción con `{}` (porque el submit fue antes),
        #    la ejecuta dos veces y la segunda pisa la primera: medido
        #    2026-11-18, VERIFY.MONITOR perdió `plan`/`deviations` y quedó
        #    `compliant=true` sin desviaciones. Se mezclan los campos
        #    registrados con los nuevos (los nuevos ganan).
        action_fields = merge_action_fields(state, state.current_sub_state, fields)
        task_result = None
        if sub_state.action:
            task_result, task_ok, task_field = self._execute_action(
                ctx, sub_state, candidate, action_fields
            )
            if task_result is not None and not task_ok:
                return {
                    "ok": False,
                    "action": "task_failed",
                    "error": task_field or f"Action '{sub_state.action}' failed",
                    "task_action": sub_state.action,
                }

        # 3. Mover el índice dentro del nodo.
        next_index = state.sub_state_index + 1
        if next_index >= len(node.sub_states):
            return {
                "ok": False,
                "action": "node_complete",
                "error": f"Node '{state.current_node}' complete: transition with a gate",
                "available_gates": [
                    g.name for g in ValidationService.get_available_gates(state, graph)
                ],
            }

        next_sub = node.sub_states[next_index]
        new_state = candidate.with_node(state.current_node, next_sub.key, next_index)
        new_state = new_state.record_node_execution(
            node=state.current_node,
            sub_state=state.current_sub_state,
            execution_type=sub_state.execution.value,
            action=sub_state.action,
            submitted_fields=action_fields,
            task_ok=True,
            task_result=task_result,
        )
        new_state = self._remember_task_id(new_state, task_result)

        # Side effects (contrato con SideEffectEngine) al completar el sub-estado.
        try:
            engine = getattr(self._bundle, "side_effects", None)
            apply_fn = getattr(engine, "apply_side_effects", None)
            if callable(apply_fn):
                eff = apply_fn(sub_state, new_state, self._bundle.chain_dir, ctx=ctx, fields=fields)
                if isinstance(eff, dict) and eff.get("ok") is False:
                    return {
                        "ok": False,
                        "action": "side_effect_failed",
                        "error": eff.get("error"),
                    }
        except Exception as e:  # noqa: BLE001 — fail-open
            logger.warning("side_effect failed (fail-open): %s", e)

        if not self._save_state(new_state):
            return {"ok": False, "error": "Failed to persist state"}
        self._trace_submit(ctx, new_state, fields)

        return {
            "ok": True,
            "node": new_state.current_node,
            "sub_state": new_state.current_sub_state,
            "position": f"{new_state.current_node}.{new_state.current_sub_state}",
            "revision": new_state.revision,
            # Lo que devolvió la acción (baseline, diff, tests): sin esto el
            # agente tenía que leer node_executions para verlo.
            "action_result": task_result,
            "next_action": next_action_for(new_state, graph),
        }

    def _execute_action(
        self,
        ctx: ExecutionContext,
        sub_state: Any,
        state: WorkflowState,
        fields: dict[str, Any],
    ) -> tuple[Any, bool, str]:
        """Execute sub_state.action via TaskRegistry (Sprint 5).

        Mirrors legacy mcp/shared/core/engine/.../submit.py `_execute_task`:

          - No action → (None, True, "") — nothing to execute, task assumed OK
            (there is no declared work to run).
          - Action set but NO task_registry in bundle → FAIL-LOUD (composition
            error, D1/capa_rout.md): the engine MUST NOT silently advance
            without running the declared action.
          - Action executed → returns (task_result, task_ok, task_error).

        G-W6 anti-hallucination: verify_predicates with no predicates must
        NOT pass as empty-success — the target handler already returns
        ok=False in that case, which we propagate (no auto-advance).
        """
        action = sub_state.action if sub_state else ""
        if not action:
            return None, True, ""

        registry = getattr(self._bundle, "task_registry", None)
        if registry is None:
            # Fail-loud: declared action but no registry (broken composition).
            raise WorkflowEngineError(
                f"Sub-state '{state.current_node}.{state.current_sub_state}' "
                f"declares action='{action}' but the engine has no task_registry "
                f"injected (composition root broken). Inject it via context/setup."
            )

        if not registry.has(action):
            raise WorkflowEngineError(
                f"TaskRegistry: action '{action}' not found for "
                f"'{state.current_node}.{state.current_sub_state}'"
            )

        # Build a duck-typed execution context exposing ONLY the attributes the
        # tasks handlers consume (chain_dir, task_id, session_id). We do NOT
        # import the tasks feature's ExecutionContext here (R1: workflow must
        # not import tasks); the injected registry accepts any context object
        # with those attributes.
        from types import SimpleNamespace

        # `task_id` NO se rellena con el nombre de la acción: una acción no es
        # una tarea. Ese fallback hacía que `implement_task` persistiera
        # `task-capture_baseline` como id, que `verify` no encontrara la spec y
        # que el ancla del contexto no matcheara nunca. La acción viaja aparte.
        # El id puede venir del caller o estar ya recordado en el estado: una
        # vez que `implement_task` declara la tarea, CADA acción posterior la
        # conoce sin que el llamador tenga que repetirla.
        task_id = (ctx.task_id if ctx else "") or str((state.context or {}).get("task_id", "") or "")
        exec_ctx = SimpleNamespace(
            chain_dir=self._bundle.chain_dir,
            task_id=task_id,
            session_id=(ctx.session_id if ctx else "") or "mcp",
            cpg_adapter=getattr(self._bundle, "cpg_adapter", None),
        )
        exec_data = {
            "task_id": task_id,
            "action": action,
            "chain_dir": self._bundle.chain_dir,
            **fields,
        }

        # The bundle.task_registry is a tasks.feature TaskRegistry whose
        # execute returns TaskResult(ok, data, error).
        result = registry.execute(action, exec_ctx, exec_data)
        ok = bool(getattr(result, "ok", True))
        error = getattr(result, "error", "") or ""
        data = getattr(result, "data", None) or {}
        return {"action": action, "data": data}, ok, error

    def _skill_candidates_scored(
        self, ctx: ExecutionContext, intent: dict
    ) -> list[tuple[str, int]]:
        """[(skill, score)] que matchean la intención; [] si no hay/falla."""
        try:
            from fast_mcp.shared_kernel.paths import walker_root
            from fast_mcp.shared_kernel.project_support import ProjectService
            from fast_mcp.shared_kernel.skills import (
                discover,
                score as skill_score,
                search,
                skills_roots,
            )

            project_root = ""
            try:
                project_root = str(ProjectService.resolve_paths(ctx.chain_dir).project_root or "")
            except Exception:  # noqa: BLE001 — se escanea solo lo global
                project_root = ""
            skills = discover(skills_roots(project_root or None, walker_root()))
            needs = str(intent.get("needs") or "")
            tags = intent.get("tags") or ()
            if isinstance(tags, str):
                tags = [tags]
            hits = search(skills, needs, tuple(str(t) for t in tags))
            return [(s.name, skill_score(s, needs, tags)) for s in hits]
        except Exception as e:  # noqa: BLE001 — el gate no rompe el submit
            logger.debug("skill gate: discovery falló (%s)", e)
            return []

    def _skill_candidates(self, ctx: ExecutionContext, intent: dict) -> list[str]:
        """Nombres de candidatos (sin umbral: catálogo advisory)."""
        return [name for name, _ in self._skill_candidates_scored(ctx, intent)]

    def _check_skill_gate(self, ctx: ExecutionContext, sub_state: Any, state: Any) -> dict | None:
        """Gate de skills: exige `load_skill` de un candidato (anti-deadlock).

        - Solo aplica con `skill_intent.policy == "required"`.
        - Sin candidatos en el entorno → pasa con nota: bloquear sería un
          deadlock (el agente no puede cargar un skill que no existe).
        - La carga tiene que ser posterior a `state.updated_at` y de un
          candidato, para que no la satisfaga un load viejo de otra fase.
        """
        intent = getattr(sub_state, "skill_intent", None) or {}
        if not isinstance(intent, dict) or intent.get("policy") != "required":
            return None

        scored = self._skill_candidates_scored(ctx, intent)
        candidates = [name for name, sc in scored if sc >= MIN_SKILL_SCORE_REQUIRED]
        if not candidates:
            self._trace_skill_gate(ctx, state, "no_candidates", [])
            return {
                "ok": True,
                "note": (
                    f"sin skills con match suficiente (score >= "
                    f"{MIN_SKILL_SCORE_REQUIRED}) en este entorno: el gate de "
                    "skills no aplica"
                ),
                "skills_checked": [],
            }

        tracer = getattr(self._bundle, "tracer", None)
        loaded = False
        if tracer is not None and hasattr(tracer, "has_skill_load"):
            try:
                loaded = tracer.has_skill_load(
                    self._bundle.chain_dir,
                    task_id=ctx.task_id or "",
                    skills=set(candidates),
                    since=str(getattr(state, "updated_at", "") or ""),
                )
            except Exception:  # noqa: BLE001 — sin tracer verificable no se bloquea
                loaded = True

        if loaded:
            self._trace_skill_gate(ctx, state, "passed", candidates)
            return {"ok": True, "skills_checked": candidates}

        self._trace_skill_gate(ctx, state, "blocked", candidates)
        # `error` es el canal que TODO el resto lee. Dejar el motivo solo en
        # `message` hacía que un bloqueo se viera como silencio (`error: None`)
        # y el agente no pudiera diagnosticarlo (medido 2026-11-18).
        skill_message = (
            "Antes de avanzar, cargá uno de los skills de esta fase con "
            "`load_skill(<nombre>)`: " + ", ".join(candidates)
        )
        return {
            "ok": False,
            "action": "gate_blocked",
            "blocked": True,
            "error_type": "required_skill_gate",
            "error": skill_message,
            "message": skill_message,
            "skills_checked": candidates,
        }

    def _trace_skill_gate(
        self, ctx: ExecutionContext, state: Any, outcome: str, candidates: list[str]
    ) -> None:
        """Deja constancia del gate en el trace.

        Sin esto no hay forma de responder "¿cuántas veces bloqueó?" sin leer
        logs: `outcome` es blocked | passed | no_candidates.
        """
        tracer = getattr(self._bundle, "tracer", None)
        if tracer is None or not hasattr(tracer, "trace"):
            return
        try:
            tracer.trace(
                self._bundle.chain_dir,
                "skill_gate",
                {
                    "outcome": outcome,
                    "skills": candidates,
                    "node": getattr(state, "current_node", ""),
                    "sub_state": getattr(state, "current_sub_state", ""),
                },
                task_id=ctx.task_id or "",
            )
        except Exception as e:  # noqa: BLE001 — trazabilidad best-effort
            logger.debug("skill gate: no se pudo trazar (%s)", e)

    def _check_required_tools_gate(
        self, ctx: ExecutionContext, sub_state: Any
    ) -> dict:
        """Check if required_tools gate is satisfied via trace."""
        required = [t for t in (sub_state.required_tools or []) if isinstance(t, dict)]
        layers = {t.get("id", "") for t in required if t.get("id")}
        if not layers:
            return {"ok": True}

        # Check trace for recent kg_query
        try:
            has_query = self._bundle.tracer.has_kg_query(
                self._bundle.chain_dir,
                task_id=ctx.task_id or "",
                session_id=ctx.session_id or "",
                layers=layers,
            )
            if has_query:
                return {"ok": True, "layers_checked": sorted(layers)}
        except Exception:
            pass  # fail-open on tracer errors

        # Gate blocked
        hints = "; ".join(
            f"{t.get('id', '?')}: {t.get('command_hint', '')}" for t in required
        )
        tools_message = (
            "This step requires consulting tools before submit: "
            f"{hints}. Invoke the tool and retry submit."
        )
        return {
            "ok": False,
            "action": "gate_blocked",
            "blocked": True,
            "error_type": "required_tools_gate",
            "error": tools_message,
            "message": tools_message,
        }

    # ── transition ──────────────────────────────────────────────────────

    def transition(
        self,
        ctx: ExecutionContext,
        gate: str,
        reason: str = "",
        skip_preconditions: bool = False,
    ) -> dict:
        """Transition between nodes via a gate.

        Pipeline:
            1. Load state + graph
            2. Check if blocked (resolve if possible)
            3. Find gate + validate source
            4. Check preconditions (unless skip)
            5. Apply transition
            6. Record NodeExecution
            7. Save state
            8. Trace transition
            9. Return result
        """
        try:
            return self._transition_impl(ctx, gate, reason, skip_preconditions)
        except Exception as e:
            return {"ok": False, "error": str(e), "action": "transition_error"}

    def _transition_impl(
        self,
        ctx: ExecutionContext,
        gate: str,
        reason: str,
        skip_preconditions: bool,
    ) -> dict:
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        # 1. Check if blocked — resolve with "force" strategy
        if BlockResolver.is_blocked(state):
            resolve_result = BlockResolver.resolve(
                state, "force", {"reason": reason or "transition"}, graph=graph
            )
            if is_successful(resolve_result):
                state = resolve_result.value_or(state)

        # 2. Find gate
        gate_obj = graph.find_gate(state.current_node, gate)
        if gate_obj is None:
            return {
                "ok": False,
                "error": f"Gate '{gate}' not found for node '{state.current_node}'",
                "action": "gate_not_found",
            }

        # 3. Validate source node
        if gate_obj.source_node != state.current_node:
            return {
                "ok": False,
                "error": (
                    f"Gate '{gate}' source is '{gate_obj.source_node}', "
                    f"but current node is '{state.current_node}'"
                ),
                "action": "invalid_source",
            }

        # 3b. Validate source sub-state. Un gate de flujo principal solo se
        # dispara desde su sub-estado declarado; sin esto, `all_done` desde
        # PLANNING y `start_task` desde TASK_REVIEW pasaban (medido 2026-11-18).
        if (
            getattr(gate_obj, "source_sub_state", "")
            and gate_obj.source_sub_state != state.current_sub_state
        ):
            return {
                "ok": False,
                "error": (
                    f"Gate '{gate}' can only be used from "
                    f"'{state.current_node}.{gate_obj.source_sub_state}', "
                    f"current sub-state is "
                    f"'{state.current_node}.{state.current_sub_state}'"
                ),
                "action": "invalid_source_sub_state",
            }

        # 3c. Contract.pre: `ckg_has_decision('X')` se evalúa contra la decisión
        # registrada en el sub-estado fuente. Sin esto, el gate pasaba sin
        # decisión (medido: plan_approval_approved sin 'approve').
        contract = getattr(gate_obj, "contract", None) or {}
        pre = contract.get("pre", "") if isinstance(contract, dict) else ""
        if pre and "ckg_has_decision(" in pre:
            import re

            match = re.search(r"ckg_has_decision\('([^']+)'\)", pre)
            if match:
                expected = match.group(1)
                # Si el gate declara source_sub_state, la decisión vive ahí.
                # Si no (p. ej. design_approved desde DESIGN_GATHER), se busca
                # en los sub-estados del nodo actual: la decisión la registra
                # DESIGN_REVIEW/ANALYZE.REVIEW, no el sub-estado de origen.
                source_sub = gate_obj.source_sub_state or state.current_sub_state
                node = graph.get_node(state.current_node)
                node_keys = (
                    [ss.key for ss in node.sub_states]
                    if node
                    else [state.current_sub_state]
                )
                if gate_obj.source_sub_state:
                    # El source manda, pero la decisión puede vivir en otro
                    # sub-estado del nodo: DESIGN_REVIEW aprueba y CKG_SAVE es
                    # el source del gate después de capturar el diseño.
                    candidates = [gate_obj.source_sub_state] + [
                        key for key in node_keys if key != gate_obj.source_sub_state
                    ]
                else:
                    candidates = node_keys
                found = ""
                for key in candidates:
                    data = state.sub_state_data.get(key)
                    if data is None:
                        continue
                    decision = str((getattr(data, "fields", {}) or {}).get("decision", ""))
                    if decision == expected:
                        found = decision
                        break
                    if decision and not found:
                        found = decision
                if found != expected:
                    return {
                        "ok": False,
                        "action": "contract_precondition_failed",
                        "error": (
                            f"Gate '{gate}' requires decision '{expected}' at "
                            f"{state.current_node}.{source_sub}; "
                            f"found '{found or '(none)'}'"
                        ),
                        "expected": expected,
                        "found": found,
                    }

        # 4. Check preconditions (unless skipped)
        if not skip_preconditions:
            pre_errors = ValidationService.check_transition_preconditions(
                state, graph, gate
            )
            if pre_errors:
                return {
                    "ok": False,
                    "error": "Transition preconditions not met",
                    "errors": pre_errors,
                    "action": "precondition_failed",
                }

        # 5. Capture from_node for tracing
        from_node = f"{state.current_node}.{state.current_sub_state}"

        # 6. Apply transition. Para `project_selected` hacia SPRINT, resolver el
        # sub-estado destino según el estado real del sprint del proyecto:
        # active -> ACTIVE, planning -> PLANNING. Fail-open: si no se puede
        # resolver, se deja el declarado (nunca rompe la transición).
        target_node = gate_obj.target_node
        target_sub_state = gate_obj.target_sub_state
        if gate == "project_selected" and target_node == "SPRINT":
            try:
                from fast_mcp.composition import _build_sprint_service

                svc = _build_sprint_service(self._bundle.chain_dir)
                sprint_status = None
                resolved = False
                session_id = getattr(ctx, "session_id", "") or ""
                if svc is not None:
                    # 1. Sprint de la sesión.
                    get_session = getattr(svc, "get_session", None)
                    get_spring = getattr(svc, "get_spring", None)
                    if session_id and callable(get_session) and callable(get_spring):
                        try:
                            sess = get_session(session_id)
                            sid = (sess or {}).get("spring_id") if isinstance(sess, dict) else None
                            if sid:
                                sp = get_spring(sid)
                                if sp is not None:
                                    sprint_status = str(
                                        (sp.get("status") if isinstance(sp, dict) else getattr(sp, "status", ""))
                                        or ""
                                    ).lower()
                                    resolved = True
                        except Exception:  # noqa: BLE001
                            sprint_status = None
                            resolved = False
                    # 2. Sprint activo del proyecto.
                    if not resolved:
                        get_active = getattr(svc, "get_active_spring", None)
                        if callable(get_active):
                            try:
                                active = get_active()
                                if active is not None:
                                    sprint_status = str(
                                        (active.get("status") if isinstance(active, dict) else getattr(active, "status", ""))
                                        or ""
                                    ).lower()
                                    resolved = True
                            except Exception:  # noqa: BLE001
                                sprint_status = None
                                resolved = False
                    # 3. Planning determinista.
                    if not resolved:
                        list_springs = getattr(svc, "list_springs", None)
                        if callable(list_springs):
                            try:
                                plannings = list_springs(status="planning") or []
                                if plannings:
                                    ordered = sorted(
                                        plannings,
                                        key=lambda s: (
                                            ((s.get("updated_at") if isinstance(s, dict) else getattr(s, "updated_at", "")) or ""),
                                            ((s.get("spring_id") if isinstance(s, dict) else getattr(s, "spring_id", "")) or ""),
                                        ),
                                        reverse=True,
                                    )
                                    sprint_status = "planning"
                                    resolved = True
                            except Exception:  # noqa: BLE001
                                sprint_status = None
                                resolved = False
                if sprint_status == "active":
                    target_sub_state = "ACTIVE"
                elif sprint_status == "planning":
                    target_sub_state = "PLANNING"
            except Exception:  # noqa: BLE001 — fail-open, nunca romper
                logger.debug("resolve sprint sub-state failed (fail-open)")

        # 6. Apply transition
        try:
            state = TransitionService.apply(
                state,
                graph,
                target_node,
                target_sub_state,
            )
        except NodeNotFoundError as e:
            return {"ok": False, "error": str(e), "action": "target_not_found"}

        # 7. Record NodeExecution
        state = state.record_node_execution(
            execution_type="transition",
            gate=gate,
            submitted_fields={"reason": reason} if reason else {},
            task_ok=True,
            task_result={
                "from_node": gate_obj.source_node,
                "to_node": gate_obj.target_node,
                "to_sub_state": gate_obj.target_sub_state or "",
            },
        )

        # 7b. Al ENTRAR a otro nodo, su data de sub-estados se descarta: es de
        # la pasada anterior. Sin esto, un gate de usuario se cruzaba con la
        # decisión vieja (medido 2026-11-18: DESIGN_REVIEW seguía con el
        # `approve` de SPEC-002 y el `advance` de SPEC-001 lo daba por hecho).
        if gate_obj.target_node != gate_obj.source_node:
            target_node = graph.get_node(gate_obj.target_node)
            if target_node is not None:
                stale = {ss.key for ss in target_node.sub_states}

                def _is_target_data(key: Any) -> bool:
                    text = str(key)
                    if "." in text:
                        node_part, _, sub_part = text.partition(".")
                        return node_part == gate_obj.target_node and sub_part in stale
                    return text in stale

                cleaned = {
                    key: value
                    for key, value in (state.sub_state_data or {}).items()
                    if not _is_target_data(key)
                }
                if len(cleaned) != len(state.sub_state_data or {}):
                    from dataclasses import replace as _replace

                    state = _replace(state, sub_state_data=cleaned)

        # 8. Save state (repository handles CAS + revision bump)
        if not self._save_state(state):
            return {"ok": False, "error": "Failed to persist state"}

        # 10. Trace transition
        to_node = f"{state.current_node}.{state.current_sub_state}"
        self._trace_transition(ctx, gate, from_node, to_node)

        return {
            "ok": True,
            "from_node": gate_obj.source_node,
            "to_node": gate_obj.target_node,
            "to_sub_state": state.current_sub_state,
            "position": f"{state.current_node}.{state.current_sub_state}",
            "gate": gate,
            "revision": state.revision,
            "next_action": next_action_for(state, graph),
        }

    # ── status ──────────────────────────────────────────────────────────

    def status(self, ctx: ExecutionContext, view: str = "light") -> dict:
        """Read-only snapshot of the current workflow state.

        INV-S2-06: never mutates state. `view=light` (default) omite
        `prompt.data`, `node_executions` y `history`: el agente no los usa para
        decidir el próximo paso y son el mayor costo fijo por llamada.
        """
        full = str(view or "").lower() == "full"
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        # Auto-init: un proyecto sin `.workflow-state.json` está en el estado
        # inicial del grafo, no en ".". Sin esto el plugin recibía
        # `current_position: "."`, no reconocía un sub-estado `auto` y nunca
        # avanzaba: el proyecto quedaba congelado (medido 2026-11-17).
        if not state.current_node and graph.initial_state:
            from dataclasses import replace
            init_node = graph.get_node(graph.initial_state)
            init_sub = init_node.sub_states[0].key if init_node and init_node.sub_states else ""
            state = replace(
                state,
                current_node=graph.initial_state,
                current_sub_state=init_sub,
            )
        state, _ = state.reconcile_index(graph)

        node = graph.get_node(state.current_node)
        sub_state_def = node.find_sub_state(state.current_sub_state) if node else None

        # Available gates
        available_gates = []
        if node:
            gates = ValidationService.get_available_gates(state, graph)
            for g in gates:
                available_gates.append({
                    "name": g.name,
                    "target": f"{g.target_node}.{g.target_sub_state or '*'}"
                    if g.target_sub_state
                    else g.target_node,
                    "description": g.description,
                })

        # Last 10 executions
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
                "duration_ms": ne.duration_ms,
            })

        from fast_mcp.shared_kernel.build_info import build_info

        exec_type_now = sub_state_def.execution.value if sub_state_def else "unknown"
        auto_step = bool(sub_state_def and sub_state_def.is_auto())
        prompt = (
            None if auto_step else self._resolve_current_prompt(state, graph, ctx)
        )
        gates_now = ValidationService.get_available_gates(state, graph) if node else []
        return {
            "ok": True,
            "node": state.current_node,
            "sub_state": state.current_sub_state,
            "sub_state_index": state.sub_state_index,
            "execution_type": sub_state_def.execution.value
            if sub_state_def
            else "unknown",
            # Una sola fuente: lo que el paso exige de verdad (no el atributo
            # `required_fields`, que solía venir vacío aunque el esquema pidiera).
            "required_fields": declared_required_fields(sub_state_def)
            if sub_state_def
            else [],
            "expected_payload": expected_payload(sub_state_def) if sub_state_def else {},
            "next_action": next_action_for(state, graph),
            "decision_map": decision_map_for(gates_now),
            "user_message": extract_user_message(
                (prompt or {}).get("template") or "",
                f"{state.current_node}.{state.current_sub_state}",
            ),
            **({"history": list(state.history), "node_executions": last_executions} if full else {}),
            # La forma de `available_gates` no cambia entre vistas: el costo
            # está en `prompt.data` y en las ejecuciones, no en 1-8 gates.
            "available_gates": available_gates,
            "revision": state.revision,
            "blocked": bool(state.blocked_at),
            "task_id": ctx.task_id,
            "session_id": ctx.session_id,
            "prompt": prompt_for_view(prompt, view),
            # Handshake: si `stale` es true, el proceso corre código anterior al
            # disco; el agente no debe concluir que un fix "no sirve".
            "build": build_info(),
        }

    # ── help ────────────────────────────────────────────────────────────

    def help(self, ctx: ExecutionContext, view: str = "light") -> dict:
        """Current state summary + available operations.

        Read-only: INV-S2-06 (el auto-init no persiste nada).

        `view`: `light` (default) devuelve lo que el agente necesita para
        decidir el próximo paso; `full` agrega `prompt.data`, `gates_detail` e
        historial. El default bajó el payload de 3,79× el template a ~1×.
        """
        full = str(view or "").lower() == "full"
        graph = self._load_graph()
        state = self._load_state()
        if graph is None or state is None:
            return {"ok": False, "error": "Failed to load workflow state"}

        # Auto-init: el plugin lee por acá. Un proyecto sin estado está en el
        # sub-estado inicial del grafo, no en ".". Sin esto recibía
        # `current_position: "."`, no lo reconocía como `auto` y el workflow
        # nunca arrancaba (medido 2026-11-17: 7/7 proyectos congelados).
        if not state.current_node and graph.initial_state:
            from dataclasses import replace
            init_node = graph.get_node(graph.initial_state)
            init_sub = init_node.sub_states[0].key if init_node and init_node.sub_states else ""
            state = replace(
                state,
                current_node=graph.initial_state,
                current_sub_state=init_sub,
            )

        node = graph.get_node(state.current_node)
        sub_state_def = node.find_sub_state(state.current_sub_state) if node else None

        # Available gates
        available_gates = []
        if node:
            gates = ValidationService.get_available_gates(state, graph)
            for g in gates:
                available_gates.append({
                    "name": g.name,
                    "target": g.target_node,
                    "description": g.description,
                })

        # Required fields for current sub-state: una sola fuente derivada
        # (attribute + input_schema + output_schema), la misma que usa `advance`.
        required_fields = declared_required_fields(sub_state_def) if sub_state_def else []

        # Execution type guidance
        exec_type = sub_state_def.execution.value if sub_state_def else "unknown"

        # ¿El paso actual ya quedó satisfecho? Lo consulta el plugin para saber
        # si corresponde negar escrituras (`edit`/`write`) hasta que el agente
        # registre el paso. Se calcula sobre los campos del sub-estado actual,
        # no sobre todo el nodo: un requerido viejo ya no bloquea.
        sub_data = state.get_sub_state_data(state.current_sub_state)
        step_satisfied = sub_data.all_required_complete(required_fields)

        from fast_mcp.shared_kernel.build_info import build_info

        # En un paso automático el prompt no lo lee nadie: lo ejecuta el
        # motor. Devolverlo es el ruido más barato de eliminar (medido: 7
        # plantillas por encima de 800 chars que el agente nunca usa).
        # La regla es del DOMINIO (`SubState.is_auto` cubre auto y bootstrap),
        # no una lista de strings que se desincroniza.
        auto_step = bool(sub_state_def and sub_state_def.is_auto())
        prompt = (
            None if auto_step else self._resolve_current_prompt(state, graph, ctx)
        )
        return {
            "ok": True,
            "current_position": f"{state.current_node}.{state.current_sub_state}",
            "execution_type": exec_type,
            "required_fields": required_fields,
            "expected_payload": expected_payload(sub_state_def) if sub_state_def else {},
            "next_action": next_action_for(state, graph),
            "decision_map": decision_map_for(gates),
            "user_message": extract_user_message(
                (prompt or {}).get("template") or "",
                f"{state.current_node}.{state.current_sub_state}",
            ),
            "step_satisfied": step_satisfied,
            "available_gates": [g["name"] for g in available_gates],
            **({"gates_detail": available_gates, "history": list(state.history[-5:])} if full else {}),
            "blocked": bool(state.blocked_at),
            "revision": state.revision,
            "task_id": str((state.context or {}).get("task_id", "") or ""),
            "prompt": prompt_for_view(prompt, view),
            "build": build_info(),
        }
