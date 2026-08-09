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
            from features.engine.application.task_dispatcher import dispatch_public_task
            dispatch_public_task("trace_push", {"_payload": payload})
        except Exception:
            pass

    return _publish
