"""CKG — SHIM de compatibilidad.

Re-exporta todo desde features.cognitive_knowledge para mantener
backward compatibility con ~10 imports existentes.
"""
from __future__ import annotations
import sys

from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
from features.cognitive_knowledge.domain.models import NodeType, EdgeType
from features.cognitive_knowledge.domain.knowledge_graph import (
    KnowledgeNode as Node,
    KnowledgeEdge as Edge,
    KnowledgeGraph as Graph,
)
from features.cognitive_knowledge import domain, application, infrastructure

# Deep import forwarding via sys.modules
sys.modules[f"{__name__}.domain"] = domain
sys.modules[f"{__name__}.application"] = application
sys.modules[f"{__name__}.infrastructure"] = infrastructure
