"""event_tracing/context/providers.py — Providers de contexto del Trace."""
from __future__ import annotations
from typing import Any

from app.context_format import format_trace_results
from shared.common.context import ContextPiece, ContextProvider, fmt_tokens


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


class ReflectionProvider(ContextProvider):
    """Capa: Reflection — paquete de evidencia para REFINE.DIAGNOSE.

    Reflexion-by-context: entrega al agente externo los HECHOS (intentos
    pasados, gates fallidos, hint determinista) extraídos del TraceGraph +
    estado, para que él reflexione y diagnostique la causa raíz.

    El walker no decide: solo prepara la evidencia que el LLM del agente
    consume en el prompt de REFINE.DIAGNOSE.
    """

    LAYER = "reflection"

    def instructions(self, config: dict, ctx: Any) -> str:
        return (
            "Layer: reflection — evidence package for this task (source node, "
            "attempt counter, past attempts, failed gates, deterministic hint). "
            "Use it to reflect on what already failed before diagnosing root cause."
        )

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        chain_dir = getattr(ctx, "chain_dir", "")
        if not chain_dir:
            return None

        baggage = getattr(ctx, "baggage", {}) or {}
        task_id = (
            baggage.get("task_id")
            or getattr(ctx, "task_id", "")
        )
        # Claves GENÉRICAS que el motor persiste para cualquier nodo/error:
        #   - last_error / last_error_strategy: SubmitOrchestrator tras un fallo
        #   - last_reason: TransitionUseCase con el reason de la transición
        # El provider (feature de negocio) las interpreta como contexto REFINE.
        last_error = baggage.get("last_error", "")
        last_error_strategy = baggage.get("last_error_strategy", "")
        last_reason = baggage.get("last_reason", "")
        failed_gates = baggage.get("step_failed_gates", "")

        from features.event_tracing.application.reflection import (
            build_reflection_context,
            format_reflection_context,
        )

        package = build_reflection_context(
            chain_dir=chain_dir,
            task_id=task_id,
            last_error=last_error,
            last_error_strategy=last_error_strategy,
            last_reason=last_reason,
            failed_gates=failed_gates,
        )
        content = format_reflection_context(package)

        return ContextPiece(
            provider="reflection",
            name=f"reflection evidence for task {task_id}",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=10,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers de event_tracing en un ContextProviderRegistry."""
    registry.register("trace", TraceProvider())
    registry.register("reflection", ReflectionProvider())
