"""features/workflow/domain/uncaptured.py — Gate de trabajo sin capturar (dominio).

Reglas de la gate host-side de uncaptured_work (ADR-068): el motor es anémico
(ADR-067 §2.3) y NO conoce este negocio; el host valida externamente y decide
si bloquea el cierre del sprint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UncapturedStatus(str, Enum):
    """Estado de la detección de trabajo sin capturar."""

    CLEAN = "clean"          # todo el trabajo modificado está cubierto
    UNCAPTURED = "uncaptured"  # hay archivos modificados sin especificación
    ERROR = "error"          # la detección falló (fail-open, visible)


@dataclass(frozen=True)
class UncapturedWorkResult:
    """Resultado de la detección de trabajo sin capturar."""

    status: UncapturedStatus = UncapturedStatus.CLEAN
    impacted_files: list[str] = field(default_factory=list)
    covered_files: list[str] = field(default_factory=list)
    uncaptured_files: list[str] = field(default_factory=list)
    spring_id: str = ""
    has_spring: bool = False
    error: str = ""

    @property
    def should_block(self) -> bool:
        """True solo cuando la detección se completó Y hay trabajo sin capturar."""
        return (
            self.status == UncapturedStatus.UNCAPTURED
            and len(self.uncaptured_files) > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "should_block": self.should_block,
            "impacted_files": list(self.impacted_files),
            "covered_files": list(self.covered_files),
            "uncaptured_files": list(self.uncaptured_files),
            "spring_id": self.spring_id,
            "has_spring": self.has_spring,
            "error": self.error,
        }

    def gate_message(self) -> str:
        """Mensaje accionable cuando el gate bloquea el cierre del sprint."""
        if not self.should_block:
            return ""
        files_txt = "\n".join(f"    • {f}" for f in self.uncaptured_files[:15])
        extra = (
            f"\n    (+{len(self.uncaptured_files) - 15} más)"
            if len(self.uncaptured_files) > 15
            else ""
        )
        sprint_txt = f" (sprint {self.spring_id})" if self.spring_id else ""
        return (
            "GATE TRABAJO SIN CAPTURAR: hay cambios en el código que no están "
            "registrados como tareas en el session-log"
            f"{sprint_txt}. El cierre del sprint se BLOQUEA para no perder trabajo:\n"
            f"{files_txt}{extra}\n"
            "Acción: invoca capture_task para persistir este trabajo y vuelve a "
            "submit.\n"
            '  walker <project>/capture_task \'{"objective":"...","files":["<archivo>"]}\''
        )