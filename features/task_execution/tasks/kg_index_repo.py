"""kg_index_repo — thin adapter to cognitive_knowledge use case."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.cognitive_knowledge.application.use_cases import IndexRepoUseCase


def kg_index_repo(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    url = data.get("url", "")
    name = data.get("name", "")
    if not url:
        return TaskResult.failure("Requires --url")
    if not name:
        name = url.rstrip("/").split("/")[-1]
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    result = IndexRepoUseCase.execute(chain_dir, url, name, data.get("branch", "main"))
    return TaskResult.success(data=result) if result.get("ok", True) else TaskResult.failure(result.get("error", ""))


def register(registry: Any) -> None:
    registry.register("kg_index_repo", kg_index_repo,
                      metadata={"description": "Index external repo in CKG", "tags": ["kg", "ckg"]})
