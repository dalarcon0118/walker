"""
application/context_resolver.py — ContextResolver: engine-facing context resolution.

Roles:
  - Valida que los `needs` de un sub-estado tengan provider (engine, nodo, o global)
  - Promueve los `provides` de un nodo al global state (SprintState.context)
  - Inyecta contexto resuelto en ExecutionContext.baggage

NO confundir con ContextComposer (LLM-facing). Este resolver es para el ENGINE.

Flujo:
  1. Transición entre nodos → promote_provides() + validate_needs()
  2. Antes de step() → resolve_for_sub_state() → inyecta en baggage
  3. Después de submit() → check_auto_transition() si required_fields ok
"""

from __future__ import annotations

from typing import Set

from ..domain.models import (
    SprintState,
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

    SSOT: SprintState.context para global state.
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
        state: SprintState,
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

        # 2. Global state (SprintState.context)
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
                raise ContextResolutionError(key, node.node_id, sub_state.key)

        return context

    def promote_provides(
        self,
        state: SprintState,
        node: WorkflowNode,
    ) -> list[str]:
        """Promueve provides del nodo y sub-estados al global state.

        Busca las keys declaradas en provides (nodo + sub-estados)
        en TODOS los sub_state_data disponibles (no solo en el sub-estado
        que las declara). Esto cubre casos donde el dato fue guardado
        por auto-advance desde un sub-estado anterior.
        """
        all_keys = set(node.provides)
        for ss in node.sub_states:
            all_keys.update(ss.provides)

        pending = all_keys - set(state.context.keys())
        if not pending:
            return list(all_keys & set(state.context.keys()))

        # Buscar keys pendientes en TODOS los sub_state_data
        for ss_data in state.sub_state_data.values():
            if not ss_data:
                continue
            for key in pending & set(ss_data.fields.keys()):
                state.context[key] = ss_data.fields[key]
            pending -= set(ss_data.fields.keys())
            if not pending:
                break

        return list(all_keys & set(state.context.keys()))

    def apply_context_updates(
        self,
        state: SprintState,
        context_updates: list[dict] | None,
    ) -> list[str]:
        """Aplica context_updates de una edge al global state.

        Cada update: {"from": "NODE.SUB.field", "to": "context.field"}

        Returns:
            Lista de campos actualizados.
        """
        updated = []
        if not context_updates:
            return updated

        for update in context_updates:
            from_path = update.get("from", "")
            to_field = update.get("to", "").replace("context.", "")

            if not from_path or not to_field:
                continue

            # Parsear: NODE.SUB.field o SUB.field
            parts = from_path.split(".")
            if len(parts) >= 3:
                # NODE.SUB.field: intentar prefixed y unprefixed
                node_key, sub_key, field = parts[0], parts[1], parts[2]
                prefixed = f"{node_key}.{sub_key}"
                ss_data = state.sub_state_data.get(
                    prefixed
                ) or state.sub_state_data.get(sub_key)
                value = ss_data.fields.get(field, "") if ss_data else ""
            elif len(parts) == 2:
                # SUB.field
                sub_key, field = parts[0], parts[1]
                prefixed = f"{state.current_node}.{sub_key}"
                ss_data = state.sub_state_data.get(
                    prefixed
                ) or state.sub_state_data.get(sub_key)
                value = ss_data.fields.get(field, "") if ss_data else ""
            else:
                continue

            if value:
                state.context[to_field] = value
                updated.append(to_field)

        return updated

    # ── Validación en carga ─────────────────────────────────────────

    def validate_graph(self, graph: WorkflowGraph) -> list[str]:
        """Valida que todos los needs del grafo tengan provider.

        Colecta todos los provides (engine + nodos + sub-estados) y
        verifica que cada need tenga un match. Sin orden topológico,
        tolerante a ciclos en el grafo.

        Returns:
            Lista de errores de validación (vacía = todo ok).
        """
        # Colectar todos los provides disponibles
        available = set(self.ENGINE_CONTEXT_KEYS)
        for node in graph.nodes.values():
            available.update(node.provides)
            for ss in node.sub_states:
                available.update(ss.provides)

        # Validar que cada need tenga un provider
        errors: list[str] = []
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

        return errors

    @property
    def validation_errors(self) -> list[str]:
        return list(self._errors)
