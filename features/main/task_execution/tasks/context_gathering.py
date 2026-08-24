"""Context gathering tasks — internal workflow steps via ICpgAnalysisPort + CKG.

ADR-WALKER-048: las tasks consumen Application Services, nunca Use Cases
directos (INV-013).
"""
from __future__ import annotations
from typing import Any
from shared.common.shared_domain import ExecutionContext, TaskResult
from shared.common.result_types import is_successful
from features.cognitive_knowledge.application.services.ckg_service import (
    CkgApplicationService,
)


def _ckg_context(chain_dir: str, task_id: str = "") -> list:
    result = CkgApplicationService.gather_context(chain_dir, task_id=task_id)
    if not is_successful(result):
        return []
    return result.value_or([])


def _cpg_port(chain_dir: str) -> Any:
    from features.main.task_execution.application.cpg_port_resolver import resolve_cpg_port
    return resolve_cpg_port(chain_dir)


def gather_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    files = data.get("files", [])
    port = _cpg_port(chain_dir)
    cpg_result = port.gather_context(files) if port else {}
    context = {
        "cpg": cpg_result,
        "ckg": _ckg_context(chain_dir),
        "input": data.get("context", {}),
    }
    return TaskResult.success(data={"task_id": data.get("task_id", ctx.task_id),
                                     "context": context, "files_analyzed": len(files)})


def gather_task_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    files = data.get("files", [])
    task_id = data.get("task_id", ctx.task_id)
    port = _cpg_port(chain_dir)
    cpg_result = port.gather_context(files) if (files and port) else {}
    context = {
        "cpg": cpg_result,
        "ckg": _ckg_context(chain_dir, task_id),
        "task_id": task_id, "input": data.get("context", {}),
    }
    return TaskResult.success(data={"task_id": task_id, "context": context,
                                     "files_analyzed": len(files)})


def register(registry: Any) -> None:
    registry.register(
        "gather_context",
        gather_context,
        metadata={
            "description": "Reúne contexto del CPG + CKG para archivos específicos.",
            "usage": 'walker <project>/gather_context \'{"files":["src/auth.py"]}\'',
            "example": 'walker <project>/gather_context \'{"files":["src/auth.py"],"task_id":"TASK-042"}\'',
            "tags": ["code", "context", "cpg", "ckg"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Archivos a analizar (relativos al project_root).",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "ID de tarea para enriquecer con contexto específico.",
                    },
                    "context": {
                        "type": "object",
                        "description": "Contexto adicional del llamador.",
                    },
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "context": {"type": "object"},
                    "files_analyzed": {"type": "integer"},
                },
                "required": ["context", "files_analyzed"],
            },
        },
    )
    registry.register(
        "gather_task_context",
        gather_task_context,
        metadata={
            "description": "Reúne contexto del CPG + CKG para una tarea específica.",
            "usage": 'walker <project>/gather_task_context \'{"files":["src/auth.py"],"task_id":"TASK-042"}\'',
            "example": 'walker <project>/gather_task_context \'{"files":["src/auth.py"]}\'',
            "tags": ["code", "context", "cpg", "ckg", "task"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Archivos a analizar (relativos al project_root).",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "ID de tarea para filtrar conocimiento específico.",
                    },
                    "context": {
                        "type": "object",
                        "description": "Contexto adicional del llamador.",
                    },
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "context": {"type": "object"},
                    "files_analyzed": {"type": "integer"},
                },
                "required": ["context", "files_analyzed"],
            },
        },
    )
