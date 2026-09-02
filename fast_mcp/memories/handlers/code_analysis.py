"""fast_mcp/memories/handlers/code_analysis.py — Code analysis tool handlers.

Each handler receives a resolved MemoryContext (no repeated resolve_paths)
and returns a dict. Consistent error handling via the dispatch() wrapper.
"""
from __future__ import annotations

import os
from typing import Any

from fast_mcp.memories.context import MemoryContext


def handle_read_code(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Read a project file's content by path."""
    file_path = kwargs.get("file", "")
    if not file_path:
        return {"error": "file is required"}

    candidate = (
        file_path if os.path.isabs(file_path)
        else os.path.join(ctx.project_root, file_path)
    )
    candidate = os.path.normpath(candidate)

    if not os.path.isfile(candidate):
        return {"file": file_path, "exists": False, "error": f"file not found: {file_path}"}

    with open(candidate, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return {
        "file": file_path,
        "project_root": ctx.project_root,
        "exists": True,
        "size": len(content),
        "content": content,
    }


def handle_code_search(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Full-text search in CKG over indexed code and knowledge."""
    from returns.pipeline import is_successful

    from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import CkgReadService

    query = kwargs.get("query", "").strip()
    if not query:
        return {"error": "query is required"}

    max_results = int(kwargs.get("max_results", 20))
    result = CkgReadService.gather_context(ctx.chain_dir, query=query)
    results = result.value_or([]) if is_successful(result) else []
    return {"query": query, "count": len(results), "results": results[:max_results]}


def handle_code_context(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Structured code context: symbols, calls, imports and relations."""
    service = ctx.code_context_service()
    target_file = kwargs.get("file", "")
    query = kwargs.get("query", "").strip()
    return service.execute(target_file=target_file, query=query)


def handle_inspect_codebase(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Inspect codebase: architecture, dead code, security, metrics."""
    service = ctx.code_analysis_service()
    action = kwargs.get("action", "business")
    scope = kwargs.get("scope", kwargs.get("area", ctx.chain_dir))

    dispatch_map = {
        "business": lambda: service.business_layer(focus=scope),
        "dead_code": lambda: service.detect_unused_functions(file_filter=scope),
        "review": lambda: service.review_code(),
        "metrics": lambda: service.project_metrics(file_filter=scope),
    }

    result_fn = dispatch_map.get(action)
    if result_fn is None:
        return {"scope": scope, "action": action, "error": f"Unknown action: {action}"}

    return {"scope": scope, "action": action, "result": result_fn()}


def handle_build_code_index(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Full CPG rebuild from scratch."""
    cpg = ctx.cpg()
    if cpg is None:
        return {"built": False, "error": "CPG not available"}
    result = cpg.build()
    return {"built": True, "project_root": ctx.project_root, "result": result}


def handle_update_incremental_code(ctx: MemoryContext, **kwargs: Any) -> dict:
    """Incremental CPG update via watcher: re-indexes only changed files."""
    cpg = ctx.cpg()
    if cpg is None:
        return {"updated": False, "error": "CPG not available"}
    result = cpg.update()
    return {"updated": True, "project_root": ctx.project_root, "result": result}
