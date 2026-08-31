"""features/workflow/domain — Dominio del bounded context workflow (ADR-068).

Las reglas de las gates host-side del workflow del walker (que envuelven el
engine anémico). El engine NO conoce estas reglas (ADR-067 §2.3: host valida
fuera); este dominio las define.
"""
from __future__ import annotations

from features.workflow.domain.uncaptured import UncapturedStatus, UncapturedWorkResult

__all__ = ["UncapturedStatus", "UncapturedWorkResult"]