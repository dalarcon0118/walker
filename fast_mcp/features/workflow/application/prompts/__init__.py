"""prompts — Subsistema de prompts del workflow (carga dinámica por nodo).

Restaurado desde el motor heredado (mcp/features/workflow/application/prompts/,
eliminado por el commit 63efa79) y re-homed en fast_mcp.

Mecanismo restaurado (el que el usuario reportó como funcional):
> los prompts se cargaban dinámicamente según el tipo de nodo; si era un
> task LLM había un prompt asociado a la tarea.

SSOT de prompts: templates/nodes/<nodo>/<fase>.prompt.json (más
protocol/*.md y gates/*.md).

Componentes:
  - static_loader / loader  → carga de templates (SSOT + proyecto).
  - renderer                → sustitución de placeholders (puro).
  - manager (PromptManager) → orquesta loader + renderer, implementa
                              IPromptManager (ports/prompt_manager.py) e
                              incluye resolve_for_sub_state (carga dinámica
                              por execution==llm_task).
  - collector               → recolecta datos CKG/workflow/sprint para
                              inyectar en templates (enriquecimiento).
  - evidence                → motor de evidencia + meta-instruction para
                              USER GATES (formato de bloques de evidencia).
  - gate_briefing           → briefing estándar de USER GATES (qué sucedió
                              / por qué / opciones).
  - protocol_builder        → genera el protocolo MCP (estático en fast_mcp).
"""
from __future__ import annotations

from .manager import PromptManager
from .ports.prompt_manager import IPromptManager

__all__ = [
    "PromptManager",
    "IPromptManager",
    "collector",
    "evidence",
    "gate_briefing",
    "protocol_builder",
]
