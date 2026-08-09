"""task_execution/application/cpg_port_resolver.py — Factory del puerto CPG.

Centraliza la creación de ICpgAnalysisport para las tasks públicas.
Si no se inyecta un puerto, construye WalkerCpgAnalysisAdapter por defecto.

Result pattern (dry-python/returns): el core con @safe nunca lanza; ante
fallo de construcción devuelve None con log (convention-001).
"""
from __future__ import annotations

import logging
from returns.pipeline import is_successful
from returns.result import safe

from features.task_execution.ports.cpg_analysis_port import ICpgAnalysisPort

logger = logging.getLogger(__name__)


@safe
def _build_default_port(chain_dir: str) -> ICpgAnalysisPort:
    """Core puro: construye el WalkerCpgAnalysisAdapter por defecto (puede lanzar)."""
    from features.code_analysis.domain.events import EventBus
    from features.code_analysis.infrastructure.adapters.trace_event_publisher import create_trace_publisher
    from features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
        WalkerCpgAnalysisAdapter,
    )
    from features.code_analysis.infrastructure.cognitive_cpg_repo import (
        CognitiveCpgRepository,
    )
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    data_dir = str(paths.project_root)
    project_id = paths.project_id
    repo = CognitiveCpgRepository(data_dir, project_id)
    bus = EventBus()
    trace_publisher = create_trace_publisher(chain_dir)
    if trace_publisher is not None:
        bus.subscribe(trace_publisher)
    return WalkerCpgAnalysisAdapter(repo, event_bus=bus)


def resolve_cpg_port(chain_dir: str, port: ICpgAnalysisPort | None = None) -> ICpgAnalysisPort | None:
    """Resuelve el puerto CPG para una task.

    Args:
        chain_dir: directorio del sprint/proyecto.
        port: puerto inyectado (opcional). Si se provee, se usa tal cual.

    Returns:
        ICpgAnalysisPort o None si no se pudo construir.
    """
    if port is not None:
        return port
    result = _build_default_port(chain_dir)
    if is_successful(result):
        return result.value_or(None)
    logger.warning(
        "cpg_port_resolver: no se pudo construir el puerto CPG (%s: %s)",
        type(result.failure()).__name__, result.failure(),
    )
    return None
