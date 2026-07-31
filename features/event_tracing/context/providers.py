"""event_tracing/context/providers.py — Providers de contexto del Trace."""
from __future__ import annotations
from typing import Any

from features.engine.context.models import ContextPiece
from features.engine.context.registry import ContextProvider
from features.engine.context.format import fmt_tokens, format_trace_results


class TraceProvider(ContextProvider):
    """Capa: TraceGraph — historial de interacciones. Layer: trace."""

    LAYER = "trace"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: trace — session history (tool calls, messages, file changes, state transitions). Use to detect failure patterns."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        task_id = getattr(ctx, "task_id", "")
        raw_query = config.get("query", {})
        max_events = config.get("max_events", 8)

        if isinstance(raw_query, dict):
            filters = dict(raw_query)
        elif isinstance(raw_query, str):
            filters = {}
            q = raw_query.replace("{task_id}", task_id)
            if q:
                filters = {"task": q}
        else:
            filters = {}

        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = format_trace_results(result, max_events)
        return ContextPiece(
            provider="trace",
            name="session history",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=20,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers de event_tracing en un ContextProviderRegistry."""
    registry.register("trace", TraceProvider())
