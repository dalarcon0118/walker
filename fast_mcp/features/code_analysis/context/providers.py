"""code_analysis/context/providers.py — Providers de contexto del CPG.

Providers de negocio de code_analysis. SE REGISTRAN EXTERNAMENTE
(ver features/engine/bootstrap.build_context_registry) y el engine
NO los conoce: solo los consume si el nodo los declara en context.sources.

Return pattern: ante fallo los providers devuelven None con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

from fast_mcp.shared_kernel.context_format import format_cpg_results
from fast_mcp.shared_kernel.common_context import ContextPiece, ContextProvider, fmt_tokens


class CPGProvider(ContextProvider):
    """Capa: Code Property Graph — código fuente estructurado. Layer: cpg."""

    LAYER = "cpg"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: cpg — structured source code (functions, classes, calls, imports). REPLACES raw file reading."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        task_id = getattr(ctx, "task_id", "")
        raw_query = config.get("query", {})
        max_results = config.get("max_results", 30)

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

        content = format_cpg_results(result, max_results)
        return ContextPiece(
            provider="cpg",
            name=f"cpg: {filters}",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=30,
        )


class BusinessProvider(ContextProvider):
    """Capa: business — capa de negocio derivada del CPG.

    Entrega funciones puras reutilizables, flujos de negocio (cadenas
    CALLS completas) y patrones arquitectonicos (TEA, hooks, redux).
    Reduce alucinacion: dice al LLM QUÉ reutilizar y QUÉ patrón seguir.
    """

    LAYER = "business"

    def instructions(self, config: dict, ctx: Any) -> str:
        return (
            "Layer: business — business layer derived from the CPG. Contains "
            "PURE FUNCTIONS (reusable helpers without side-effects), "
            "BUSINESS FLOWS (complete call chains = use cases), and "
            "ARCHITECTURE PATTERNS (TEA, hooks, redux, pure domain). "
            "REUSE these functions and follow these patterns instead of "
            "inventing new ones."
        )

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None
        chain_dir = getattr(query_manager, "chain_dir", "")
        if not chain_dir:
            return None

        focus = config.get("file", "") or config.get("focus", "")
        max_items = config.get("max_items", 20)
        include_flows = config.get("include_flows", True)

        try:
            from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase
            from fast_mcp.shared_kernel.project_support import ProjectService

            paths = ProjectService.resolve_paths(chain_dir)
            q = QueryGraphUseCase(
                chain_dir=chain_dir,
                project_id=paths.project_id,
                data_dir=paths.data_dir if paths.data_dir else None,
            )
            layer = q.business_layer(focus=focus)
        except Exception as e:
            logger.warning(
                "BusinessProvider.collect fallo (%s: %s); sin capa business",
                type(e).__name__, e,
            )
            return None

        lines: list[str] = []
        pure = layer.get("pure_functions", [])[:max_items]
        if pure:
            lines.append("PURE FUNCTIONS (reusable, no side-effects):")
            for p in pure:
                sig = (p.get("signature", "") or "")[:90]
                lines.append(f"  - {p.get('name')} ({p.get('file','').split('/')[-1]})")
                if sig:
                    lines.append(f"      {sig}")
        if include_flows:
            flows = layer.get("business_flows", [])[:10]
            if flows:
                lines.append("\nBUSINESS FLOWS (use-case chains):")
                for f in flows:
                    chain = " -> ".join(f.get("chain", [])[:6])
                    lines.append(f"  - {f.get('entrypoint')}: {chain}")
        patterns = layer.get("architecture_patterns", [])[:10]
        if patterns:
            lines.append("\nARCHITECTURE PATTERNS:")
            for p in patterns:
                lines.append(f"  - {p.get('pattern')} ({p.get('file','').split('/')[-1]})")

        if not lines:
            return None

        content = "\n".join(lines)
        return ContextPiece(
            provider="business",
            name="business layer",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=40,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers de code_analysis en un ContextProviderRegistry."""
    registry.register("cpg", CPGProvider())
    registry.register("business", BusinessProvider())
