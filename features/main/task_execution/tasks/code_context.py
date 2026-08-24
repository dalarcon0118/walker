"""code_context task — thin wrapper over CodeContextService.

This task does NOT build use cases or access graph infrastructure directly.
It receives CodeContextService via DI from the composition root (preferred)
or falls back to the resolver when called through the registry.

Design: Use Case (code_analysis) → Application Service (code_analysis) →
Task (main) → Agent/MCP/CLI.
"""
from __future__ import annotations

from typing import Any

from shared.common.shared_domain import ExecutionContext, TaskResult


def code_context(
    ctx: ExecutionContext,
    data: dict[str, Any],
    code_context_service: Any | None = None,
) -> TaskResult:
    """Thin wrapper: delegates to CodeContextService.

    Preferred path: code_context_service is injected by the composition root.
    Fallback path: resolves via resolver when called from registry/CLI/MCP.
    """
    # ADR-WALKER-053 D3: la task CONSUME contexto resuelto, no lo arma.
    # repo path efectivo = payload explícito → _project_ctx inyectado por el
    # despachador → chain_dir ambiental (legacy sprint-dir).
    from features.main.task_execution.application.project_context import (
        resolve_repo_path,
    )

    repo_path = resolve_repo_path(data, ctx)
    chain_dir = getattr(ctx, "chain_dir", "") or ""
    if not repo_path and not chain_dir:
        return TaskResult.failure(
            "no project context",
            data={
                "next_step": (
                    "Pasá {project_id} o corré dentro de un proyecto "
                    "(walker <project>/list muestra los disponibles)"
                )
            },
        )

    service = code_context_service
    if service is None:
        from features.main.task_execution.application.code_context_resolver import (
            resolve_code_context_service,
        )
        service = resolve_code_context_service(repo_path or chain_dir)

    if service is None:
        return TaskResult.failure(
            "code_context service unavailable",
            data={"next_step": "Check composition root registration"},
        )

    target_file = data.get("file", "")
    query = data.get("query", "").strip()

    try:
        result = service.execute(target_file=target_file, query=query)
    except Exception as exc:
        return TaskResult.failure(
            f"code_context failed: {exc}",
            data={"target": target_file or query},
        )

    return TaskResult.success(data=result)


def register(registry: Any) -> None:
    registry.register(
        "code_context",
        code_context,
        metadata={
            "description": (
                "Structured code context for the agent: symbols, "
                "calls, imports and relations by file or search. "
                "Use it when you need to understand 'what X involves' or 'who calls Y', "
                "instead of reading flat files or searching text."
            ),
            "usage": 'walker <project>/code_context \'{"file":"src/auth.py"}\'',
            "example": 'walker <project>/code_context \'{"query":"auth login"}\'',
            "query_instructions": (
                "When to use: "
                "(1) file → context for a specific file; "
                "(2) query → structured search by symbol/function/class; "
                "(3) do NOT use for plain text (use read_code) or general knowledge (use code_search)."
            ),
            "tags": ["code", "context", "cpg", "graph", "business"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "File path to contextualize (relative to project_root).",
                    },
                    "query": {
                        "type": "string",
                        "description": "Structured search by symbol, function or class name.",
                    },
                    "depth": {
                        "type": "string",
                        "description": "Depth: shallow | normal | deep (default normal).",
                    },
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "context_type": {"type": "string", "enum": ["file", "search", "function", "class", "module"]},
                    "target": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "summary": {"type": "string"},
                    "symbols": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                                "signature": {"type": "string"},
                            },
                            "required": ["name", "type", "file"],
                        },
                    },
                    "relations": {
                        "type": "object",
                        "properties": {
                            "calls": {"type": "array"},
                            "called_by": {"type": "array"},
                            "imports": {"type": "array"},
                        },
                    },
                    "insights": {"type": "array", "items": {"type": "string"}},
                    "next_actions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["context_type", "target", "confidence", "summary", "symbols", "relations", "insights", "next_actions"],
            },
        },
    )
