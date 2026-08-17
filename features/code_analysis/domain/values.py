"""domain/values.py — Value Objects del dominio CPG.

VOs que representan conceptos del dominio de code_analysis sin identidad propia.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True)
class CodePath:
    """Ruta de codigo fuente (archivo o directorio)."""
    path: str

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("CodePath no puede ser vacio")


@dataclass(frozen=True)
class CpgNodeId:
    """Identificador de nodo en el CPG."""
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("CpgNodeId no puede ser vacio")


@dataclass(frozen=True)
class CpgEdgeType:
    """Tipo de edge en el CPG."""
    value: str

    def __post_init__(self) -> None:
        valid = {
            "CALLS",
            "CONTAINS",
            "INHERITS",
            "IMPLEMENTS",
            "REFERENCES",
            "IMPORTS",
            "DEFINES",
        }
        if self.value not in valid:
            raise ValueError(f"CpgEdgeType invalido: {self.value}. Validos: {sorted(valid)}")


@dataclass(frozen=True)
class CodeLanguage:
    """Lenguaje de programacion."""
    value: str

    def __post_init__(self) -> None:
        valid = {"python", "javascript", "typescript", "java", "go", "rust"}
        if self.value.lower() not in valid:
            raise ValueError(f"CodeLanguage invalido: {self.value}. Validos: {sorted(valid)}")
