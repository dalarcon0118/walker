"""fast_mcp/memories/handlers/__init__.py — Handler registry.

Maps tool names to handler callables. Each handler has the signature:

    def handler(ctx: MemoryContext, **kwargs) -> dict

The registry provides consistent error handling (convention-001: never
silent except) and resolves the MemoryContext once per call.

Usage:
    from fast_mcp.memories.handlers import get_handler, HANDLER_NAMES

    handler = get_handler("read_code")
    result = handler(ctx, file="src/main.py")
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fast_mcp.memories.context import MemoryContext

logger = logging.getLogger(__name__)

# Type alias for handler functions
Handler = Callable[[MemoryContext, ...], dict[str, Any]]

# ── Handler registry (lazy imports to avoid circular deps) ──────────

_HANDLER_IMPORTS: dict[str, tuple[str, str]] = {
    # Code analysis
    "read_code": ("fast_mcp.memories.handlers.code_analysis", "handle_read_code"),
    "code_search": ("fast_mcp.memories.handlers.code_analysis", "handle_code_search"),
    "code_context": ("fast_mcp.memories.handlers.code_analysis", "handle_code_context"),
    "inspect_codebase": ("fast_mcp.memories.handlers.code_analysis", "handle_inspect_codebase"),
    "build_code_index": ("fast_mcp.memories.handlers.code_analysis", "handle_build_code_index"),
    "snapshot": ("fast_mcp.memories.handlers.code_analysis", "handle_snapshot"),
    "baseline_diff": ("fast_mcp.memories.handlers.code_analysis", "handle_baseline_diff"),
    "update_incremental_code": ("fast_mcp.memories.handlers.code_analysis", "handle_update_incremental_code"),
    # Knowledge
    "knowledge_query": ("fast_mcp.memories.handlers.knowledge", "handle_knowledge_query"),
    "extract_knowledge": ("fast_mcp.memories.handlers.knowledge", "handle_extract_knowledge"),
    "memory_schema": ("fast_mcp.memories.handlers.knowledge", "handle_memory_schema"),
    "recall_knowledge": ("fast_mcp.memories.handlers.knowledge", "handle_recall_knowledge"),
    "documenting_knowledge": ("fast_mcp.memories.handlers.knowledge", "handle_documenting_knowledge"),
    "search_memories": ("fast_mcp.memories.handlers.knowledge", "handle_search_memories"),
    "agent_context": ("fast_mcp.memories.handlers.knowledge", "handle_agent_context"),
    "brief": ("fast_mcp.memories.handlers.knowledge", "handle_brief"),
    "plan_diff": ("fast_mcp.memories.handlers.knowledge", "handle_plan_diff"),
    "session_context": ("fast_mcp.memories.handlers.knowledge", "handle_session_context"),
    "preflight": ("fast_mcp.memories.handlers.knowledge", "handle_preflight"),
    "search_skills": ("fast_mcp.memories.handlers.knowledge", "handle_search_skills"),
    "load_skill": ("fast_mcp.memories.handlers.knowledge", "handle_load_skill"),
    "index_repo": ("fast_mcp.memories.handlers.knowledge", "handle_index_repo"),
}

# Public set of all handler names (for registration in phases)
HANDLER_NAMES: frozenset[str] = frozenset(_HANDLER_IMPORTS.keys())

# Cache of resolved handlers
_handlers: dict[str, Handler] = {}


def get_handler(name: str) -> Handler | None:
    """Get a handler by name (lazy import + cache)."""
    if name in _handlers:
        return _handlers[name]

    spec = _HANDLER_IMPORTS.get(name)
    if spec is None:
        return None

    module_path, attr_name = spec
    try:
        import importlib

        mod = importlib.import_module(module_path)
        handler = getattr(mod, attr_name)
        _handlers[name] = handler
        return handler
    except Exception as e:
        logger.warning(
            "handlers: could not load '%s' from %s.%s (%s: %s)",
            name, module_path, attr_name, type(e).__name__, e,
        )
        return None


def dispatch(tool_name: str, ctx: MemoryContext, **kwargs) -> dict[str, Any]:
    """Dispatch a tool call to its handler with consistent error handling.

    This is the single entry point for all memory tool dispatch.
    Replaces the 150-line if/elif chain in composition.py.
    """
    # OJO: el parámetro NO se puede llamar `name`: cualquier tool con un
    # argumento `name` (p. ej. `load_skill`) colisionaba con la firma y el
    # dispatch moría con "multiple values for argument 'name'" antes de llegar
    # al handler.
    handler = get_handler(tool_name)
    if handler is None:
        return {"error": f"Unknown memory tool: {tool_name}"}

    try:
        result = handler(ctx, **kwargs)
    except Exception as e:
        logger.warning(
            "handler '%s' failed (%s: %s)", tool_name, type(e).__name__, e,
        )
        return {"error": str(e), "tool": tool_name}

    _emit_kg_query(ctx, tool_name, result, kwargs)
    return result


# ── Evidencia de consulta (`kg_query`) ────────────────────────────────────
#
# `required_tools` del workflow exige un evento `kg_query` en la traza para
# probar que el agente consultó el CKG/CPG antes de implementar. El tipo estaba
# declarado en event_tracing/domain/values.py y NADIE lo emitía, así que el gate
# era insatisfacible y por eso nada obligaba a usar la memoria (medido
# 2026-11-17: `submit` en IMPLEMENT.WORK quedaba bloqueado para siempre).
#
# Se emite desde el dispatch — único punto por donde pasan todas las tools de
# memoria — y no desde cada handler (SRP: los handlers responden, no trazan).

_KG_QUERY_LAYERS: dict[str, tuple[str, ...]] = {
    "recall_knowledge": ("ckg",),
    "search_memories": ("ckg", "cpg", "delta", "trace"),
    "agent_context": ("ckg", "cpg", "delta", "trace"),
    "code_search": ("cpg",),
    "code_context": ("cpg",),
    "inspect_codebase": ("cpg",),
}

# `knowledge_query` hace reads y writes con el mismo nombre de tool.
_KNOWLEDGE_QUERY_READ_ACTIONS = frozenset({"search", "active"})


def _is_kg_query(tool_name: str, kwargs: dict) -> bool:
    if tool_name not in _KG_QUERY_LAYERS:
        if tool_name == "knowledge_query":
            return str(kwargs.get("action", "search")) in _KNOWLEDGE_QUERY_READ_ACTIONS
        return False
    return True


def _emit_kg_query(ctx: MemoryContext, tool_name: str, result: Any, kwargs: dict) -> None:
    """Registra que el agente consultó memoria (evidencia para `required_tools`)."""
    if not _is_kg_query(tool_name, kwargs):
        return
    if isinstance(result, dict) and result.get("error"):
        return  # una consulta fallida no es evidencia de nada
    try:
        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
        )

        service = build_trace_service(ctx.chain_dir)
        if service is None:
            return
        layers = _KG_QUERY_LAYERS.get(tool_name, ("ckg",))
        service.push_event(ctx.chain_dir, {
            "event_type": "kg_query",
            "source": "memory_tool",
            "data": {
                "tool": tool_name,
                "layers": list(layers),
                "index": str(kwargs.get("index", "")),
                "action": str(kwargs.get("action", "")),
            },
        })
    except Exception as exc:  # noqa: BLE001 — trazar no puede romper una lectura
        logger.warning("kg_query trace failed (%s: %s)", type(exc).__name__, exc)
