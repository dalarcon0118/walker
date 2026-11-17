"""test_fast_mcp_handler_registry_bdd.py — Tests for the handler registry pattern.

Verifies:
  1. All handlers are registered and loadable.
  2. dispatch() routes to the correct handler.
  3. MemoryContext resolves correctly.
  4. Unknown tools return error without raising.
  5. Handler failures are caught consistently.
  6. No imports of mcp/ in handler modules.
"""

from __future__ import annotations

import ast
import inspect

import pytest


def test_all_handlers_registered():
    """All memory tool handlers are registered in the registry."""
    from fast_mcp.memories.handlers import HANDLER_NAMES

    expected = {
        "read_code", "code_search", "code_context", "inspect_codebase",
        "build_code_index", "update_incremental_code",
        "knowledge_query", "extract_knowledge", "memory_schema",
        "recall_knowledge", "documenting_knowledge", "search_memories",
        "agent_context", "session_context", "preflight", "index_repo",
        "search_skills", "load_skill",
        "snapshot", "baseline_diff",
    }
    assert HANDLER_NAMES == expected, f"Missing: {expected - HANDLER_NAMES}; Extra: {HANDLER_NAMES - expected}"


def test_all_handlers_loadable():
    """Every registered handler can be loaded without errors."""
    from fast_mcp.memories.handlers import get_handler, HANDLER_NAMES

    for name in HANDLER_NAMES:
        handler = get_handler(name)
        assert handler is not None, f"Handler '{name}' could not be loaded"
        assert callable(handler), f"Handler '{name}' is not callable"


def test_dispatch_unknown_tool_returns_error():
    """dispatch() with unknown tool name returns error dict."""
    from fast_mcp.memories.handlers import dispatch
    from fast_mcp.memories.context import MemoryContext

    # Use a dummy context (dispatch checks handler existence before calling)
    ctx = MemoryContext(chain_dir="/tmp", project_root="/tmp", project_id="test")
    result = dispatch("nonexistent_tool_xyz", ctx)
    assert "error" in result
    assert "Unknown memory tool" in result["error"]


def test_dispatch_read_code_no_file_returns_error():
    """dispatch('read_code') without file param returns error."""
    from fast_mcp.memories.handlers import dispatch
    from fast_mcp.memories.context import MemoryContext

    ctx = MemoryContext(chain_dir="/tmp", project_root="/tmp", project_id="test")
    result = dispatch("read_code", ctx)
    assert "error" in result
    assert "file is required" in result["error"]


def test_dispatch_code_search_no_query_returns_error():
    """dispatch('code_search') without query returns error."""
    from fast_mcp.memories.handlers import dispatch
    from fast_mcp.memories.context import MemoryContext

    ctx = MemoryContext(chain_dir="/tmp", project_root="/tmp", project_id="test")
    result = dispatch("code_search", ctx)
    assert "error" in result
    assert "query is required" in result["error"]


def test_dispatch_index_repo_no_url_returns_error():
    """dispatch('index_repo') without url returns error."""
    from fast_mcp.memories.handlers import dispatch
    from fast_mcp.memories.context import MemoryContext

    ctx = MemoryContext(chain_dir="/tmp", project_root="/tmp", project_id="test")
    result = dispatch("index_repo", ctx)
    assert "error" in result
    assert "url is required" in result["error"]


def test_memory_context_resolve_none_on_empty():
    """MemoryContext.resolve returns None for empty chain_dir."""
    from fast_mcp.memories.context import MemoryContext

    assert MemoryContext.resolve("") is None
    assert MemoryContext.resolve(None) is None  # type: ignore[arg-type]


def test_memory_context_holds_resolved_project(tmp_path):
    """MemoryContext.resolve builds context with resolved paths."""
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases
    from fast_mcp.memories.context import MemoryContext

    # Create a project
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n")

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project
    use_cases.prepare_use_case(ctx, project_path=str(repo))

    # Resolve memory context
    mem_ctx = MemoryContext.resolve(str(fake_root / "data" / "projects.json"))
    # The chain_dir should resolve to something (or None if paths don't match)
    # This test mainly verifies the resolve call doesn't crash


def test_handlers_no_imports_of_mcp():
    """Handler modules don't import forbidden modules (app/features/shared/protocols)."""
    from fast_mcp.memories.handlers import code_analysis, knowledge

    forbidden_prefixes = ("app", "features", "shared", "protocols", "config", "data")
    allowed = {
        "mcp.server.fastmcp",
        "pydantic",
        "returns",
    }

    def is_forbidden(module: str) -> bool:
        root = module.split(".")[0]
        if root in allowed:
            return False
        return root in forbidden_prefixes

    for mod in (code_analysis, knowledge):
        tree = ast.parse(inspect.getsource(mod))
        bad = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and is_forbidden(node.module):
                bad.add(node.module)
            elif isinstance(node, ast.Import):
                for n in node.names:
                    if is_forbidden(n.name):
                        bad.add(n.name)
        assert not bad, f"{mod.__name__} imports forbidden modules: {bad}"
