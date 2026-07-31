"""kg_research — thin adapter to cognitive_knowledge use case."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.cognitive_knowledge.application.use_cases import ResearchTopicUseCase


def kg_research(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    topic = data.get("topic", "")
    if not topic:
        return TaskResult.failure("Requires --topic")
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    result = ResearchTopicUseCase.execute(chain_dir, topic, data.get("query", ""))
    return TaskResult.success(data=result) if result.get("saved") else TaskResult.failure(result.get("error", "Research failed"))


def register(registry: Any) -> None:
    registry.register("kg_research", kg_research,
                      metadata={"description": "Research topic on web, save to CKG", "tags": ["kg", "research"]})
