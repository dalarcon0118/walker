"""
tasks/domain/ — Dominio del ServicesModule (ADR-068 §9.7 + corrección posterior).

Las tasks SÍ tienen dominio propio: los Value Objects de input validado
(ValidatedVO, TaskInput) — antes mal ubicados en shared/common/shared_domain/.

NOTA de fronteras:
  - TaskResult / ExecutionContext pertenecen al ENGINE
    (shared/core/engine/domain/execution.py): son el contrato del puerto
    `ActionCatalog` — el engine los declara y consume; las tasks los
    importan desde allí (sin inversión).
  - ProjectId pertenece a features/project/domain/.
"""
from __future__ import annotations

from .validated_vo import TaskInput, ValidatedVO

__all__ = ["TaskInput", "ValidatedVO"]