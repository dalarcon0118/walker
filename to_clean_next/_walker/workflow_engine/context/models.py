"""Context Composer — domain models.

ContextPiece: una pieza atómica de contexto producida por un provider.
ComposedContext: el resultado final de la composición (system + layers + user).
ContextBudget: presupuesto de tokens para el ContextWindowManager.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
