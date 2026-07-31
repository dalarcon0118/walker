"""spring/context/providers.py — Providers de contexto del sprint (state).

Providers de negocio de spring (state). SE REGISTRAN EXTERNAMENTE
via register_providers() — el engine NO los conoce.

Ya contiene TaskProvider, SprintProvider, etc. Aquí se agrega StateProvider
(antes vivía en features/engine/context/providers.py).
"""
from __future__ import annotations
from typing import Any

from features.engine.context.models import ContextPiece
from features.engine.context.registry import ContextProvider
from features.engine.context.format import fmt_tokens, format_state_result


class StateProvider(ContextProvider):
    """Capa: estado del sprint actual. Layer: state.

    Delega en QueryManager para consultar sprint-state.json.
    """

    LAYER = "state"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: state — current workflow position, sprint goal, task inventory, project identity."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None
        filters = config.get("query", {})
        if isinstance(filters, str):
            filters = {}
        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = format_state_result(result)
        return ContextPiece(
            provider="state",
            name="sprint state",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=0,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers de spring en un ContextProviderRegistry."""
    registry.register("state", StateProvider())
