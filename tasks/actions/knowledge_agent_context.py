"""kg_agent_context task — consulta consolidada delta + ckg + cpg + trace.

Devuelve un resumen del proyecto y su actividad reciente en una sola llamada:
  walker <project>/agent_context '{"delta":{},"ckg":{},"cpg":{},"trace":{}}'

ADT-WALKER-023 (decisión): las tools NO tocan CognitiveGraph directo. Esta task
delega en el orquestador de joins TaskIntelligenceService (task_execution/application),
que cruza las 4 capas por archivo/task_id y devuelve una respuesta de negocio.

Result pattern (dry-python/returns): el orquestador degrada cada sección con log
(convention-001: nunca except silencioso). El caller solo enruta.
"""

from __future__ import annotations
import json
import logging
from typing import Any
from shared.core.engine.domain.execution import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


def _compact(v, max_chars=400):
    if v is None:
        return ""
    s = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
    s = s.strip()
    return s[:max_chars] + ("…" if len(s) > max_chars else "")


def kg_agent_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from features.project import ProjectService
    from app.orchestration.intelligence_service import (
        TaskIntelligenceService,
        build_intelligence_service,
    )
    from tasks.infrastructure.kg_context import _trace_kg_query

    chain_dir = ctx.chain_dir or ""
    project_id = data.get("_project_id", "")

    # Resolver project_root solo para validar contexto de proyecto (sin construir el grafo).
    project_root = ""
    try:
        paths = ProjectService.resolve_paths(chain_dir)
        project_root = str(paths.project_root)
    except Exception as e:
        logger.warning(
            "agent_context: resolve_paths(%s) fallo (%s: %s)",
            chain_dir,
            type(e).__name__,
            e,
        )

    if not project_root:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No active sprint and no project context",
                "next_step": "Run 'walker <project>/status' or 'walker <project>/build_initial_ckg' first",
            },
        )

    body = data.get("_payload", data) if isinstance(data, dict) else {}
    delta_q = body.get("delta", {}) or {}
    ckg_q = body.get("ckg", {}) or {}
    cpg_q = body.get("cpg", {}) or {}
    trace_q = body.get("trace", {}) or {}

    _trace_kg_query(chain_dir, "agent_context", body, ctx)

    # Construir el orquestador de joins (consume los 4 repos, esconde CognitiveGraph).
    from returns.pipeline import is_successful

    service: TaskIntelligenceService = build_intelligence_service(chain_dir)
    result = service.get_task_intelligence(
        file=(
            cpg_q.get("file")
            or ckg_q.get("file")
            or delta_q.get("file")
            or trace_q.get("file")
            or ""
        ),
        task_id=(
            delta_q.get("task_id")
            or trace_q.get("task_id")
            or ckg_q.get("task_id")
            or ""
        ),
        text=(
            ckg_q.get("text") or ckg_q.get("query", "") or trace_q.get("text", "") or ""
        ),
        chain_dir=chain_dir,
    )
    if not is_successful(result):
        error = result.failure()
        logger.warning(
            "agent_context: get_task_intelligence fallo (%s: %s)",
            type(error).__name__,
            error,
        )
        return TaskResult.failure(
            str(error),
            data={
                "error": "La consulta consolidada no pudo completarse",
                "next_step": "Reintenta o revisa los índices con memory_schema",
            },
        )

    payload = result.value_or(None).as_dict()

    return TaskResult.success(
        data={
            "type": "kg_agent_context",
            "project_id": project_id or ctx.project_id or "",
            "summary": payload.get("summary", {}),
            "delta": payload.get("delta", {}),
            "ckg": payload.get("ckg", {}),
            "cpg": payload.get("cpg", {}),
            "trace": payload.get("trace", {}),
            "insights": payload.get("insights", []),
            "next_actions": [
                {
                    "command": "walker memory_schema",
                    "reason": "discover available indices",
                },
                {
                    "command": "walker search_memories {'ckg':{},'trace':{}}",
                    "reason": "knowledge + recent activity",
                },
                {
                    "command": 'walker agent_context {"cpg":{"file":$ARCHIVO}}',
                    "reason": "estructura + convenciones + specs de un archivo",
                },
            ],
        }
    )
# Nota: el registro de la task "agent_context" es ÚNICO y declarativo — vive en
# kg_metadata.py (KG_TASKS), que kg.py (thin adapter) registra. Este módulo NO
# tiene register() propio para evitar el doble registro (ADR-051 G3).
