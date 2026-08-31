"""MigrationService — migración desde facts.json a nodos CKG."""
from __future__ import annotations

from typing import Any

from features.cognitive_knowledge.domain.knowledge_graph import KnowledgeGraph as CodeGraph, KnowledgeNode as Node, KnowledgeEdge as Edge
from features.cognitive_knowledge.domain.models import NodeType
from features.cognitive_knowledge.domain.errors import CkgError


def migrate_from_facts(graph: CodeGraph, facts: list[dict[str, Any]]) -> int:
    """Transforma facts.json en nodos CKG. Retorna número de nodos creados."""
    count = 0
    type_map = {
        "convention": NodeType.CONVENTION,
        "pattern": NodeType.PATTERN,
        "decision": NodeType.DECISION,
        "arquitectura": NodeType.DECISION,
        "component": NodeType.COMPONENT,
        "layer": NodeType.LAYER,
        "quality": NodeType.QUALITY,
    }
    
    for fact in facts:
        fact_type = str(fact.get("type", "convention")).lower()
        node_type = type_map.get(fact_type, NodeType.CONVENTION)
        
        name = fact.get("name") or fact.get("id") or str(count)
        node_id = f"{node_type.value}-{name}"
        
        node = Node(
            id=node_id,
            type=node_type,
            label=str(fact.get("title", fact.get("name", fact_type))),
            description=str(fact.get("description", fact.get("que_hace", ""))),
            metadata={k: v for k, v in fact.items()
                      if k not in ("type", "name", "id", "title", "description", "que_hace", "que_hacia")},
        )
        try:
            graph.add_node(node)
            count += 1
        except CkgError:
            continue
        
        # Crear edges por dependencias declaradas
        for dep in fact.get("depends_on", []) or []:
            target = dep if isinstance(dep, str) else dep.get("id", dep.get("target", ""))
            if target and graph.has_node(target):
                graph.add_edge(Edge(from_id=node.id, to_id=target))
    
    return count
