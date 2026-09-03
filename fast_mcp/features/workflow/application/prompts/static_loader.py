"""static_loader.py — Carga de templates estáticos (rutas canónicas SSOT).

Restaurado desde el motor heredado (mcp/features/workflow/application/prompts/
static_loader.py, eliminado por el commit 63efa79). Módulo HOJA (sin imports
del paquete prompts): `canonical_templates_dir`, `load_static` y
`canonical_nodes_dir` son helpers puros de paths/lectura de templates.

SSOT de todos los prompts servidos por el feature workflow:
  - nodes/<node>/<prompt>.prompt.json  → prompts del workflow del motor
  - protocol/*.md                      → protocolos (MCP / REST)
  - gates/*.md                         → gate briefings
No depende de proyecto ni de chain_dir.
"""
from __future__ import annotations

import logging
import os

from ...domain.errors import TemplateNotFoundError

logger = logging.getLogger(__name__)


def canonical_templates_dir() -> str:
    """ROOT ÚNICO de templates: <pkg>/templates/."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def canonical_nodes_dir() -> str:
    """Subpath de prompts de nodo del motor: templates/nodes/."""
    return os.path.join(canonical_templates_dir(), "nodes")


def load_static(template_id: str) -> str:
    """Carga un template SOLO desde el root canónico (templates/)."""
    root = canonical_templates_dir()
    candidates = (
        os.path.join(root, template_id),
        os.path.join(root, f"{template_id}.md"),
        os.path.join(root, f"{template_id}.txt"),
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError as e:
                logger.debug(
                    "template %s ilegible (%s); probando siguiente", path, e
                )
    raise TemplateNotFoundError(template_id, f"Template '{template_id}' no encontrado en {root}")
