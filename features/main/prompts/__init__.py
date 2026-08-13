"""features/prompts/ — Módulo centralizado de prompts.

Responsabilidades:
  - Cargar templates de prompts
  - Recolectar datos de contexto
  - Renderizar templates con datos

Uso:
    from features.main.prompts import PromptManager
    pm = PromptManager(chain_dir)
    prompt = pm.resolve("activity", {"node_id": "ANALYZE"})
"""
from __future__ import annotations

from .manager import PromptManager
from .loader import TemplateLoader
from .renderer import TemplateRenderer
from .collector import collect_all, collect_activity, collect_project_info, collect_available_tasks, collect_knowledge, collect_state
from .application.ports.prompt_manager import IPromptManager

__all__ = [
    "PromptManager", "IPromptManager", "TemplateLoader", "TemplateRenderer",
    "collect_all", "collect_activity", "collect_project_info",
    "collect_available_tasks", "collect_knowledge", "collect_state",
]
