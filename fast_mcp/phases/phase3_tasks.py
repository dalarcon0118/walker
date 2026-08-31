"""fast_mcp.phases.phase3_tasks — Fase 3: tasks de ejecución + workflow completo.

Cubre las tools de EJECUCIÓN y el flujo de trabajo completo que cierran la
migración:
  - planificación: sprint_planning, resolve_project_sprint, create_first_sprint
  - ejecución:     implement_task, run_tests, verify_predicates, review_code,
                   capture, refine_*, research_topic, trace_push, intelligence
  - integración:   detect_project_context, build_project_config, update_project,
                   context_gathering

Tras esta fase, `fast-mcp/` cubre la superficie completa de `mcp/` y el resto
del monorepo no migrado se elimina (ADR-WALKER-071).

Capa de test de la fase: tests/ — test_workflow_e2e_*, test_task_contract_ssot,
test_smoke_agent_harness, test_agent_e2e_bdd, test_intelligence_service_bdd.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def install(server: FastMCP) -> None:
    """Registra las tools de tasks + workflow de la Fase 3 (pendiente de migrar)."""
    # TODO(Fase 3): implementar cuando la Fase 2 quede verde.
    logger.info("fast_mcp fase3: tasks/workflow — pendiente de migrar (no se registra nada)")
