"""delta_tracking/context/providers.py — Providers de contexto del Delta."""
from __future__ import annotations
from typing import Any

from features.engine.context.models import ContextPiece
from features.engine.context.registry import ContextProvider
from features.engine.context.format import fmt_tokens, format_delta_results


class DeltaProvider(ContextProvider):
    """Capa: DeltaGraph — contexto federado de tareas. Layer: delta."""

    LAYER = "delta"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: delta — cross-sprint task history (objectives, statuses, dependencies, file impact). Check for prior similar work."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        raw_query = config.get("query", {})
        if isinstance(raw_query, dict):
            filters = dict(raw_query)
        else:
            filters = {}

        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = format_delta_results(result)
        return ContextPiece(
            provider="delta",
            name="task history",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=40,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers de delta_tracking en un ContextProviderRegistry."""
    registry.register("delta", DeltaProvider())
