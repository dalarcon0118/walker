"""domain/ — CKG: conocimiento de desarrollo de software.

Usa _walker/shared/knowledge_graph.py como infraestructura base.
Define tipos de nodo y edge específicos del dominio de desarrollo.
"""
from __future__ import annotations

# Constantes de tipos para el dominio de desarrollo
# No son enum — el grafo base acepta cualquier string
# Esto permite que otros dominios usen sus propios tipos

class NodeType:
    """Tipos de nodo del dominio desarrollo."""
    COMPONENT = "component"
    PATTERN = "pattern"
    LAYER = "layer"
    CONVENTION = "convention"
    DECISION = "decision"
    QUALITY = "quality"
    DATA_STRUCTURE = "dataStructure"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    FILE = "file"
    EVENT = "event"
    API = "api"
    REFACTOR = "refactor"
    RULE = "rule"
    WARNING = "warning"
    METRIC = "metric"
    ARTEFACTO = "artefacto"
    REGLA = "regla"
    ADVERTENCIA = "advertencia"
    FUERA_SCOPE = "fuera_scope"


class EdgeType:
    """Tipos de relación del dominio desarrollo."""
    BELONGS_TO = "BELONGS_TO"
    IMPLEMENTS = "IMPLEMENTS"
    DEPENDS_ON = "DEPENDS_ON"
    FOLLOWS = "FOLLOWS"
    DECIDED_IN = "DECIDED_IN"
    HAS_QUALITY = "HAS_QUALITY"
    SIMILAR_TO = "SIMILAR_TO"
    SUPERSEDES = "SUPERSEDES"
    PART_OF = "PART_OF"
    EXPORTS = "EXPORTS"
    CONSUMES = "CONSUMES"
    HANDLED_BY = "HANDLED_BY"
    TRIGGERS = "TRIGGERS"
    DEFINED_BY = "DEFINED_BY"
    RELATED_TO = "RELATED_TO"
    EXTENDS = "EXTENDS"
