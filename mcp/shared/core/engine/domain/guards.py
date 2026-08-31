"""domain/guards.py — Guardas de visibilidad deterministas (estilo NestJS Guard).

Principio: guardas PURAS, sin side effects, sin try/catch.
Una guarda responde SIEMPRE una decisión booleana derivable de su entrada.

SSOT del scope check (SPEC-005 #7): una task con scope en {internal, system}
solo puede ser invocada por el motor interno (channel='system'). Las entradas
externas (CLI/MCP: channel cli/mcp) son bloqueadas.

Sin listas hardcodeadas: el scope vive en la metadata de la task registrada.
"""

from __future__ import annotations

from typing import Any

from ..domain.registry import TaskRegistry

__all__ = ["ScopeGuard"]


class ScopeGuard:
    """Guarda de visibilidad: check(scope, channel) determinista.

    No lanza: si la task no existe en el registry, devuelve True (permite)
    para que `execute()` emita el error estándar de 'no registrada'.
    """

    BLOCKED_SCOPES: frozenset[str] = frozenset({"internal", "system"})
    ALLOWED_CHANNELS: frozenset[str] = frozenset({"system"})

    @staticmethod
    def check(task_name: str, channel: str, registry: TaskRegistry) -> bool:
        """Devuelve True si la task PUEDE ejecutarse en el channel dado.

        Args:
            task_name: nombre de la task en el registry
            channel: canal de invocación (cli | mcp | system | workflow)
            registry: TaskRegistry con la metadata de la task

        Returns:
            bool: True = permitida; False = bloqueada por scope
        """
        info = registry.get_task_info(task_name)
        if info is None:
            return True  # task inexistente → execute() falla con error estándar
        scope = info.get("scope", "public")
        if scope in ScopeGuard.BLOCKED_SCOPES and channel not in ScopeGuard.ALLOWED_CHANNELS:
            return False
        return True

    @staticmethod
    def is_blocked(task_name: str, channel: str, registry: TaskRegistry) -> bool:
        """Inverso de check(): True si la task está bloqueada en el channel."""
        return not ScopeGuard.check(task_name, channel, registry)
