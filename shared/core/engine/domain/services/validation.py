"""domain/services/validation.py — ValidationService.

Validaciones de dominio del workflow.
SRP: Unica responsabilidad = validar reglas de negocio del workflow.
SSOT: Toda regla de validacion vive aqui.
"""

from __future__ import annotations

from shared.core.engine.domain.entities import (
    WorkflowGraph,
    WorkflowState,
    SubState,
    Gate,
)


class ValidationService:
    """Validaciones de dominio del workflow."""

    @staticmethod
    def check_transition_preconditions(
        state: WorkflowState,
        graph: WorkflowGraph,
        gate_name: str,
    ) -> list[str]:
        errors: list[str] = []
        current_node_id = state.current_node
        node = graph.get_node(current_node_id)
        if not node:
            return [f"Nodo actual '{current_node_id}' no existe en el grafo"]

        current_idx = state.sub_state_index
        for i, ss in enumerate(node.sub_states):
            if i > current_idx:
                break

            sub_data = state.get_sub_state_data(ss.key)

            if ss.required_fields:
                missing = [
                    f for f in ss.required_fields if not sub_data.is_field_complete(f)
                ]
                if missing:
                    errors.append(
                        f"[{current_node_id}.{ss.key}] incomplete: "
                        f"falta {', '.join(missing)}"
                    )

            if ss.output_schema:
                schema = ss.output_schema
                schema_required = schema.get("required", [])

                task_data_raw = sub_data.fields.get("task_data", "")
                task_fields = {}
                if task_data_raw and isinstance(task_data_raw, str):
                    try:
                        import json

                        parsed = json.loads(task_data_raw)
                        if isinstance(parsed, dict):
                            task_fields = parsed
                    except Exception:
                        pass
                elif isinstance(task_data_raw, dict):
                    task_fields = task_data_raw

                combined = {**sub_data.fields, **task_fields}

                if schema_required:
                    schema_missing = [
                        f
                        for f in schema_required
                        if combined.get(f) is None or combined.get(f) == ""
                    ]
                    if schema_missing:
                        errors.append(
                            f"[{current_node_id}.{ss.key}] output_schema 'required' "
                            f"missing: {', '.join(schema_missing)}"
                        )

                props = schema.get("properties", {})
                for prop_name, prop_def in props.items():
                    val = combined.get(prop_name)
                    if val is None:
                        continue
                    expected_type = prop_def.get("type", "")
                    if expected_type == "array" and not isinstance(val, list):
                        errors.append(
                            f"[{current_node_id}.{ss.key}] output_schema '{prop_name}' "
                            f"debe ser array, got {type(val).__name__}"
                        )
                    elif expected_type == "object" and not isinstance(val, dict):
                        errors.append(
                            f"[{current_node_id}.{ss.key}] output_schema '{prop_name}' "
                            f"debe ser object, got {type(val).__name__}"
                        )
                    elif expected_type == "string" and not isinstance(val, str):
                        errors.append(
                            f"[{current_node_id}.{ss.key}] output_schema '{prop_name}' "
                            f"debe ser string, got {type(val).__name__}"
                        )

        return errors

    @staticmethod
    def validate_sub_state_data(
        sub_state: SubState,
        fields: dict,
    ) -> list[str]:
        from shared.core.engine.domain.services.field_resolver import RequiredFieldResolver

        errors: list[str] = []
        required = RequiredFieldResolver.derive(sub_state)
        for field in required:
            val = fields.get(field)
            if val is None or val == "":
                errors.append(f"campo requerido '{field}' faltante o vacio")
        return errors

    @staticmethod
    def is_node_complete(state: WorkflowState, graph: WorkflowGraph) -> bool:
        node = graph.get_node(state.current_node)
        if not node:
            return True
        return node.is_complete(state.sub_state_index)

    @staticmethod
    def can_advance(state: WorkflowState, graph: WorkflowGraph) -> bool:
        node = graph.get_node(state.current_node)
        if not node:
            return False
        return state.sub_state_index < len(node.sub_states) - 1

    @staticmethod
    def is_sub_state_complete(
        state: WorkflowState, graph: WorkflowGraph, sub_state_key: str
    ) -> bool:
        node = graph.get_node(state.current_node)
        if not node:
            return True
        ss = node.find_sub_state(sub_state_key)
        if not ss:
            return True
        from shared.core.engine.domain.services.field_resolver import RequiredFieldResolver

        required = RequiredFieldResolver.derive(ss)
        if not required:
            return True
        sub_data = state.get_sub_state_data(sub_state_key)
        return all(sub_data.is_field_complete(f) for f in required)

    @staticmethod
    def get_available_gates(
        state: WorkflowState,
        graph: WorkflowGraph,
    ) -> list[Gate]:
        edges = graph.edges_by_source.get(state.current_node, [])
        available = []
        for g in edges:
            if g.source_sub_state:
                if (
                    g.source_sub_state == state.current_sub_state
                    and ValidationService.is_sub_state_complete(
                        state, graph, state.current_sub_state
                    )
                ):
                    available.append(g)
                continue
            if ValidationService.is_sub_state_complete(
                state, graph, state.current_sub_state
            ):
                available.append(g)
        return available

    @staticmethod
    def outgoing_edges(state: WorkflowState, graph: WorkflowGraph) -> list[Gate]:
        edges = graph.edges_by_source.get(state.current_node, [])
        result = []
        for g in edges:
            if g.source_sub_state and g.source_sub_state != state.current_sub_state:
                continue
            result.append(g)
        return result

    @staticmethod
    def classify_sub_state(state: WorkflowState, graph: WorkflowGraph) -> str:
        outgoing = ValidationService.outgoing_edges(state, graph)
        if len(outgoing) <= 1:
            return "sequence"
        guarded = [g for g in outgoing if g.contract and g.contract.get("pre")]
        if guarded:
            return "guarded" if len(guarded) == len(outgoing) else "xor"
        return "xor"

    @staticmethod
    def resolve_xor_by_intent(
        state: WorkflowState, graph: WorkflowGraph, intent: str
    ) -> Gate | None:
        for g in ValidationService.outgoing_edges(state, graph):
            if g.name == intent:
                return g
        return None

    @staticmethod
    def verify_graph(graph: WorkflowGraph) -> dict:
        errors: list[str] = []
        warnings: list[str] = []
        node_ids = list(graph.nodes.keys())

        for nid in node_ids:
            outgoing = graph.edges_by_source.get(nid, [])
            if not outgoing:
                errors.append(
                    f"Nodo '{nid}' no tiene salidas (deadlock): el flujo "
                    f"quedaria atascado."
                )

        targets = {
            e.target_node for edges in graph.edges_by_source.values() for e in edges
        }
        for nid in node_ids:
            if nid != graph.initial_state and nid not in targets:
                warnings.append(
                    f"Nodo '{nid}' no es alcanzable (ningun edge lo apunta)."
                )

        def _is_terminal_node(nid: str) -> bool:
            node = graph.get_node(nid)
            if node and node.sub_states:
                last = node.sub_states[-1]
                if not last.on_success:
                    return True
            return False

        def _can_reach_terminal(start: str, visited: set) -> bool:
            if start in visited:
                return False
            visited.add(start)
            outgoing = graph.edges_by_source.get(start, [])
            if not outgoing:
                return True
            if _is_terminal_node(start):
                return True
            return any(
                _can_reach_terminal(e.target_node, set(visited)) for e in outgoing
            )

        for nid in node_ids:
            if not _can_reach_terminal(nid, set()):
                errors.append(
                    f"Nodo '{nid}' no puede alcanzar un estado final "
                    f"(todos sus caminos forman ciclos)."
                )

        for nid in node_ids:
            outgoing = graph.edges_by_source.get(nid, [])
            if len(outgoing) <= 1:
                continue
            guarded = [e for e in outgoing if e.contract and e.contract.get("pre")]
            node = graph.get_node(nid)
            has_intent = False
            if node:
                for ss in node.sub_states:
                    sch = ss.input_schema or {}
                    props = sch.get("properties", {})
                    if "intent" in props:
                        has_intent = True
                        break
            if not guarded and not has_intent and nid != "REFINE":
                warnings.append(
                    f"Nodo '{nid}' es XOR-split ({len(outgoing)} salidas) sin "
                    f"guarda ni campo 'intent'. El agente no podria decidir la "
                    f"salida con submit."
                )

        return {"valid": not errors, "errors": errors, "warnings": warnings}


__all__ = ["ValidationService"]
