"""shared/common/context — Contrato neutral de providers de contexto (DIP).

El contrato de plugin (ContextProvider ABC, ContextPiece/ContextBudget/
ComposedContext y los formatters) vive en libs para que las features de
negocio dependan de la capa neutral y NO de features/engine/context.

Regla DIP: libs NO importa features. Este paquete solo usa stdlib.
"""
from __future__ import annotations

from .models import ContextPiece, ContextBudget, ComposedContext
from .contracts import ContextProvider
from .format import (
    fmt_tokens,
)

__all__ = [
    "ContextPiece",
    "ContextBudget",
    "ComposedContext",
    "ContextProvider",
    "fmt_tokens",
]
