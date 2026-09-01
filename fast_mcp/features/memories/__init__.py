"""features/memories — Memory tool handlers (Phase 2).

Provides the handler registry for all 14 memory tools. Each handler
receives a resolved MemoryContext and returns a dict.

Usage:
    from fast_mcp.features.memories.handlers import dispatch, HANDLER_NAMES

    result = dispatch("read_code", ctx, file="src/main.py")
"""
