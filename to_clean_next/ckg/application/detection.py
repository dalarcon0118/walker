"""DetectionService — análisis de integridad y calidad del CKG."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ckg.domain.knowledge_graph import KnowledgeGraph as CodeGraph
from ckg.domain.models import NodeType
from ckg.domain.errors import CkgError


def detect_issues(graph: CodeGraph, project_dir: Path) -> list[dict[str, Any]]:
    """Detecta problemas de integridad en el CKG.
    
    - Ciclos en el grafo
    - Nodos huérfanos (sin edges)
    - Violaciones de capas (BELONGS_TO a layer que no existe)
    - Nodos stale (archivo referenciado ya no existe)
    - Nodos sin tipo válido
    """
    issues: list[dict[str, Any]] = []
    
    # Integridad estructural
    integrity_errors = graph.validate_integrity()
    for err in integrity_errors:
        issues.append({"type": "integrity", "message": err})
    
    # Nodos stale
    for node_id, node in graph.nodes.items():
        if node.type == NodeType.FILE:
            candidate = project_dir / node_id
            if not candidate.exists():
                issues.append({
                    "type": "stale",
                    "node_id": node_id,
                    "message": f"Archivo ya no existe en disco: {node_id}",
                })
    
    # Violaciones de capas
    for edge in graph.edges:
        if edge.type.value == "BELONGS_TO":
            try:
                graph.get_node(edge.to_id)
            except CkgError:
                issues.append({
                    "type": "layer_violation",
                    "edge_from": edge.from_id,
                    "edge_to": edge.to_id,
                    "message": f"BELONGS_TO a capa inexistente: {edge.to_id}",
                })
    
    return issues


def detect_stale_nodes(graph: CodeGraph, project_dir: Path) -> list[dict[str, Any]]:
    """Fase DEPRECATE: solo nodos cuyos archivos ya no existen."""
    stale: list[dict[str, Any]] = []
    for node_id, node in graph.nodes.items():
        if node.type in (NodeType.FILE, NodeType.MODULE, NodeType.CLASS, NodeType.FUNCTION):
            if not (project_dir / node_id).exists():
                stale.append({
                    "node_id": node_id,
                    "label": node.label,
                    "type": node.type.value,
                    "message": f"Archivo no encontrado: {node_id}",
                })
    return stale
