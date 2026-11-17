"""shared_kernel/skills — índice de skills del entorno (ver `index.py`)."""

from fast_mcp.shared_kernel.skills.index import (
    MAX_CATALOG_CHARS,
    MAX_CATALOG_ITEMS,
    MAX_SKILL_BODY,
    Skill,
    discover,
    format_catalog,
    read_skill,
    score,
    search,
    skills_roots,
)

__all__ = [
    "MAX_CATALOG_CHARS",
    "MAX_CATALOG_ITEMS",
    "MAX_SKILL_BODY",
    "Skill",
    "discover",
    "format_catalog",
    "read_skill",
    "score",
    "search",
    "skills_roots",
]
