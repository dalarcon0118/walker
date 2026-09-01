"""infrastructure/adapters/trace_event_publisher.py — Publicador de eventos CPG al trace.

Escucha eventos del EventBus de code_analysis y los registra en el índice
trace via el servicio público trace_push. Si no se puede resolver el
contexto del proyecto, los eventos se descartan best-effort.
"""
from __future__ import annotations


def create_trace_publisher(chain_dir: str) -> callable | None:
    """Crea un listener que publica eventos CPG en el índice trace.

    Args:
        chain_dir: directorio del sprint/proyecto.

    Returns:
        Función listener para EventBus.subscribe(), o None si no se pudo
        construir el publisher (sin proyecto resoluble).
    """
    try:
        from fast_mcp.shared_kernel.project_support import ProjectService
        paths = ProjectService.resolve_paths(chain_dir)
        project_id = paths.project_id
    except Exception:
        return None

    def _publish(event: object) -> None:
        # Trace feature deferred to Sprint 5. Log event for later replay.
        # TODO: port trace_push task and restore dispatch when event_tracing is ready.
        import logging
        logging.getLogger(__name__).debug(
            "trace_event_publisher: event buffered (trace not ported yet): %s",
            type(event).__name__,
        )

    return _publish
