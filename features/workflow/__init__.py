"""features/workflow — Bounded context del workflow del walker (ADR-068).

Owns la semántica host del workflow que envuelve el engine anémico:
  - prompts (builders, loader, collector, gate_briefing) — IPromptCollector.
  - gates host-side (uncaptured_work).
"""
from __future__ import annotations

from features.workflow.application.prompts.manager import PromptManager
from features.workflow.application.prompts.loader import TemplateLoader
from features.workflow.application.prompts.renderer import TemplateRenderer

__all__ = ["PromptManager", "TemplateLoader", "TemplateRenderer"]