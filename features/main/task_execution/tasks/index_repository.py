"""index_repo — thin adapter to cognitive_knowledge CkgApplicationService.

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
class IndexRepoInput(TaskInput):
    """Input validado de index_repo.

    url (required): repo remoto.
    name: nombre (default: último segmento de la url).
    branch: rama (default main).
    """

    url: str = ""
    name: str = ""
    branch: str = "main"

    def _validate(self) -> str:
        if not self.url:
            return "Requires --url"
        return ""


def index_repo(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    input_result = IndexRepoInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "url requerida")
    inp: IndexRepoInput = input_result.data
    url = inp.url
    name = inp.name or url.rstrip("/").split("/")[-1]
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    result = CkgApplicationService.index_repo(chain_dir, url, name, inp.branch)
    if not is_successful(result):
        return TaskResult.failure(str(result.failure()))
    data_res = result.value_or({})
    return TaskResult.success(data=data_res) if data_res.get("ok", True) else TaskResult.failure(data_res.get("error", ""))


def register(registry: Any) -> None:
    registry.register("index_repo", index_repo,
                      metadata={"description": "Index external repo in CKG", "tags": ["kg", "ckg"],
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "url": {"type": "string", "description": "URL del repo remoto a indexar (requerido)", "example": "https://github.com/org/repo.git"},
                                        "name": {"type": "string", "description": "Nombre del repo (default: último segmento de la url)", "example": "repo"},
                                        "branch": {"type": "string", "description": "Rama a indexar (default main)", "example": "develop"},
                                    },
                                    "required": ["url"],
                                }})
