"""handlers/add_tasks.py — acción SPRINT_PLANNING.ADD_TASKS.

Agrega specs al sprint en curso. `tasks` vacío es un no-op exitoso (no borra
ni duplica): el sprint del roadmap se materializa una vez y luego se refina.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def add_tasks(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    try:
        from fast_mcp.composition import build_sprint_service

        service = build_sprint_service(
            str(getattr(ctx, "chain_dir", "") or data.get("chain_dir") or "")
        )
        if service is None:
            return TaskResult.failure("add_tasks: sprint service no disponible")

        spring_id = str(data.get("spring_id") or "")
        if not spring_id:
            session_id = str(getattr(ctx, "session_id", "") or data.get("session_id") or "")
            if session_id and hasattr(service, "get_session"):
                record = service.get_session(session_id) or {}
                spring_id = str(record.get("spring_id") or "")
        if not spring_id:
            return TaskResult.failure("add_tasks: spring_id o sesión con sprint ligado")

        tasks = data.get("tasks") or []
        if not isinstance(tasks, list):
            tasks = []
        spec_ids: list[str] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            spec = service.add_spec(
                spring_id=spring_id,
                objective=str(task.get("objective") or task.get("title") or ""),
                domain=str(task.get("domain") or ""),
                spec_type=str(task.get("type") or "task"),
                files=list(task.get("files") or []),
                status="specified",
            )
            spec_ids.append(str(spec.get("spec_id") or ""))
        return TaskResult.success(
            data={
                "ok": True,
                "spring_id": spring_id,
                "added": len(spec_ids),
                "spec_ids": spec_ids,
            }
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"add_tasks: {type(exc).__name__}: {exc}")


def register(registry) -> None:
    registry.register(
        "add_tasks",
        add_tasks,
        metadata={
            "description": "Add specs to the current sprint (no-op with empty tasks)",
            "tags": ["sprint", "planning"],
            "scope": "internal",
        },
    )
