"""domain/entities.py — Entidades del dominio CPG.

Entidades del dominio de code_analysis: nodos y edges del Code Property Graph.
"""
from __future__ import annotations

from shared.libs.cognitive_graph.odm import Entity, fields


class CodeNode(Entity):
    """CPG: Code Property Graph — estructura del codigo fuente."""

    __index__ = "cpg"

    name: str = fields.String()
    type: str = fields.String()  # function, class, file, module
    file: str = fields.String()
    line: int = fields.Int()
