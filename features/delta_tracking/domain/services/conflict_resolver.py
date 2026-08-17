"""domain/services/conflict_resolver.py — Detección y resolución de conflictos.

SSOT: Los conflictos se derivan del estado actual (workflow + delta + trace).
No existe fuente de verdad paralela como .delta-conflicts.json.

Tipos de conflicto:
  - missing_required: campo requerido faltante en sub-estado
  - invalid_transition: transición no permitida
  - gate_blocked: gate bloqueada por validación externa
  - obsolete_spec: spec obsoleta o duplicada
  - spec_overlap: specs que se superponen
  - state_corruption: estado del workflow inconsistente

Estrategias de resolución:
  - skip: marca sub-estado como completado sin el dato (engine)
  - force: permite el submit con advertencia (engine)
  - edit: actualiza la spec en delta_tracking (delta)
  - merge: integra specs sin perder información (delta)
  - compensate: ejecuta compensación y continúa (engine)
"""

from __future__ import annotations

from datetime import datetime, timezone

from features.delta_tracking.domain.conflict import (
    Conflict,
    ResolutionStrategy,
)


class ConflictDetector:
    """Detecta conflictos del sprint/workflow (delta_tracking)."""

    @staticmethod
    def detect(
        workflow_state: dict,
        workflow_graph: dict,
        specs: list[dict],
        trace_events: list[dict],
    ) -> list[Conflict]:
        """Deriva conflictos del estado actual (sin archivos externos)."""
        conflicts: list[Conflict] = []
        current_node = workflow_state.get("current_node", "")
        current_sub_state = workflow_state.get("current_sub_state", "")
        sub_state_index = workflow_state.get("sub_state_index", 0)

        # 1. Detectar missing_required
        node = workflow_graph.get("nodes", {}).get(current_node, {})
        sub_states = node.get("sub_states", [])
        if sub_state_index < len(sub_states):
            ss = sub_states[sub_state_index]
            required_fields = ss.get("required_fields", [])
            sub_data = workflow_state.get("sub_state_data", {}).get(
                current_sub_state, {}
            )
            fields = sub_data.get("fields", {}) if isinstance(sub_data, dict) else {}
            for field in required_fields:
                if not fields.get(field):
                    conflicts.append(
                        Conflict(
                            type="missing_required",
                            node=current_node,
                            sub_state=current_sub_state,
                            message=f"Campo requerido '{field}' faltante en {current_node}.{current_sub_state}",
                            strategy="skip",
                        )
                    )

        # 2. Detectar obsolete_spec
        spec_ids = [s.get("spec_id", s.get("task_id", "")) for s in specs]
        seen = set()
        for sid in spec_ids:
            if sid in seen:
                conflicts.append(
                    Conflict(
                        type="obsolete_spec",
                        spec_id=sid,
                        message=f"Spec duplicada: {sid}",
                        strategy="merge",
                    )
                )
            seen.add(sid)

        # 3. Detectar spec_overlap (mismos archivos en specs diferentes)
        file_to_specs: dict[str, list[str]] = {}
        for s in specs:
            sid = s.get("spec_id", s.get("task_id", ""))
            files = s.get("impacted_artifacts", {}).get("files", []) or s.get(
                "files", []
            )
            for f in files:
                file_to_specs.setdefault(f, []).append(sid)
        for f, sids in file_to_specs.items():
            if len(sids) > 1:
                conflicts.append(
                    Conflict(
                        type="spec_overlap",
                        message=f"Archivo {f} compartido por specs: {', '.join(sids)}",
                        strategy="merge",
                    )
                )

        # 4. Detectar gate_blocked (trace sin kg_query para required_tools)
        node = workflow_graph.get("nodes", {}).get(current_node, {})
        sub_states = node.get("sub_states", [])
        if sub_state_index < len(sub_states):
            ss = sub_states[sub_state_index]
            required_tools = ss.get("required_tools", [])
            if required_tools:
                has_kg = any(ev.get("event_type") == "kg_query" for ev in trace_events)
                if not has_kg:
                    conflicts.append(
                        Conflict(
                            type="gate_blocked",
                            node=current_node,
                            sub_state=current_sub_state,
                            message="Gate bloqueada: required_tools sin evidencia de consulta (kg_query)",
                            strategy="force",
                        )
                    )

        # 5. Detectar state_corruption
        if workflow_state.get("context", {}).get("reconstructed", False):
            conflicts.append(
                Conflict(
                    type="state_corruption",
                    node=current_node,
                    sub_state=current_sub_state,
                    message="Estado reconstruido desde workflow-state.json — historial puede estar incompleto",
                    strategy="force",
                )
            )

        return conflicts


class ConflictResolver:
    """Resuelve conflictos del sprint/workflow (LEGACY).

    Ahora delega estrategias de workflow en WorkflowConflictResolver (engine).
    Mantiene edit/merge para specs en delta_tracking.
    """

    @staticmethod
    def detect(
        workflow_state: dict,
        workflow_graph: dict,
        specs: list[dict],
        trace_events: list[dict],
    ) -> list[Conflict]:
        """Deriva conflictos del estado actual (sin archivos externos)."""
        return ConflictDetector.detect(
            workflow_state, workflow_graph, specs, trace_events
        )

    @staticmethod
    def resolve(
        conflict: Conflict,
        strategy: str,
        payload: dict,
        workflow_state: dict,
        specs: list[dict],
    ) -> tuple[dict, list[dict]]:
        """Aplica resolución al conflicto.

        Estrategias de workflow (skip/force/compensate) se delegan en engine.
        Estrategias de specs (edit/merge) se resuelven aquí.
        """
        if strategy in ("edit", "merge"):
            return ConflictResolver._resolve_spec_strategy(
                conflict, strategy, payload, workflow_state, specs
            )
        # Workflow strategies: delegar a engine
        try:
            from core.engine.domain.services.conflict_resolver import (
                WorkflowConflictResolver,
            )
            from core.engine.domain.entities import WorkflowState, WorkflowGraph

            state = WorkflowState(
                current_node=workflow_state.get("current_node", ""),
                current_sub_state=workflow_state.get("current_sub_state", ""),
                sub_state_index=workflow_state.get("sub_state_index", 0),
                sub_state_data={},
                history=workflow_state.get("history", []),
                context=workflow_state.get("context", {}),
                version=workflow_state.get("version", "5.0"),
                updated_at=workflow_state.get("updated_at", ""),
                conflicts=workflow_state.get("conflicts", []),
            )
            graph = WorkflowGraph(
                nodes=workflow_graph.get("nodes", {}),
                edges=[],
                edges_by_source={},
                initial_state=workflow_graph.get("initial_state", ""),
                on_events=workflow_graph.get("on", {}),
            )
            from returns.pipeline import is_successful

            result = WorkflowConflictResolver.resolve(state, strategy, payload, graph)
            if is_successful(result):
                new_state = result.value_or(state)
                return new_state.to_dict() if hasattr(
                    new_state, "to_dict"
                ) else new_state, specs
            return workflow_state, specs
        except Exception:
            return workflow_state, specs

    @staticmethod
    def _resolve_spec_strategy(
        conflict: Conflict,
        strategy: str,
        payload: dict,
        workflow_state: dict,
        specs: list[dict],
    ) -> tuple[dict, list[dict]]:
        """Resuelve estrategias específicas de specs (edit/merge)."""
        now = datetime.now(timezone.utc).isoformat()
        resolution = ResolutionStrategy(
            strategy=strategy, payload=payload, applied_at=now
        )
        new_state = dict(workflow_state)
        new_specs = list(specs)

        if strategy == "edit":
            new_specs = ConflictResolver._resolve_edit(
                new_specs, conflict, payload, resolution
            )
        elif strategy == "merge":
            new_specs = ConflictResolver._resolve_merge(
                new_specs, conflict, payload, resolution
            )

        return new_state, new_specs

    @staticmethod
    def _resolve_edit(
        specs: list[dict],
        conflict: Conflict,
        payload: dict,
        resolution: ResolutionStrategy,
    ) -> list[dict]:
        for s in specs:
            sid = s.get("spec_id", s.get("task_id", ""))
            if sid == conflict.spec_id:
                s.update(payload)
                s["_edited"] = True
                s["_edited_at"] = resolution.applied_at
                break
        return specs

    @staticmethod
    def _resolve_merge(
        specs: list[dict],
        conflict: Conflict,
        payload: dict,
        resolution: ResolutionStrategy,
    ) -> list[dict]:
        target_id = payload.get("target_spec_id", "")
        source_id = conflict.spec_id or payload.get("source_spec_id", "")
        if not target_id or not source_id:
            return specs
        target = None
        source = None
        for s in specs:
            sid = s.get("spec_id", s.get("task_id", ""))
            if sid == target_id:
                target = s
            elif sid == source_id:
                source = s
        if target and source:
            merged = dict(source)
            merged.update(payload.get("changes", {}))
            merged["_merged_from"] = source_id
            merged["_merged_at"] = resolution.applied_at
            specs = [
                s for s in specs if s.get("spec_id", s.get("task_id", "")) != source_id
            ]
            specs.append(merged)
        return specs


__all__ = ["ConflictResolver", "Conflict", "ResolutionStrategy"]
