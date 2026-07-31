"""entities.py — Entidad CodeNode para CPG (indice cpg).

Uso:
    from features.code_analysis.entities import CodeNode

    CodeNode.bind(cg)

    # Buscar
    functions = CodeNode.find_by(file="src/auth.py")
    func = CodeNode.find_by(name="validate_token", type="function")
"""

from __future__ import annotations
from libs.cognitive_graph.odm import Entity, fields


class CodeNode(Entity):
    """CPG: Code Property Graph — estructura del codigo fuente."""

    __index__ = "cpg"

    name: str = fields.String()
    type: str = fields.String()  # function, class, file, module
    file: str = fields.String()
    line: int = fields.Int()
