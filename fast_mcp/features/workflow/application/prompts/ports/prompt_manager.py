"""application/ports/prompt_manager.py — Puerto del servicio de prompts.

Puerto de salida (driven port) en Clean Architecture. Define el contrato
que el PromptManager implementa. La capa de aplicación del engine depende
de esta abstracción, no de la implementación concreta de features.prompts.

Restaurado desde el motor heredado y adaptado a fast_mcp: se añade
`resolve_for_sub_state` para expresar la carga dinámica por tipo de nodo:
un sub-estado con `execution == LLM_TASK` tiene un prompt asociado a la
tarea que se resuelve desde templates/nodes/<nodo>/<sub>.prompt.json.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IPromptManager(ABC):
    """Contrato del servicio de prompts (resolve/compose/collect + meta)."""

    @abstractmethod
    def resolve(
        self,
        template_id: str,
        context: dict | None = None,
        extra_data: dict | None = None,
    ) -> str:
        """Renderiza un template con contexto (conserta al LLM)."""
        ...

    @abstractmethod
    def resolve_with_meta(
        self,
        template_id: str,
        context: dict | None = None,
        extra_data: dict | None = None,
    ) -> dict[str, Any]:
        """Renderiza y devuelve template + data (+ input_schema/usage si aplica)."""
        ...

    @abstractmethod
    def resolve_for_sub_state(
        self,
        node_id: str,
        sub_state_key: str,
        execution: str,
        fields: dict | None = None,
        extra_data: dict | None = None,
    ) -> dict[str, Any]:
        """Resuelve el prompt del sub-estado actual (carga dinámica por nodo).

        Si `execution == "llm_task"`, usa el prompt/prompt_id del sub-estado o
        el template por convención nodes/<nodo>/<sub_state_key>. Devuelve
        {"template_id", "template", "data", "type"} o {"error": ...}.
        """
        ...

    @abstractmethod
    def available_templates(self) -> list[str]:
        """Lista los templates canónicos de prompts/templates/."""
        ...
