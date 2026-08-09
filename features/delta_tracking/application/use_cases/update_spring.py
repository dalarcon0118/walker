"""application/use_cases/update_spring.py — UpdateSpringUseCase.

SRP: mutaciones del nodo spring (status, context, campos). Extraídas de
SpringService (set_spring_status/set_spring_context/update_spring) que
mezclaba query + command.
"""

from __future__ import annotations

from features.delta_tracking.domain.entities import Spring
from features.delta_tracking.application.ports.delta_repository import DeltaRepository

# Campos inmutables del nodo: no se pueden sobrescribir via update genérico.
_IMMUTABLE = ("type", "_id", "spring_id", "spec_key")


class UpdateSpringUseCase:
    """Muta el nodo spring del DeltaGraph (create-or-update por entidad)."""

    def __init__(self, repo: DeltaRepository):
        self._repo = repo

    def execute(self, spring_id: str, updates: dict) -> Spring | None:
        """Merge genérico de campos sobre la entidad persistida.

        Ignora campos inmutables y valores None. Sin la entidad → None
        (no crea: crear es responsabilidad de CreateSpringUseCase).
        """
        spring = self._repo.get_spring(spring_id)
        if spring is None:
            return None
        for k, v in updates.items():
            if k in _IMMUTABLE:
                continue
            if hasattr(spring, k) and v is not None:
                setattr(spring, k, v)
        self._repo.save_spring(spring)
        return spring

    def set_status(self, spring_id: str, status: str) -> Spring | None:
        """Cambia status del spring derivando el flag activo."""
        spring = self._repo.get_spring(spring_id)
        if spring is None:
            return None
        spring.status = status
        spring.active = status == "active"
        self._repo.save_spring(spring)
        return spring

    def set_context(
        self,
        spring_id: str,
        context: dict | None = None,
        **extra,
    ) -> Spring | None:
        """Establece el contexto del spring.

        Acepta kwargs sueltos (current_spec_index=..., current_task_index=...)
        además de un dict posicional: los callers del workflow (planning.py,
        AdvanceSpecIndexUseCase) invocaban con **kwargs, lo que lanzaba
        TypeError con la firma antigua de SpringService. El use case los
        fusiona en un solo dict de contexto.
        """
        updates = dict(context or {})
        updates.update(extra or {})
        if not updates:
            return self._repo.get_spring(spring_id)
        return self.execute(spring_id, {"context": updates})
