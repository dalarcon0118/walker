"""ContextService — extracción de subgrafos relevantes para el LLM."""
from __future__ import annotations

from ckg.domain.knowledge_graph import KnowledgeGraph as CodeGraph


def context_for_files(graph: CodeGraph, files: list[str], depth: int = 2) -> str:
    """BFS desde archivos con profundidad depth. Retorna subgrafo formateado para LLM."""
    reachable: set[str] = set(files)
    queue = list(files)
    visited = set(files)
    current_depth = 0
    while queue and current_depth < depth:
        next_queue: list[str] = []
        for node_id in queue:
            for target in graph.neighbors(node_id):
                if target not in visited:
                    visited.add(target)
                    reachable.add(target)
                    next_queue.append(target)
        queue = next_queue
        current_depth += 1

    lines = [f"Contexto para {len(files)} archivo(s) (depth={depth}):"]
    for node_id in sorted(reachable):
        try:
            node = graph.get_node(node_id)
            lines.append(f"  {node_id}: {node.label or node.type.value}")
        except Exception:
            continue
    return "\n".join(lines)


def context_for_task(graph: CodeGraph, task_id: str, task_files: list[str]) -> str:
    """Wrapper para contexto por tarea."""
    return context_for_files(graph, task_files, depth=2)
