"""trace_factory.py — construcción del TraceService para un chain_dir.

Extraído de `composition.trace_tool_runner` para que cualquier adaptador
(handler MCP, gate del workflow) pueda empujar eventos sin duplicar el wiring
del CognitiveGraph. El composition root sigue siendo el dueño del ciclo de vida;
esto es una fábrica sin estado.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_trace_service(chain_dir: str) -> Any | None:
    """TraceService listo para `push_event`, o None si el grafo no resuelve."""
    if not chain_dir:
        return None
    try:
        from fast_mcp.features.event_tracing.application.services.trace_service import (
            TraceService,
        )
        from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import (
            CognitiveTraceRepository,
        )
        from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
        from fast_mcp.shared_kernel.cognitive_graph.config import (
            load_memory_connection,
        )
        from fast_mcp.shared_kernel.cognitive_graph.schemas import (
            PRODUCTION_INDEX_SCHEMAS,
        )
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        conn = load_memory_connection(paths.data_dir)
        cg = build_cognitive_graph(paths.data_dir, conn=conn)
        try:
            cg.register_index("trace", PRODUCTION_INDEX_SCHEMAS["trace"])
        except Exception:  # noqa: BLE001 — ya registrado
            pass
        return TraceService(CognitiveTraceRepository(cg))
    except Exception as exc:  # noqa: BLE001 — tracing best-effort
        logger.warning("no se pudo construir el TraceService (%s: %s)", type(exc).__name__, exc)
        return None
