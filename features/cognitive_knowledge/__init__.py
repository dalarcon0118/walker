"""CKG — Code Knowledge Graph (desarrollo de software).

Construido sobre _walker/shared/knowledge_graph.py (grafo multi-dominio).
Define tipos y operaciones específicas del dominio de desarrollo.
"""
from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
from features.cognitive_knowledge.domain.knowledge_graph import KnowledgeNode as Node, KnowledgeEdge as Edge, KnowledgeGraph as Graph

__all__ = ["CodeKnowledgeGraph", "Node", "Edge", "Graph"]
