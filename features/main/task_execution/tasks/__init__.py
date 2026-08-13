"""
features/task_execution/tasks/ — Implementaciones concretas de tareas.

Cada modulo expone una funcion register(registry) que registra una o
mas tareas en el TaskRegistry del engine. El discovery lo ejecuta
features/main (composition root final, ADR-045) via register_task_execution_tasks.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

from returns.pipeline import is_successful
from returns.result import safe

from core.engine.infrastructure.task_registry import InMemoryTaskRegistry

logger = logging.getLogger(__name__)


def register_task_execution_tasks(registry: InMemoryTaskRegistry) -> None:
    """Registra todas las tasks de task_execution en el registry.

    Result pattern (convention-001): por módulo, el core @safe nunca lanza;
    ante fallo de registro se loggea warning y el resto del package sigue
    registrándose.
    """
    for _, name, _ in pkgutil.iter_modules(__path__):
        if name.startswith("_"):
            continue
        outcome = _register_module_core(name, registry)
        if not is_successful(outcome):
            error = outcome.failure()
            logger.warning(
                "task_execution: no se pudo registrar task '%s' (%s: %s)",
                name,
                type(error).__name__,
                error,
            )


@safe
def _register_module_core(name: str, registry: InMemoryTaskRegistry) -> None:
    """Core puro: importa un módulo de tasks y llama a su register().

    Result pattern (convention-001): @safe nunca lanza; el fallo se captura
    como Failure y lo decide el caller (loggea warning y sigue).
    """
    module = importlib.import_module(f"{__name__}.{name}")
    register_fn = getattr(module, "register", None)
    if register_fn:
        register_fn(registry)
