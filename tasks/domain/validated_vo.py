"""
tasks/domain/validated_vo.py — Value Object validado (framework de tasks).

PROPÓSITO: que la lógica de negocio (tasks) reciba INPUTS LIMPIOS Y
VALIDADOS, no dicts crudos. Un ValidatedVO:
  - Se construye SIEMPRE validado (no puede existir en estado inválido).
  - `from_dict(raw)` valida y retorna un Result (ok: VO | error: msg),
    nunca lanza excepciones de validación (el caller decide).
  - Es inmutable (frozen dataclass).
  - `to_dict()` serializa para persistencia/transporte.

Composición con monadas: `from_dict` retorna un Result tipado, que
encadena con TaskResult.map/and_then sin if/else.

Uso:
    class ReviewInput(ValidatedVO):
        files: list[str] = []
        thresholds: dict | None = None

    r = ReviewInput.from_dict({"files": [...], "thresholds": {...}})
    if r.ok:
        task(r.data)      # input limpio y validado
    else:
        log(r.error)

DOMINIO de tasks (ADR-068 §9.7): las tasks SÍ tienen dominio propio —
TaskInput/ValidatedVO viven aquí, en tasks/domain/ (antes estaban en
shared/common/shared_domain/, cajón de sastre). TaskResult/ExecutionContext
son contrato del ENGINE (shared/core/engine/domain/execution.py) porque el
engine los declara en su puerto ActionCatalog.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.core.engine.domain.execution import TaskResult


@dataclass(frozen=True)
class ValidatedVO:
    """Base para Value Objects validados (inmutable).

    Subclases declaran campos tipados. La validación se hace en
    `_validate()` (opcional) y el parseo de tipos en `_parse_field`.
    """

    # ── Construcción validada ────────────────────────────────────

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> TaskResult["ValidatedVO"]:
        """Construye el VO desde un dict crudo, validando.

        Returns:
            TaskResult.success(vo) si válido; TaskResult.failure(msj) si no.
        """
        raw = raw or {}
        try:
            vo = cls._build_from_dict(raw)
        except Exception as e:
            return TaskResult.failure(f"{cls.__name__}: {str(e)[:200]}")

        # Validación semántica (opcional, definida por la subclase)
        err = vo._validate()
        if err:
            return TaskResult.failure(f"{cls.__name__}: {err}")
        return TaskResult.success(vo)

    @classmethod
    def _build_from_dict(cls, raw: dict[str, Any]) -> "ValidatedVO":
        """Construye el VO parseando tipos. Las subclases pueden overridear."""
        kwargs = {}
        for field_name, field_type in cls.__annotations__.items():
            if field_name.startswith("_") or field_name in ("from_dict", "to_dict"):
                continue
            if field_name in raw:
                kwargs[field_name] = raw[field_name]
        return cls(**kwargs)

    # ── Validación (overridear en subclase si hace falta) ────────

    def _validate(self) -> str:
        """Retorna mensaje de error si el VO es inválido, o '' si es válido."""
        return ""

    # ── Serialización ────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serializa el VO a dict (para persistencia/transporte)."""
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


@dataclass(frozen=True)
class TaskInput(ValidatedVO):
    """Input base de una task del workflow.

    Todo input de task DEBERÍA heredar de TaskInput (o ValidatedVO) para
    que la lógica de negocio reciba entidades limpias y validadas.
    """

    task_id: str = ""