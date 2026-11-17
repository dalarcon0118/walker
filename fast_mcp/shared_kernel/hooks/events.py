"""shared_kernel/hooks/events.py — catálogo de eventos de ciclo de vida.

Antes los eventos eran strings libres: `emit("commti")` devolvía "sin
suscriptores" y un hook declarado con el evento mal escrito NUNCA corría sin que
nadie avisara (la misma familia de "cero silencioso" del resto del sistema).

El catálogo es la fuente de verdad para:
- validar manifiestos de plugins al instalar (`hook.json` → `events`),
- avisar en `HookBus.load` si un hook inline declara un evento inexistente,
- `walker hooks events` (qué eventos existen y quién los escucha).

Agregar un evento = agregarlo acá + emitirlo desde el código que lo produce.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventSpec:
    name: str
    description: str
    payload: tuple[str, ...]
    phase: str = "after"  # before | after
    blocking_allowed: bool = False


EVENTS: dict[str, EventSpec] = {
    "commit": EventSpec(
        name="commit",
        description="Después de un commit de git (lo emite el hook post-commit).",
        payload=("repo",),
    ),
    "cpg.indexed": EventSpec(
        name="cpg.indexed",
        description="Después de un sync del CPG que terminó OK (no en noop/error).",
        payload=("project_id", "mode", "nodes", "edges", "files"),
    ),
    "task.after": EventSpec(
        name="task.after",
        description="Después de ejecutar un task del workflow (también si falló).",
        payload=("task", "task_id", "session_id", "ok", "error", "chain_dir"),
    ),
}


def is_known(event: str) -> bool:
    return event in EVENTS


def known_events() -> list[str]:
    return sorted(EVENTS)


def describe() -> list[dict]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "payload": list(spec.payload),
            "phase": spec.phase,
            "blocking_allowed": spec.blocking_allowed,
        }
        for spec in (EVENTS[name] for name in known_events())
    ]


def validate_events(events: list[str]) -> list[str]:
    """Devuelve los eventos desconocidos (lista vacía = todo válido)."""
    return [event for event in events if not is_known(event)]
