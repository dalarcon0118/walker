"""Context Window Manager — gestión de presupuesto de tokens.

Fase 1: pasa todas las piezas sin filtrar (overflow_warning).
Fase 2+: priorización, resúmenes, descarte por budget.
"""

from __future__ import annotations

import logging

from .models import ContextPiece, ContextBudget

logger = logging.getLogger(__name__)


class ContextWindowManager:
    """Gestiona el presupuesto de tokens entre las piezas de contexto.

    Fase 1 — behavior simple: incluye todo, emite warning si excede.
    """

    def __init__(self):
        self._budget: ContextBudget | None = None

    @property
    def budget(self) -> ContextBudget:
        if self._budget is None:
            self._budget = ContextBudget()
        return self._budget

    @budget.setter
    def budget(self, value: ContextBudget) -> None:
        self._budget = value

    def allocate(self, pieces: list[ContextPiece]) -> AllocatedContext:
        """Distribuye el presupuesto entre las piezas de contexto.

        Fase 1: incluye todo (overflow_warning).
        Si alguna pieza fue omitida por error, se marca como skipped.

        Returns:
            AllocatedContext con las piezas incluidas, descartadas, y stats.
        """
        budget = self.budget
        available = budget.max_tokens - budget.reserve_for_response

        included: list[ContextPiece] = []
        excluded: list[ContextPiece] = []
        total_tokens = 0

        for piece in pieces:
            if piece.reason != "included":
                # Ya viene marcada como error/skipped desde el provider
                excluded.append(piece)
                continue

            if total_tokens + piece.tokens <= available:
                included.append(piece)
                total_tokens += piece.tokens
                piece.included = True
            elif budget.strategy == "overflow_warning":
                # Incluir igual pero advertir
                included.append(piece)
                total_tokens += piece.tokens
                piece.included = True
                piece.reason = "included_over_budget"
                logger.warning(
                    "Context window budget exceeded: %d/%d tokens used "
                    "(provider=%s, piece=%s)",
                    total_tokens,
                    available,
                    piece.provider,
                    piece.name,
                )
            elif budget.strategy == "truncate":
                excluded.append(piece)
                piece.included = False
                piece.reason = "budget_exceeded"
            else:
                # summarize_fallback o strict
                excluded.append(piece)
                piece.included = False
                piece.reason = f"budget_exceeded (strategy={budget.strategy})"

        return AllocatedContext(
            included=included,
            excluded=excluded,
            total_tokens=total_tokens,
            budget_used=total_tokens + budget.reserve_for_response,
            budget=budget,
        )


class AllocatedContext:
    """Resultado de la asignación de budget."""

    def __init__(
        self,
        included: list[ContextPiece],
        excluded: list[ContextPiece],
        total_tokens: int,
        budget_used: int,
        budget: ContextBudget,
    ):
        self.included = included
        self.excluded = excluded
        self.total_tokens = total_tokens
        self.budget_used = budget_used
        self.budget = budget
