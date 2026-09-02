"""shared_kernel.common_context — Contrato neutral de contexto (port autónomo).

Port de `mcp/shared/common/context/` (ADR-WALKER-071 §0 #4/#5): piezas de
contexto producidas por providers y el contrato que las features implementan
(cpg, ckg, trace, state...). Vive en el kernel (capa neutral, DIP): las
features de negocio dependen de este contrato, NUNCA del engine. Solo usa
stdlib + dataclasses.

Este paquete NO importa nada de `mcp/` (ni app/, features/, shared/,
protocols/): el contrato está portado aquí, no se llama de forma remota.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ContextPiece",
    "ContextBudget",
    "ComposedContext",
    "ContextProvider",
    "fmt_tokens",
]


@dataclass
class ContextPiece:
    """Una pieza atómica de contexto producida por un provider.

    Attributes:
        provider: nombre del provider que la produjo (ej: "sprint_state", "ckg")
        name: nombre descriptivo (ej: "nodes for task TASK-012")
        instructions: texto instructivo sobre cómo usar esta capa (va al system prompt).
                      Vacío si el provider no tiene instrucciones particulares.
        content: datos concretos de la capa (va al context layer).
                 Vacío si el provider solo entrega instrucciones sin data.
        tokens: costo estimado en tokens (instrucciones + contenido).
        priority: 0-100 (menor = más importante). 0-9: crítico, siempre incluido.
        included: True si esta pieza entró al mensaje final.
        reason: por qué fue incluida o descartada ("included", "budget_exceeded",
                "error", "instructions_only", "skipped").
        metadata: datos adicionales del provider para debugging/provenance.
    """

    provider: str
    name: str = ""
    instructions: str = ""
    content: str = ""
    tokens: int = 0
    priority: int = 50
    included: bool = True
    reason: str = "included"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextBudget:
    """Presupuesto de tokens para la ventana de contexto.

    Attributes:
        max_tokens: tokens máximos para el mensaje completo (ej: 100000).
        reserve_for_response: tokens reservados para la respuesta del LLM.
        strategy: qué hacer cuando se excede el budget.
            "overflow_warning" — incluir todo y advertir
            "summarize_fallback" — resumir fuentes de baja prioridad
            "truncate" — cortar fuentes de baja prioridad
            "strict" — fallar si se excede
    """

    max_tokens: int = 100000
    reserve_for_response: int = 15000
    strategy: str = "overflow_warning"


@dataclass
class ComposedContext:
    """Resultado de la composición, listo para enviar al LLM.

    Attributes:
        system_message: focus.role + focus.constraints + context_priority
                        + instructions de cada provider incluido.
        context_layers: content de cada provider incluido, formateado por capa.
        user_message: prompt template resuelto + user_input.
        provenance: lista de ContextPiece con el detalle de qué se usó/descartó.
        budget_used: tokens totales usados (estimado).
        budget: presupuesto aplicado.
    """

    system_message: str = ""
    context_layers: list[str] = field(default_factory=list)
    user_message: str = ""
    provenance: list[ContextPiece] = field(default_factory=list)
    budget_used: int = 0
    budget: ContextBudget | None = None
    context_schemas: dict[str, Any] = field(default_factory=dict)


class ContextProvider(ABC):
    """Fuente de contexto configurable por nodo.

    Cada provider representa una CAPA de datos que el nodo puede declarar.
    No asume inteligencia en el workflow — el provider se PRESENTA al LLM
    como una capa con contenido y propósito.

    Dos salidas:
      - instructions(): texto que va al system prompt. Declara qué capa es,
        qué contiene, cómo usarla, con qué combinarse.
      - collect(): los datos concretos de la capa para el context layer.
    """

    @abstractmethod
    def instructions(self, config: dict, ctx: Any) -> str:
        """Declaración de capa para el LLM.

        Retorna string vacío si no hay instrucciones particulares.
        """
        return ""

    @abstractmethod
    def collect(self, config: dict, ctx: Any, query_manager: Any = None) -> ContextPiece | None:
        """Recupera los datos concretos de esta capa.

        Retorna None si esta capa solo existe para dar instrucciones
        y no tiene datos que entregar.
        """
        ...

    def schema(self) -> dict:
        """JSON Schema de la config que acepta este provider."""
        return {}

    def output_schema(self) -> dict:
        """JSON Schema de la CAPA de datos que produce este provider.

        Default fail-open {} (provider sin DTO declarado).
        """
        return {}


def fmt_tokens(content: str) -> int:
    """Estimación de tokens (~4 chars/token)."""
    return len(content) // 4