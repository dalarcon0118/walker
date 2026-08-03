"""
domain/services.py — Servicios de dominio puros para Workflow Navigation.

Contienen la lógica de negocio extraída de engine.py:
  - ValidationService: precondiciones, gates, datos requeridos
  - AdvanceService: avance automatico entre sub-estados, failure loops
  - TransitionService: aplicacion de transiciones de estado

PRINCIPIO: Son servicios PUROS. No hacen IO, no imprimen, no persisten.
           El caller (use case o infraestructura) decide que hacer con el resultado.
"""

from __future__ import annotations

import json

from .models import (
    WorkflowGraph,
    WorkflowState,
    SubState,
    ExecutionType,
    Gate,
)
from .errors import (
    NodeNotFoundError,
)


class ValidationService:
    """Validaciones de dominio del workflow.

    SRP: Unica responsabilidad = validar reglas de negocio del workflow.
    SSOT: Toda regla de validacion vive aqui.
    """

    @staticmethod
    def check_transition_preconditions(
        state: WorkflowState,
        graph: WorkflowGraph,
        gate_name: str,
    ) -> list[str]:
        """Valida precondiciones para permitir una transicion inter-nodo.

        Verifica que los sub-estados completados del nodo actual cumplan
        con sus contratos (required_fields y output_schema) antes de
        transicionar a otro nodo.

        PRINCIPIO: El workflow NO conoce el significado de los datos.
        Solo valida que los datos producidos cumplan los contratos que
        cada sub-estado declaro en su nodo (required_fields, output_schema).

        Returns:
            Lista de mensajes de error (vacia = todo ok).
        """
        errors: list[str] = []
        current_node_id = state.current_node
        node = graph.get_node(current_node_id)
        if not node:
            return [f"Nodo actual '{current_node_id}' no existe en el grafo"]

        # Validar TODOS los sub-estados completados (no solo MANUAL)
        # Incluye el sub-estado actual: estás por SALIR de él, por lo que
        # debe estar completo (sus required_fields llenos). Si el sub-estado
        # actual no tiene datos, la validacion falla (no se omite).
        current_idx = state.sub_state_index
        for i, ss in enumerate(node.sub_states):
            if i > current_idx:
                break  # Sub-estados futuros no se validan

            sub_data = state.get_sub_state_data(ss.key)

            # ── 1. Validar required_fields (cualquier execution type) ──
            if ss.required_fields:
                missing = [
                    f for f in ss.required_fields
                    if not sub_data.is_field_complete(f)
                ]
                if missing:
                    errors.append(
                        f"[{current_node_id}.{ss.key}] incomplete: "
                        f"falta {', '.join(missing)}"
                    )

            # ── 2. Validar output_schema ──────────────────────────────
            if ss.output_schema:
                schema = ss.output_schema
                schema_required = schema.get("required", [])

                # Intentar obtener el task_data estructurado guardado como JSON
                task_data_raw = sub_data.fields.get("task_data", "")
                task_fields = {}
                if task_data_raw and isinstance(task_data_raw, str):
                    try:
                        parsed = json.loads(task_data_raw)
                        if isinstance(parsed, dict):
                            task_fields = parsed
                    except json.JSONDecodeError:
                        pass
                elif isinstance(task_data_raw, dict):
                    task_fields = task_data_raw

                # Usar los campos reales del sub_data mezclados con task_fields
                # para tener la vision completa del output producido
                combined = {**sub_data.fields, **task_fields}

                if schema_required:
                    schema_missing = [
                        f for f in schema_required
                        if combined.get(f) is None or combined.get(f) == ""
                    ]
                    if schema_missing:
                        errors.append(
                            f"[{current_node_id}.{ss.key}] output_schema 'required' "
                            f"missing: {', '.join(schema_missing)}"
                        )

                # ── 3. Validar properties del schema ──────────────────
                props = schema.get("properties", {})
                for prop_name, prop_def in props.items():
                    # Solo validar si hay un valor presente
                    val = combined.get(prop_name)
                    if val is None:
                        continue  # Si no es required, no pasa nada
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
        """Valida que los campos requeridos del sub-estado esten presentes.

        Returns:
            Lista de errores (vacia = valido).
        """
        errors: list[str] = []
        for field in sub_state.required_fields:
            val = fields.get(field)
            if val is None or val == "":
                errors.append(f"campo requerido '{field}' faltante o vacio")
        return errors

    @staticmethod
    def is_node_complete(state: WorkflowState, graph: WorkflowGraph) -> bool:
        """Determina si todos los sub-estados del nodo actual estan completos."""
        node = graph.get_node(state.current_node)
        if not node:
            return True
        return node.is_complete(state.sub_state_index)

    @staticmethod
    def can_advance(state: WorkflowState, graph: WorkflowGraph) -> bool:
        """Hay un sub-estado siguiente al actual?"""
        node = graph.get_node(state.current_node)
        if not node:
            return False
        return state.sub_state_index < len(node.sub_states) - 1

    @staticmethod
    def is_sub_state_complete(state: WorkflowState, graph: WorkflowGraph,
                              sub_state_key: str) -> bool:
        """Determina si un sub-estado especifico tiene sus datos completos.

        Un sub-estado esta completo si:
          - no declara required_fields (no exige datos), O
          - todos sus required_fields estan presentes en sub_state_data
        """
        node = graph.get_node(state.current_node)
        if not node:
            return True
        ss = node.find_sub_state(sub_state_key)
        if not ss or not ss.required_fields:
            return True
        sub_data = state.get_sub_state_data(sub_state_key)
        return all(sub_data.is_field_complete(f) for f in ss.required_fields)

    @staticmethod
    def get_available_gates(
        state: WorkflowState,
        graph: WorkflowGraph,
    ) -> list[Gate]:
        """Obtiene las gates disponibles desde el nodo y sub-estado actual.

        Reglas (combinan mecanica + regla de negocio de validacion):
          - Una edge con source_sub_state (from_sub) es una gate de sub-estado:
            valida SOLO si estamos en ese sub-estado Y ese sub-estado esta
            COMPLETO (sus required_fields estan llenos). Esto garantiza que
            NO se pueda avanzar de fase sin haber completado la fase actual.
          - Una edge SIN source_sub_state es una gate entre nodos:
            valida solo cuando el nodo actual esta completo
            (todos sus sub-estados recorridos).
        """
        edges = graph.edges_by_source.get(state.current_node, [])
        available = []
        for g in edges:
            if g.source_sub_state:
                # Gate de sub-estado: solo desde ese sub-estado Y completo
                if g.source_sub_state == state.current_sub_state and \
                   ValidationService.is_sub_state_complete(state, graph,
                                                           state.current_sub_state):
                    available.append(g)
                continue
            # Gate del nodo (sin from_sub): válida si el sub-estado ACTUAL
            # está completo (required_fields vacíos o llenos).
            # NO exige nodo completo: en workflow.v3 las gates del nodo son
            # acciones disponibles desde el dashboard/sub-estado actual.
            if ValidationService.is_sub_state_complete(
                state, graph, state.current_sub_state
            ):
                available.append(g)
        return available

    # ── Teoría de grafos: clasificación de sub-estados ────────────────────

    @staticmethod
    def outgoing_edges(
        state: WorkflowState, graph: WorkflowGraph
    ) -> list[Gate]:
        """Aristas salientes del sub-estado actual (XOR-split del nodo).

        Devuelve las gates que parten del sub-estado actual, tanto las de
        sub-estado (source_sub_state == actual) como las del nodo
        (sin source_sub_state, disponibles desde cualquier sub-estado).
        """
        edges = graph.edges_by_source.get(state.current_node, [])
        result = []
        for g in edges:
            if g.source_sub_state and g.source_sub_state != state.current_sub_state:
                continue
            result.append(g)
        return result

    @staticmethod
    def classify_sub_state(
        state: WorkflowState, graph: WorkflowGraph
    ) -> str:
        """Clasifica el sub-estado actual segun su tipo en el grafo.

        Teoria de grafos de flujos de trabajo (van der Aalst / BPMN):
          - 'sequence': UNA arista saliente → auto-advance (on_success)
          - 'guarded':  VARIAS aristas, al menos una con guarda (contract.pre)
                        evaluable → se auto-dispara la unica verdadera
          - 'xor':      VARIAS aristas SIN guardas → decision del agente
                        (user_input) con campo de intent
        """
        outgoing = ValidationService.outgoing_edges(state, graph)
        if len(outgoing) <= 1:
            return "sequence"
        guarded = [g for g in outgoing if g.contract and g.contract.get("pre")]
        if guarded:
            # Si TODAS las salidas tienen guarda → guarded
            # Si hay una mezcla, las sin guarda son el default
            return "guarded" if len(guarded) == len(outgoing) else "xor"
        return "xor"

    @staticmethod
    def resolve_xor_by_intent(
        state: WorkflowState, graph: WorkflowGraph, intent: str
    ) -> Gate | None:
        """Resuelve la arista de un XOR-split por el intent del agente.

        El agente envia submit '{"intent":"<gate_name>"}' y el engine
        mapea el intent a la arista correspondiente y la dispara.
        """
        for g in ValidationService.outgoing_edges(state, graph):
            if g.name == intent:
                return g
        return None

    # ── Verificación estructural del workflow (teoría de grafos) ──────────

    @staticmethod
    def verify_graph(graph: WorkflowGraph) -> dict:
        """Valida la estructura del workflow como grafo dirigido.

        Teoria de grafos de flujos de trabajo:
          - Todo nodo debe tener al menos una salida (sin deadlocks)
          - Todo nodo debe poder alcanzar el estado final (reachability)
          - Los XOR-split deben poder resolverse (guarda o intent o 1 salida)
          - Los nodos no pueden ser huerfanos (sin entrada ni salida)

        Returns:
            {"valid": bool, "errors": [...], "warnings": [...]}
        """
        errors: list[str] = []
        warnings: list[str] = []
        node_ids = list(graph.nodes.keys())

        # ── 1. Nodos sin salida (deadlock) ──
        for nid in node_ids:
            outgoing = graph.edges_by_source.get(nid, [])
            if not outgoing:
                errors.append(
                    f"Nodo '{nid}' no tiene salidas (deadlock): el flujo "
                    f"quedaria atascado."
                )

        # ── 2. Nodos huerfanos (sin entrada) ──
        targets = {e.target_node for edges in graph.edges_by_source.values() for e in edges}
        for nid in node_ids:
            if nid != graph.initial_state and nid not in targets:
                warnings.append(
                    f"Nodo '{nid}' no es alcanzable (ningun edge lo apunta)."
                )

        # ── 3. Reachability: todo nodo puede alcanzar un estado final ──
        # Un estado final puede ser:
        #   - un nodo sin salidas (sink), O
        #   - un nodo cuyo ultimo sub-estado es terminal (on_success vacio
        #     y sin gates salientes desde ese sub-estado)
        def _is_terminal_node(nid: str) -> bool:
            """Un nodo es terminal si su ultimo sub-estado no avanza."""
            node = graph.get_node(nid)
            if node and node.sub_states:
                last = node.sub_states[-1]
                if not last.on_success:
                    return True
            return False

        def _can_reach_terminal(start: str, visited: set) -> bool:
            if start in visited:
                return False  # ciclo sin terminal
            visited.add(start)
            outgoing = graph.edges_by_source.get(start, [])
            if not outgoing:
                return True  # sink = terminal
            if _is_terminal_node(start):
                return True  # ultimo sub-estado terminal
            return any(_can_reach_terminal(e.target_node, set(visited)) for e in outgoing)

        for nid in node_ids:
            if not _can_reach_terminal(nid, set()):
                errors.append(
                    f"Nodo '{nid}' no puede alcanzar un estado final "
                    f"(todos sus caminos forman ciclos)."
                )

        # ── 4. XOR-split sin resolucion ──
        # Un nodo con varias salidas se resuelve por:
        #   a) guarda (contract.pre) en UNA arista -> auto cuando se cumple
        #   b) intent del agente (campo 'intent' en input_schema del sub-estado)
        # Si NO hay ninguna guarda Y ningun intent -> ambiguedad (warning).
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


class AdvanceService:
    """Logica de avance automatico entre sub-estados.

    SRP: Unica responsabilidad = mover el indice de sub-estado.
    No ejecuta scripts, no persiste, no imprime.
    """

    @staticmethod
    def advance(state: WorkflowState, graph: WorkflowGraph) -> WorkflowState | None:
        """Retorna nuevo estado avanzado al siguiente sub-estado.

        ELM pattern: no muta state, retorna nueva instancia.
        Retorna None si ya no hay mas sub-estados.
        """
        node = graph.get_node(state.current_node)
        if not node:
            return None

        next_idx = state.sub_state_index + 1
        if next_idx >= len(node.sub_states):
            return None

        next_ss = node.sub_states[next_idx]
        next_key = f"{state.current_node}.{next_ss.key}"
        return state.with_node(
            state.current_node, next_ss.key, next_idx
        ).add_history(next_key)

    @staticmethod
    def can_advance_to(
        state: WorkflowState, graph: WorkflowGraph, target_key: str
    ) -> bool:
        node = graph.get_node(state.current_node)
        if not node:
            return False
        target_idx = node.sub_state_index(target_key)
        if target_idx < 0:
            return False
        return target_idx == state.sub_state_index + 1

    @staticmethod
    def loop_back(
        state: WorkflowState,
        graph: WorkflowGraph,
        current_sub_key: str,
    ) -> WorkflowState | None:
        """Retorna nuevo estado retrocediendo al sub-estado anterior."""
        node = graph.get_node(state.current_node)
        if not node:
            return None

        prev_idx = state.sub_state_index - 1
        if prev_idx < 0:
            return None

        prev_ss = node.sub_states[prev_idx]
        prev_key = f"{state.current_node}.{prev_ss.key} (loop)"
        return state.with_node(
            state.current_node, prev_ss.key, prev_idx
        ).add_history(prev_key)

    @staticmethod
    def jump_to_sub_state(
        state: WorkflowState,
        graph: WorkflowGraph,
        target_key: str,
    ) -> WorkflowState | None:
        """Salta a un sub-estado especifico DENTRO del nodo actual.

        Usado por on_failure: cuando un sub-estado falla, el workflow
        retrocede/avanza al sub-estado indicado (si existe en el nodo).
        Ej: PLAN_APPROVAL.on_failure=PLANNING → salta a PLANNING.

        Retorna None si el target no existe en el nodo actual.
        """
        node = graph.get_node(state.current_node)
        if not node:
            return None
        target_idx = node.sub_state_index(target_key)
        if target_idx < 0:
            return None
        history_key = f"{state.current_node}.{target_key} (on_failure)"
        return state.with_node(
            state.current_node, target_key, target_idx
        ).add_history(history_key)


class TransitionService:
    """Aplicacion de transiciones de estado. INMUTABLE (ELM pattern)."""

    @staticmethod
    def apply(
        state: WorkflowState,
        graph: WorkflowGraph,
        target_node_id: str,
        target_sub_state: str | None = None,
    ) -> WorkflowState:
        """Retorna nuevo estado tras aplicar la transicion.

        No muta state — retorna nueva instancia.
        """
        target_node = graph.get_node(target_node_id)
        if not target_node:
            raise NodeNotFoundError(target_node_id)

        if target_sub_state:
            first_key = target_sub_state
        elif target_node.first_sub_state:
            first_key = target_node.first_sub_state.key
        else:
            first_key = "INIT"

        target_idx = target_node.sub_state_index(first_key)
        idx = target_idx if target_idx >= 0 else 0

        new_state = state.with_node(
            target_node_id, first_key, idx
        ).add_history(f"{target_node_id}.{first_key}")

        # ── Inicializar sub-estado destino (teoria de grafos) ──
        # Al entrar a un sub-estado nuevo, se inicializa su sub_state_data
        # con 'status: pending'. Esto satisface las post-condiciones de las
        # aristas (ej: 'ANALYZE.GATHER.status == pending') y representa
        # el ciclo de vida del nodo: pending -> activo -> completo.
        try:
            existing = new_state.get_sub_state_data(first_key)
            if not existing.fields:
                new_state = new_state.record_submit(
                    first_key, {"status": "pending"}
                )
        except Exception:
            pass

        return new_state
