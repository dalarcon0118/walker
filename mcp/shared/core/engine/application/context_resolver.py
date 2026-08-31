"""
application/context_resolver.py — ContextResolver: engine-facing context resolution.

Roles:
  - Valida que los `needs` de un sub-estado tengan provider (engine, nodo, o global)
  - Promueve los `provides` de un nodo al global state (WorkflowState.context)
  - Inyecta contexto resuelto en ExecutionContext.baggage

NO confundir con ContextComposer (LLM-facing). Este resolver es para el ENGINE.

Flujo:
  1. Transición entre nodos → promote_provides() + validate_needs()
  2. Antes de step() → resolve_for_sub_state() → inyecta en baggage
  3. Después de submit() → check_auto_transition() si required_fields ok
"""

from __future__ import annotations

from dataclasses import replace
from typing import Set

from ..domain.models import (
    WorkflowState,
    WorkflowNode,
    WorkflowGraph,
    SubState,
)
from ..domain.session import SessionRecord


class ContextResolutionError(Exception):
    """Error cuando un need no tiene provider disponible."""

    def __init__(self, need: str, node_id: str, sub_key: str):
        self.need = need
        self.node_id = node_id
        self.sub_key = sub_key
        super().__init__(
            f"Context '{need}' requerido por {node_id}.{sub_key} "
            f"pero ningún provider lo produce"
        )


class ContextResolver:
    """Resuelve contexto para el engine basado en provides/needs.

    SSOT: WorkflowState.context para global state.
    Engine context: datos que el engine conoce intrínsecamente.
    """

    # Engine context keys — siempre disponibles, no necesitan provider
    ENGINE_CONTEXT_KEYS: Set[str] = {
        "session_id",
        "chain_dir",
        "current_node",
        "current_sub",
        "history",
    }

    def __init__(self):
        self._errors: list[str] = []

    # ── API principal ────────────────────────────────────────────────

    def resolve_for_sub_state(
        self,
        state: WorkflowState,
        node: WorkflowNode,
        sub_state: SubState,
        session: SessionRecord | None = None,
    ) -> dict:
        """Resuelve contexto para un sub-estado.

        Returns:
            Dict con contexto resuelto (engine + global + provides).
            Lanza ContextResolutionError si algún need no se cumple.
        """
        context = {}

        # 1. Engine context (siempre disponible)
        context["session_id"] = session.session_id if session else ""
        context["chain_dir"] = session.chain_dir if session else ""
        context["current_node"] = state.current_node
        context["current_sub"] = state.current_sub_state
        context["history"] = list(state.history)

        # 2. Process data (WorkflowState.process_data) — P2
        context.update(dict(state.process_data))

        # 3. Global state (WorkflowState.context) — backward compat
        context.update(dict(state.context))

        # 3. Node.provides (datos que el nodo produce y están en global state)
        for key in node.provides:
            if key in state.context:
                context[key] = state.context[key]

        # 4. Validar needs
        if sub_state.needs:
            for key in sub_state.needs:
                if key in context:
                    continue  # resuelto
                if key in self.ENGINE_CONTEXT_KEYS:
                    continue  # engine context (ya incluido arriba)
                # Resolver datos de negocio desde el DELTA (fuente unica del spring)
                if key in ("sprint_id", "task_id", "project_id"):
                    resolved = self._resolve_from_sprint_state(state, key)
                    if resolved is not None:
                        context[key] = resolved
                        continue
                raise ContextResolutionError(key, node.node_id, sub_state.key)

        return context

    @staticmethod
    def _resolve_from_sprint_state(state: WorkflowState, key: str) -> str | None:
        """Resuelve un need de negocio desde el DELTA (fuente unica del spring).

        PRINCIPIO: el workflow no duplica datos de negocio; los consulta
        desde el DeltaGraph (fuente unica del spring). Resuelve:
          - sprint_id:  spring._id
          - task_id:    spec actual del spring
          - project_id: spring.project_id
        """
        chain_dir = state.context.get("chain_dir", "")
        if not chain_dir:
            return None
        try:
            # El delta/spring es INCUMBENCIA DEL HOST (ADR-WALKER-067): el
            # engine NO consulta spring. Lee las keys del contexto del estado
            # (que el host provee); si no están, retorna None.
            if key in ("sprint_id", "task_id", "project_id"):
                return state.context.get(key) or None
        except Exception:
            pass
        return None

    def promote_provides(
        self,
        state: WorkflowState,
        node: WorkflowNode,
        sub_state: SubState | None = None,
    ) -> WorkflowState:
        """Promueve provides al global state. Retorna nuevo WorkflowState.

        Si se especifica sub_state, promueve SOLO las keys declaradas en
        ese sub-estado (intra-nodo, auto-advance).

        Si no, promueve todas las keys del nodo y sub-estados
        (transiciones entre nodos).

        ELM pattern: no muta state, retorna nueva instancia con context
        actualizado.
        """
        if sub_state:
            all_keys = set(sub_state.provides)
        else:
            all_keys = set(node.provides)
            for ss in node.sub_states:
                all_keys.update(ss.provides)

        pending = all_keys - set(state.process_data.keys()) - set(state.context.keys())
        if not pending:
            return state

        new_process_data = dict(state.process_data)
        current_node_id = node.node_id
        sub_state_keys = {ss.key for ss in node.sub_states}

        for data_key, ss_data in state.sub_state_data.items():
            if not ss_data:
                continue
            # Determinar si este sub_state_data pertenece al nodo actual
            if "." in data_key:
                # Prefijado: "ANALYZE.GATHER" → node_part = "ANALYZE"
                node_part = data_key.split(".")[0]
                if node_part != current_node_id:
                    continue
            else:
                # Sin prefijo: "GATHER" → solo pertenece si es sub_state del nodo actual
                if data_key not in sub_state_keys and data_key != current_node_id:
                    continue
            for key in pending & set(ss_data.fields.keys()):
                new_process_data[key] = ss_data.fields[key]
            pending -= set(ss_data.fields.keys())
            if not pending:
                break

        return replace(state, process_data=new_process_data)

    def apply_context_updates(
        self,
        state: WorkflowState,
        context_updates: list[dict] | None,
    ) -> WorkflowState:
        """Aplica context_updates de una edge y retorna nuevo WorkflowState.

        Cada update: {"from": "NODE.SUB.field", "to": "context.field"}

        ELM pattern: no muta state, retorna nueva instancia.
        """
        if not context_updates:
            return state

        new_process_data = dict(state.process_data)
        new_ctx = dict(state.context)

        for update in context_updates:
            from_path = update.get("from", "")
            to_field = update.get("to", "").replace("context.", "")

            if not from_path or not to_field:
                continue

            parts = from_path.split(".")
            if len(parts) >= 3:
                node_key, sub_key, field = parts[0], parts[1], parts[2]
                prefixed = f"{node_key}.{sub_key}"
                ss_data = state.sub_state_data.get(
                    prefixed
                ) or state.sub_state_data.get(sub_key)
                value = ss_data.fields.get(field, "") if ss_data else ""
            elif len(parts) == 2:
                sub_key, field = parts[0], parts[1]
                prefixed = f"{state.current_node}.{sub_key}"
                ss_data = state.sub_state_data.get(
                    prefixed
                ) or state.sub_state_data.get(sub_key)
                value = ss_data.fields.get(field, "") if ss_data else ""
            else:
                continue

            if value:
                new_process_data[to_field] = value
                new_ctx[to_field] = value

        return replace(state, process_data=new_process_data, context=new_ctx)

    # ── Validación en carga ─────────────────────────────────────────

    def validate_graph(self, graph: WorkflowGraph) -> list[str]:
        """Valida el flujo de contexto del grafo (ContextFlowGuard).

        PRINCIPIO (GAP-1/GAP-2): valida el contexto en AMBAS direcciones:
          1. needs SIN provider → un sub-estado pide contexto que nadie produce
          2. provides SIN consumidor → un nodo declara contexto que nadie
             consume (fuga de datos entre fases, ej: ANALYZE.provides=
             ['analysis_result'] que nunca llega a IMPLEMENT).

        Colecta todos los provides (engine + nodos + sub-estados) y todos
        los needs. Sin orden topológico, tolerante a ciclos en el grafo.

        Returns:
            Lista de errores de validación (vacía = todo ok).
        """
        # Colectar todos los provides disponibles
        available = set(self.ENGINE_CONTEXT_KEYS)
        declared_provides: set[str] = set()
        for node in graph.nodes.values():
            declared_provides.update(node.provides)
            for ss in node.sub_states:
                declared_provides.update(ss.provides)
        available.update(declared_provides)

        # Colectar todos los needs del grafo
        all_needs: set[str] = set()
        for node in graph.nodes.values():
            for ss in node.sub_states:
                all_needs.update(ss.needs)

        # Los context_updates de las edges tambien consumen contexto
        # (destinos NODE.SUB.field -> context.key). Contarlos como
        # consumidores de provides.
        edge_consumers: set[str] = set()
        for gate in graph.edges:
            for cu in gate.context_updates or []:
                to_field = (cu.get("to") or "").replace("context.", "")
                if to_field:
                    edge_consumers.add(to_field)
                from_path = cu.get("from") or ""
                parts = from_path.split(".")
                if len(parts) >= 3:
                    edge_consumers.add(parts[2])  # NODE.SUB.field -> field
        all_needs.update(edge_consumers)

        errors: list[str] = []

        # 1. Validar que cada need tenga un provider
        for node_id, node in graph.nodes.items():
            for ss in node.sub_states:
                if not ss.needs:
                    continue
                for need in ss.needs:
                    if need not in available:
                        errors.append(
                            f"[{node_id}.{ss.key}] needs '{need}' "
                            f"pero ningún provider lo produce"
                        )

        # 2. Validar que cada provides declarado tenga consumidor
        #    (GAP-1/GAP-2: un provides sin need que lo consuma es una fuga
        #    de datos entre fases — el contexto se produce pero se pierde).
        for node_id, node in graph.nodes.items():
            for provide in node.provides:
                if provide in self.ENGINE_CONTEXT_KEYS:
                    continue  # engine keys siempre consumibles
                if provide not in all_needs:
                    errors.append(
                        f"[{node_id}] provides '{provide}' pero ningún "
                        f"sub-estado lo consume (fuga de datos entre fases)"
                    )

        return errors

    @property
    def validation_errors(self) -> list[str]:
        return list(self._errors)
