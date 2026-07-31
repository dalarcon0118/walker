"""Context Composer — composición declarativa de contexto para LLM.

Propósito:
  Reemplazar el sistema actual de prompts planos (template + placeholders)
  por un sistema donde cada nodo DECLARA qué capas de contexto necesita
  y el ContextComposer las ensambla desde providers registrables.

  Cada provider delega en QueryManager para obtener datos estructurados
  de las capas: CPG (código), CKG (conocimiento), Trace (historial),
  State (estado del sprint), Delta (tareas históricas).

Uso:
    from .composer import ContextComposer
    from .registry import ContextProviderRegistry
    from .providers import StateProvider

    registry = ContextProviderRegistry()
    registry.register("state", StateProvider())

    composer = ContextComposer(registry)
    result = composer.compose(node_config, prompt_template, execution_context)
"""
from __future__ import annotations

from .composer import ContextComposer
from .models import ContextPiece, ComposedContext, ContextBudget
from .registry import ContextProviderRegistry, ContextProvider
from .query_manager import QueryManager
from .providers import DiffProvider, TestResultsProvider
from .format import (
    fmt_tokens,
    format_ckg_results,
    format_cpg_results,
    format_trace_results,
    format_state_result,
    format_delta_results,
)

__all__ = [
    "ContextComposer",
    "ContextProviderRegistry",
    "ContextProvider",
    "QueryManager",
    "DiffProvider",
    "TestResultsProvider",
    "fmt_tokens",
    "format_ckg_results",
    "format_cpg_results",
    "format_trace_results",
    "format_state_result",
    "format_delta_results",
    "ContextPiece",
    "ComposedContext",
    "ContextBudget",
]


def create_default_registry() -> ContextProviderRegistry:
    """Crea un registry con los providers de UTILIDAD GENÉRICA del engine.

    PRINCIPIO: El engine es un mecanismo puro. SOLO registra providers
    de utilidad que no pertenecen a ninguna feature de negocio (diff,
    test_results). Los providers de negocio (cpg, ckg, business, state,
    delta, trace, project) se registran EXTERNAMENTE por el bootstrap
    via features/<feature>/context/providers.py:register_providers().

    Uso completo (bootstrap): build_context_registry() en bootstrap.py.
    """
    registry = ContextProviderRegistry()
    registry.register("diff", DiffProvider())
    registry.register("test_results", TestResultsProvider())
    return registry
