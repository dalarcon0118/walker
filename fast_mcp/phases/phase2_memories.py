"""fast_mcp.phases.phase2_memories — Fase 2: memorias que leen datos y escriben en prepare.

Cubre las tools de MEMORIA que el agente usa para leer el estado del proyecto
(CKG/CPG/Delta) y escribir conocimiento resultante durante el flujo de prepare:
indexado, kg_*, code_context, extract_knowledge, etc.

Ejemplos del grupo (a confirmar contra `tool_catalog.list_tools()`):
  - indexación:  build_code_index, index_repo, update_incremental_code
  - conocimiento: extract_knowledge, knowledge_query, knowledge_metadata,
                  knowledge_agent_context, session_knowledge
  - análisis de código: cpg_* / code_context, inspect_codebase, read_code, code_search

NOTA: esta fase se implementa tras validar la Fase 1. Los casos de uso de
memorias se PORTAN a fast_mcp (como phase1 con `fast_mcp.use_cases`) — sin
importar nada de `mcp/` (ADR-WALKER-071 §0 #4/#5).

Capa de test de la fase: tests/ — test_kg_search_*, test_cpg_*, test_cognitive_graph_*,
test_rag_pipeline_bdd.py, test_knowledge_capture_schema_bdd.py.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def install(server: FastMCP) -> None:
    """Registra las tools de memorias de la Fase 2 (pendiente de migrar)."""
    # TODO(Fase 2): implementar cuando la Fase 1 quede verde.
    logger.info("fast_mcp fase2: memorias — pendiente de migrar (no se registra nada)")
