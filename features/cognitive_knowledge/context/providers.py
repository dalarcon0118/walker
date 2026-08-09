"""cognitive_knowledge/context/providers.py — Providers de contexto del CKG."""
from __future__ import annotations
from typing import Any

from libs.context import ContextPiece, ContextProvider, fmt_tokens, format_ckg_results


class CKGProvider(ContextProvider):
    """Capa: Cognitive Knowledge Graph — decisiones, patrones, convenciones."""

    LAYER = "ckg"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: ckg — knowledge graph of decisions, conventions, patterns."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        task_id = getattr(ctx, "task_id", "")
        raw_query = config.get("query", {})
        max_nodes = config.get("max_nodes", 20)

        if isinstance(raw_query, dict):
            filters = dict(raw_query)
        elif isinstance(raw_query, str):
            filters = {}
            q = raw_query.replace("{task_id}", task_id)
            if q:
                filters = {"text": q}
        else:
            filters = {}

        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = format_ckg_results(result, max_nodes)
        return ContextPiece(
            provider="ckg",
            name=f"ckg: {filters}",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=30,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers de cognitive_knowledge en un ContextProviderRegistry."""
    registry.register("ckg", CKGProvider())
