"""application/workflow_loader.py — Carga y conversión de WorkflowGraph.

SRP: Responsabilidad única = cargar workflow desde disco y convertirlo
a representaciones internas (WorkflowGraph, dict).
"""
from __future__ import annotations

from shared.core.engine.engine import load_workflow
from shared.core.engine.domain.entities import WorkflowGraph


def find_workflow(chain_dir: str, workflow_repo: 'WorkflowRepository | None' = None) -> dict:
    """Carga workflow como dict. ERROR si no hay chain_dir o no existe workflow."""
    if not chain_dir:
        raise ValueError("chain_dir vacío — no se puede resolver el workflow")
    repo = workflow_repo or WorkflowRepository()
    graph = load_workflow(repo)
    return _graph_to_dict(graph)


def _graph_to_dict(graph: WorkflowGraph) -> dict:
    """Convierte WorkflowGraph a dict para serialización/output."""
    nodes = {}
    for nid, node in graph.nodes.items():
        sub_states = []
        for ss in node.sub_states:
            entry: dict[str, object] = {
                "key": ss.key,
                "label": ss.label or ss.key,
                "execution": ss.execution.value if ss.execution else "manual",
                "required_fields": list(ss.required_fields),
                "on_entry_message": ss.on_entry_message or "",
                "input_schema": ss.input_schema or {},
                "output_schema": ss.output_schema or {},
            }
            if ss.action:
                entry["action"] = ss.action
            if ss.prompt_id:
                entry["prompt_id"] = ss.prompt_id
            if ss.auto_complete_script:
                entry["auto_complete"] = ss.auto_complete_script
            sub_states.append(entry)

        gates = [
            {
                "name": g.name,
                "target": g.target_node,
                "target_sub_state": g.target_sub_state,
                "description": g.description,
            }
            for g in graph.edges_by_source.get(nid, [])
        ]
        nodes[nid] = {
            "label": node.label or nid,
            "description": node.description or "",
            "sub_states": sub_states,
            "gates": gates,
            "entry_action": node.entry_action_script or "",
            "on_entry_message": node.on_entry_message or "",
        }

    edges = [
        {
            "from": g.source_node,
            "to": g.target_node,
            "gate": g.name,
            "target_sub_state": g.target_sub_state,
            "description": g.description,
        }
        for g in graph.edges
    ]

    return {
        "version": "4.0",
        "initial_state": graph.initial_state or "SPRINT",
        "nodes": nodes,
        "edges": edges,
        "on": dict(graph.on_events),
    }
