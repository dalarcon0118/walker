"""entities.py — KnowledgeFact entity for CKG (index ckg).

Usage:
    from fast_mcp.features.cognitive_knowledge.domain.entities import KnowledgeFact

    fact = KnowledgeFact(
        type="decision",
        statement="Use JWT for auth",
        scope="security",
    )
"""
from __future__ import annotations

from fast_mcp.shared_kernel.cognitive_graph.odm import Entity, fields


class KnowledgeFact(Entity):
    """CKG: Knowledge Graph — decisions, conventions, patterns."""

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
