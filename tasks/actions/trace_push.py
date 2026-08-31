"""Task: trace_push — registra eventos en el TraceGraph.

Result pattern (dry-python/returns): el parseo de input/output/files con
@safe nunca lanza; ante JSON inválido degrada a dict crudo con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.core.engine.domain.execution import ExecutionContext, TaskResult
from shared.libs.cognitive_graph.config import load_memory_connection

logger = logging.getLogger(__name__)


def _project_paths(chain_dir: str) -> Any:
    """Lazy (C3): ProjectService sin edge import-time a features.project."""
    from features.project import ProjectService

    return ProjectService.resolve_paths(chain_dir)


@safe
def _parse_json_field(raw: Any) -> Any:
    """Core puro: parsea un campo JSON (puede lanzar JSONDecodeError/TypeError)."""
    return json.loads(raw) if isinstance(raw, str) else raw


def _safe_parse(raw: Any, label: str) -> Any:
    """Decide con is_successful: JSON válido → dict; inválido → {"raw": ...} + log."""
    result = _parse_json_field(raw)
    if is_successful(result):
        return result.value_or(raw)
    error = result.failure()
    logger.warning(
        "trace_push: campo '%s' no es JSON válido (%s: %s); se guarda como crudo",
        label, type(error).__name__, error,
    )
    return {"raw": raw}


def trace_push(ctx: ExecutionContext, data: dict[str, Any], graph_factory=None) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")

    paths = _project_paths(chain_dir)
    _factory = graph_factory
    if _factory is None:
        # ADR-062: id+contrato via resolve_locator — la task no importa app/.
        from shared.core.engine.bootstrap import resolve_locator

        _factory = resolve_locator("cognitive_graph")
    if _factory is None:
        raise ValueError(
            "trace_push requiere graph_factory inyectado. "
            "Pásalo desde el composition root."
        )
    from app.path_resolver import memory_data_dir

    cg = _factory(load_memory_connection(memory_data_dir(paths.project_root)))
    ts = datetime.now(timezone.utc).isoformat()

    event_id = f"ev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    event_type = data.get("event_type", "event")
    source = data.get("source", "walker_task")

    input_data = _safe_parse(data.get("input", "{}"), "input")
    output_data = _safe_parse(data.get("output", "{}"), "output")

    files_raw = data.get("files", [])
    if isinstance(files_raw, str):
        parsed = _parse_json_field(files_raw)
        files_raw = parsed.value_or([files_raw]) if is_successful(parsed) else [files_raw]

    event = {
        "event_type": event_type, "source": source,
        "tool": data.get("tool", ""),  # ADR-WALKER-055: persistir tool top-level (el hook la envía; antes se descartaba → trace no consultable por herramienta)
        "agent_id": os.environ.get("WALKER_AGENT_ID", ""),
        "session_id": os.environ.get("WALKER_SESSION_ID", ctx.session_id),
        "sprint_id": data.get("sprint_id", ""),
        "task_id": data.get("task_id", ctx.task_id or ""),
        "node": data.get("node", ""), "sub_state": data.get("sub_state", ""),
        "input": input_data, "output": output_data,
        "files": files_raw if isinstance(files_raw, list) else [],
        "started_at": data.get("started_at", ts), "finished_at": ts,
        "duration_ms": data.get("duration_ms", 0),
        "metadata": data.get("metadata", {}),
    }

    cg.store("trace", event_id, event)
    return TaskResult.success(data={"ok": True, "event_id": event_id, "event_type": event_type})


def register(registry: Any) -> None:
    registry.register("trace_push", trace_push,
                      metadata={"description": "Push events to TraceGraph", "tags": ["trace", "events"], "scope": "system"})
