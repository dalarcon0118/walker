"""application/ports/prompt_manager.py — Puerto del servicio de prompts.

Puerto de salida (driven port) en Clean Architecture. Define el contrato
que el PromptManager implementa. La capa de aplicacion del engine depende
de esta abstraccion, no de la implementacion concreta de features.prompts.

ADDR-WALKER-043 / arquitectura composite: los module services (como prompts)
se inyectan por clave logica (register_service/get_service) en vez de por
import directo feature->feature. Los consumidores resuelven IPromptManager
y NUNCA importan features.prompts.manager.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IPromptManager(ABC):
    """Contrato del servicio de prompts (resolve/compose/collect + meta)."""

    @abstractmethod
    def resolve(self, template_id: str, context: dict | None = None, extra_data: dict | None = None) -> str:
        """Renderiza un template con contexto recolectado (conserta al LLM)."""
        ...

    @abstractmethod
    def resolve_with_meta(self, template_id: str, context: dict | None = None, extra_data: dict | None = None) -> dict[str, Any]:
        """Renderiza y devuelve template + data (+ input_schema/usage si aplica)."""
        ...

    @abstractmethod
    def compose(
        self,
        node_config: dict,
        prompt_template: str,
        ctx: Any,
        chain_dir: str | None = None,
        extra_data: dict | None = None,
    ) -> str:
        """Compone el prompt via context providers (ContextComposer) + render."""
        ...

    @abstractmethod
    def available_templates(self) -> list[str]:
        """Lista los templates canonicos de features/prompts/templates/."""
        ...