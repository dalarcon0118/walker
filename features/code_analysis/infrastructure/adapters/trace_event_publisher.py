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
        from features.project import ProjectService
        paths = ProjectService.resolve_paths(chain_dir)
        project_id = paths.project_id
    except Exception:
        return None

    def _publish(event: object) -> None:
        try:
            payload = event.to_dict()
            payload.setdefault("session_id", "")
            payload.setdefault("agent_id", "")
            payload["_project_id"] = project_id
            payload.setdefault("source", "code_analysis")
            from shared.core.engine.application.task_dispatcher import execute_public_task
            from features.main.container import AppContainer

            # Motor interno (channel='system'): las tasks scope=system solo las
            # ejecuta el motor/adapters internos (ARQ objetivo SPEC-005); desde
            # CLI/MCP quedan bloqueadas en execute_public_task. Antes se usaba
            # dispatch_public_task (channel='mcp') y el gate de visibilidad
            # descartaba el evento en silencio (regresión SPEC-002 G-W1).
            registry = AppContainer.build().module_registry.get_service("task_registry")
            execute_public_task(
                "trace_push",
                project_id,
                chain_dir,
                {"_payload": payload},
                session_id="",
                channel="system",
                registry=registry,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                "trace_event_publisher: dropped event (%s: %s)",
                type(e).__name__,
                e,
            )

    return _publish
