"""entities.py — Entidad KnowledgeFact para CKG (indice ckg).

Uso:
    from features.cognitive_knowledge.entities import KnowledgeFact

    KnowledgeFact.bind(cg)

    # Crear
    fact = KnowledgeFact(
        type="decision",
        statement="Usar JWT para auth",
        scope="security",
    )
    fact.save()

    # Buscar
    facts = KnowledgeFact.search("JWT")
    facts = KnowledgeFact.find_by(type="decision")
"""

from __future__ import annotations
from shared.libs.cognitive_graph.odm import Entity, fields


class KnowledgeFact(Entity):
    """CKG: Knowledge Graph — decisiones, convenciones, patrones."""

    __index__ = "ckg"
    __search__ = ["statement", "rationale"]

    type: str = fields.String()
    subject: str = fields.String()
    statement: str = fields.Text(fulltext=True)
    rationale: str = fields.Text(fulltext=True)
    scope: str = fields.String()
    tags: list[str] = fields.List()
    metadata: dict = fields.Dict()
    contains: list[str] = fields.List()
