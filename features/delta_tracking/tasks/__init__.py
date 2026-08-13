"""features/delta_tracking/tasks/ — Tasks de dominio del sprint."""

from __future__ import annotations

from typing import Any

from core.engine.infrastructure.task_registry import InMemoryTaskRegistry


def register_delta_tracking_tasks(registry: InMemoryTaskRegistry) -> None:
    """Registra todas las tasks de dominio de delta_tracking en el registry."""
    from features.delta_tracking.tasks import (
        implementation,
        planning,
        review,
        verification,
    )

    planning.register(registry)
    implementation.register(registry)
    verification.register(registry)
    review.register(registry)
