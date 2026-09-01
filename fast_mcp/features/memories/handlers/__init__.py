"""features/memories/handlers/__init__.py — Handler registry.

Maps tool names to handler callables. Each handler has the signature:

    def handler(ctx: MemoryContext, **kwargs) -> dict

The registry provides consistent error handling (convention-001: never
silent except) and resolves the MemoryContext once per call.

Usage:
    from fast_mcp.features.memories.handlers import get_handler, HANDLER_NAMES

    handler = get_handler("read_code")
    result = handler(ctx, file="src/main.py")
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fast_mcp.features.memories.context import MemoryContext

logger = logging.getLogger(__name__)

# Type alias for handler functions
Handler = Callable[[MemoryContext, ...], dict[str, Any]]

# ── Handler registry (lazy imports to avoid circular deps) ──────────

_HANDLER_IMPORTS: dict[str, tuple[str, str]] = {
    # Code analysis
    "read_code": ("fast_mcp.features.memories.handlers.code_analysis", "handle_read_code"),
    "code_search": ("fast_mcp.features.memories.handlers.code_analysis", "handle_code_search"),
    "code_context": ("fast_mcp.features.memories.handlers.code_analysis", "handle_code_context"),
    "inspect_codebase": ("fast_mcp.features.memories.handlers.code_analysis", "handle_inspect_codebase"),
    "build_code_index": ("fast_mcp.features.memories.handlers.code_analysis", "handle_build_code_index"),
    "update_incremental_code": ("fast_mcp.features.memories.handlers.code_analysis", "handle_update_incremental_code"),
    # Knowledge
    "knowledge_query": ("fast_mcp.features.memories.handlers.knowledge", "handle_knowledge_query"),
    "extract_knowledge": ("fast_mcp.features.memories.handlers.knowledge", "handle_extract_knowledge"),
    "memory_schema": ("fast_mcp.features.memories.handlers.knowledge", "handle_memory_schema"),
    "recall_knowledge": ("fast_mcp.features.memories.handlers.knowledge", "handle_recall_knowledge"),
    "documenting_knowledge": ("fast_mcp.features.memories.handlers.knowledge", "handle_documenting_knowledge"),
    "search_memories": ("fast_mcp.features.memories.handlers.knowledge", "handle_search_memories"),
    "agent_context": ("fast_mcp.features.memories.handlers.knowledge", "handle_agent_context"),
    "index_repo": ("fast_mcp.features.memories.handlers.knowledge", "handle_index_repo"),
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


def dispatch(name: str, ctx: MemoryContext, **kwargs) -> dict[str, Any]:
    """Dispatch a tool call to its handler with consistent error handling.

    This is the single entry point for all memory tool dispatch.
    Replaces the 150-line if/elif chain in composition.py.
    """
    handler = get_handler(name)
    if handler is None:
        return {"error": f"Unknown memory tool: {name}"}

    try:
        return handler(ctx, **kwargs)
    except Exception as e:
        logger.warning(
            "handler '%s' failed (%s: %s)", name, type(e).__name__, e,
        )
        return {"error": str(e), "tool": name}
