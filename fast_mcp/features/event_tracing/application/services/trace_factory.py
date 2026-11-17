"""trace_factory.py — construcción del TraceService para un chain_dir.

Extraído de `composition.trace_tool_runner` para que cualquier adaptador
(handler MCP, gate del workflow) pueda empujar eventos sin duplicar el wiring
del CognitiveGraph. El composition root sigue siendo el dueño del ciclo de vida;
esto es una fábrica sin estado.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Un CognitiveGraph por proyecto y proceso. Sin esto, cada writer (workflow,
# trace_push, load_skill) abre su propia instancia sobre el mismo sqlite y la
# guarda de generación rechaza el segundo write ("el storage cambió bajo el
# cache"), perdiendo eventos en silencio (medido: el `skill_gate passed` no
# quedaba en el trace después de un `load_skill` desde otro service).
_SERVICES: dict[str, Any] = {}


def invalidate_trace_service(chain_dir: str) -> None:
    _SERVICES.pop(str(Path(chain_dir).resolve()), None)


def build_trace_service(chain_dir: str) -> Any | None:
    """TraceService listo para `push_event`, cacheado por proyecto."""
    if not chain_dir:
        return None
    key = str(Path(chain_dir).resolve())
    cached = _SERVICES.get(key)
    if cached is not None:
        return cached
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
        service = TraceService(CognitiveTraceRepository(cg))
        _SERVICES[key] = service
        return service
    except Exception as exc:  # noqa: BLE001 — tracing best-effort
        logger.warning("no se pudo construir el TraceService (%s: %s)", type(exc).__name__, exc)
        return None
