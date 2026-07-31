#!/usr/bin/env python3
"""prompt_engine.py — Motor de prompts (wrapper que importa del padre).

Importa todo de prompt_engine.py (padre) y solo redefine _render
con el path de templates correcto para _internal/.

Las funciones reales viven en prompt_engine.py para evitar duplicación.
"""
from __future__ import annotations
import os

from prompt_engine import *  # noqa: F401, F403

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_THIS_DIR, "templates")


def _render(template_name: str, **data) -> str:
    """Render a template file with substitutions."""
    from string import Template
    path = os.path.join(_TEMPLATES_DIR, template_name)
    if not os.path.exists(path):
        return f"{{Template '{template_name}' not found}}"
    with open(path) as f:
        tpl = Template(f.read())
    return tpl.safe_substitute(**data)
