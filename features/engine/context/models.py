"""Backward-compat shim — el contrato vive ahora en libs/context/models.py.

Re-export puro para no romper los imports internos del engine ni de tests.
Nueva lógica DEBE ir a libs/context/ (DIP): este módulo solo re-exporta.
"""
from __future__ import annotations

from libs.context.models import ContextPiece, ContextBudget, ComposedContext

__all__ = ["ContextPiece", "ContextBudget", "ComposedContext"]
