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
from .providers import (
    TaskProvider,
    StateProvider,
    CKGProvider,
    CPGProvider,
    TraceProvider,
    DeltaProvider,
    ProjectContextProvider,
    SprintProvider,
    DiffProvider,
    TestResultsProvider,
)

__all__ = [
    "ContextComposer",
    "ContextProviderRegistry",
    "ContextProvider",
    "QueryManager",
    "TaskProvider",
    "StateProvider",
    "CKGProvider",
    "CPGProvider",
    "TraceProvider",
    "DeltaProvider",
    "ProjectContextProvider",
    "SprintProvider",
    "DiffProvider",
    "TestResultsProvider",
    "ContextPiece",
    "ComposedContext",
    "ContextBudget",
]


def create_default_registry() -> ContextProviderRegistry:
    """Crea un registry con los providers estándar pre-registrados.

    Providers registrados:
    - project: contexto y convenciones del proyecto activo (desde project.json)
    - task: especificación de la tarea actual (desde plan.json)
    - state: estado del sprint actual (vía QueryManager)
    - ckg: Cognitive Knowledge Graph (patrones, decisiones, convenciones)
    - cpg: Code Property Graph (código fuente estructurado)
    - trace: historial de sesión (TraceGraph)
    - delta: contexto federado de tareas (DeltaGraph)
    - sprint: backlog completo del sprint (desde sprint-state.json)
    - diff: diff estructurado del código modificado (git diff)
    - test_results: resultados estructurados de tests (desde trace)
    """
    registry = ContextProviderRegistry()
    registry.register("project", ProjectContextProvider())
    registry.register("task", TaskProvider())
    registry.register("state", StateProvider())
    registry.register("ckg", CKGProvider())
    registry.register("cpg", CPGProvider())
    registry.register("trace", TraceProvider())
    registry.register("delta", DeltaProvider())
    registry.register("sprint", SprintProvider())
    registry.register("diff", DiffProvider())
    registry.register("test_results", TestResultsProvider())
    return registry
