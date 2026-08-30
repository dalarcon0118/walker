"""research_topic — thin adapter to cognitive_knowledge CkgApplicationService.

ADR-WALKER-048: los tasks consumen Application Services, nunca Use Cases
directos (INV-013).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from shared.common.shared_domain import ExecutionContext, TaskResult
from shared.common.shared_domain.validated_vo import TaskInput
from shared.common.result_types import is_successful
from features.cognitive_knowledge.application.services.ckg_service import (
    CkgApplicationService,
)


@dataclass(frozen=True)
class ResearchInput(TaskInput):
    """Input validado de research_topic.

    topic (required): tema a investigar.
    query: query adicional (opcional).
    """

    topic: str = ""
    query: str = ""

    def _validate(self) -> str:
        if not self.topic:
            return "Requires --topic"
        return ""


def research_topic(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    input_result = ResearchInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "topic requerido")
    inp: ResearchInput = input_result.data
    topic = inp.topic
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    result = CkgApplicationService.research_topic(chain_dir, topic, inp.query)
    if not is_successful(result):
        return TaskResult.failure(str(result.failure()))
    data_res = result.value_or({})
    return TaskResult.success(data=data_res) if data_res.get("saved") else TaskResult.failure(data_res.get("error", "Research failed"))


def register(registry: Any) -> None:
    registry.register("research_topic", research_topic,
                      metadata={"description": "Research topic on web, save to CKG", "tags": ["kg", "research"],
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "topic": {"type": "string", "description": "Tema a investigar en la web y guardar en CKG (obligatorio)", "example": "OAuth 2.0 client credentials"},
                                        "query": {"type": "string", "description": "Query adicional de investigación (opcional)", "example": "with Python SDK"},
                                    },
                                    "required": ["topic"],
                                }})
