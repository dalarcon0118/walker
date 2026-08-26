"""
application/workflow_map.py — Composición dinámica del mapa del workflow.

PROPÓSITO: construir un mapa consumible del workflow COMPLETO (todos los
nodos, sub-estados, edges/gates) de forma DINÁMICA desde el WorkflowGraph
(SSOT: data/workflow del motor). El agente consume el mapa vía walker_help
para responder preguntas estructurales:

  1. ¿Cuál es el mapa general de todo el workflow?
  2. ¿Existe algún camino para crear tareas SIN asociarlas a un sprint?
  3. ¿Existe un mecanismo para no perder tareas fuera del sprint?

El mapa NUNCA se hardcodea: se deriva del grafo cargado. Si el workflow
cambia (nuevos nodos, edges o gates), el mapa refleja el cambio.

SRP: Única responsabilidad = componer el mapa serializable del workflow.
Puras: no mutan el grafo, no persisten.
"""

from __future__ import annotations

from typing import Any

from ..domain.graph_analyzer import GraphAnalyzer


def _sub_state_to_dict(ss: Any) -> dict:
    """Serializa un sub-estado a dict consumible (compacto)."""
    execution = ss.execution.value if ss.execution else "manual"
    # PRINCIPIO (schema como autoridad): derivar required_fields desde el
    # JSON Schema, no usar el campo legacy vacio (GAP-3/GAP-4).
    from ..domain.services import RequiredFieldResolver

    required = RequiredFieldResolver.derive(ss)
    return {
        "key": ss.key,
        "label": ss.label or ss.key,
        "execution": execution,
        "action": ss.action or "",
        "required_fields": required,
        "on_success": ss.on_success or "",
    }


def _node_to_dict(node: Any) -> dict:
    """Serializa un nodo con sus sub-estados."""
    return {
        "id": node.node_id,
        "label": node.label or node.node_id,
        "description": node.description or "",
        "provides": list(node.provides),
        "sub_states": [_sub_state_to_dict(ss) for ss in node.sub_states],
    }


def _gate_to_dict(gate: Any) -> dict:
    """Serializa una edge/gate a dict consumible."""
    return {
        "from": gate.source_node,
        "to": gate.target_node,
        "gate": gate.name,
        "sub_state_gate": gate.target_sub_state or "",
        "description": gate.description or "",
    }


def _analyze_off_sprint_paths(graph: Any) -> dict:
    """Analiza si existe camino para crear tareas fuera de un sprint.

    Reglas (derivadas del grafo, no hardcodeadas):
      - Los nodos capaces de CREAR tareas son aquellos con sub-estados
        cuya action ∈ {plan_sprint, spec_task} (creación formal).
      - Si todos esos nodos son alcanzables SOLO pasando por SPRINT,
        entonces NO existe camino formal fuera del sprint.
      - Independientemente, existe el mecanismo informal anti-pérdida:
        capture_task → sprint informal session-YYYYMMDD (registrado en
        el DeltaGraph, indexado como histórico). Este es el mecanismo
        para no perder trabajo implementado fuera del workflow.

    Returns:
        dict con:
          can_create_formal_outside_sprint: bool
          nodes_that_create_tasks: list[str]
          requires_sprint: bool
          mechanism: dict con el mecanismo anti-pérdida documentado
    """
    task_creator_actions = {"plan_sprint", "spec_task"}
    creator_nodes: list[str] = []
    for node_id, node in graph.nodes.items():
        for ss in node.sub_states:
            if (ss.action or "") in task_creator_actions:
                creator_nodes.append(node_id)
                break

    # ¿Algun nodo creador es alcanzable desde START sin pasar por SPRINT?
    analyzer = GraphAnalyzer(graph)
    requires_sprint = True
    for creator in creator_nodes:
        if creator == "SPRINT":
            continue
        # Alcanzable sin SPRINT: revisar si existe path que no contenga SPRINT
        ok, _, path = analyzer.reachability(graph.initial_state, creator)
        if ok and "SPRINT" not in path:
            requires_sprint = False
            break

    return {
        "can_create_formal_outside_sprint": not requires_sprint,
        "nodes_that_create_tasks": sorted(creator_nodes),
        "requires_sprint": requires_sprint,
        "mechanism": {
            "task": "capture_task",
            "description": (
                "Mecanismo anti-pérdida para tareas fuera del sprint: registra "
                "el trabajo en el sprint informal de la fecha (session-YYYYMMDD), "
                "indexado en el DeltaGraph como histórico consultable."
            ),
            "usage": (
                "walker <project>/capture_task "
                '\'{"objective":"...","domain":"...","files":["..."]}\''
            ),
        },
    }


def compose_workflow_map(chain_dir: str = "") -> dict:
    """Compone el mapa completo del workflow desde el grafo.

    Args:
        chain_dir: directorio del sprint para cargar el workflow del
            proyecto. Vacío → carga la plantilla canónica del motor.

    Returns:
        dict serializable con:
          initial_state, nodes[], edges[], analysis{cycles, terminals,
          off_sprint}
    """
    from ..infrastructure.repositories import WorkflowRepository

    repo = WorkflowRepository()
    graph = repo.load()

    nodes = [_node_to_dict(n) for n in graph.nodes.values()]
    edges = [_gate_to_dict(g) for g in graph.edges]

    analyzer = GraphAnalyzer(graph)
    cycles = analyzer.cycles()
    terminals = analyzer.terminal_nodes()

    return {
        "initial_state": graph.initial_state or "",
        "nodes": nodes,
        "edges": edges,
        "analysis": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "cycles": cycles,
            "cycles_count": len(cycles),
            "terminal_nodes": terminals,
            "off_sprint": _analyze_off_sprint_paths(graph),
        },
    }
