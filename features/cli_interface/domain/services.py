"""
domain/services.py — ToolRegistry (aggregate SSOT del Bounded Context "Protocol").

Única fuente de verdad del contrato de tools:
  - register(): registra Tool validando unicidad (bloquea shadowing)
  - list_tools(): proyección al schema que ve el agente (tools/list)
  - dispatch(): invoca el handler (tools/call) — mismo registry, cero drift

Return-pattern: métodos devuelven (result, errors).
"""

from __future__ import annotations

from typing import Any, Callable

from .entities import Tool, Resource
from .values import ToolName, UriPattern
from .errors import ToolAlreadyExistsError, UnknownToolError


class ToolRegistry:
    """Aggregate — registro de tools con invarianzas de unicidad."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._resources: list[Resource] = []
        self._events: list[object] = []

    # ── Registro (SSOT) ──

    def register(
        self,
        name: str,
        description: str,
        handler: Callable[[dict], dict],
        input_schema: dict | None = None,
    ) -> tuple[Tool | None, list[str]]:
        """Registra un tool. Returns (tool, errors)."""
        # Validar nombre
        tool, errors = Tool.create(
            name=name, description=description,
            input_schema=input_schema, handler=handler,
        )
        if errors:
            return None, errors

        # Invarianza: unicidad — bloquea shadowing
        key = tool.name.value  # type: ignore[union-attr]
        if key in self._tools:
            return None, [str(ToolAlreadyExistsError(key))]

        self._tools[key] = tool  # type: ignore[assignment]
        from .events import ToolRegistered

        self._events.append(
            ToolRegistered(tool_name=key)
        )
        return tool, []

    def register_resource(
        self,
        uri_pattern: str,
        name: str,
        description: str,
        mime_type: str,
        handler: Callable[[dict], dict],
    ) -> tuple[Resource | None, list[str]]:
        """Registra un recurso dinámico. Returns (resource, errors)."""
        pattern, errors = UriPattern.create(uri_pattern)
        if errors:
            return None, errors
        resource = Resource(
            uri_pattern=pattern,  # type: ignore[arg-type]
            name=name,
            description=description,
            mime_type=mime_type,
            handler=handler,
        )
        self._resources.append(resource)
        return resource, []

    # ── Consulta (SSOT del contrato) ──

    def list_tools(self) -> list[dict]:
        """Proyección al schema que ve el agente (tools/list)."""
        return [t.to_definition() for t in sorted(
            self._tools.values(), key=lambda t: t.name.value
        )]

    def list_resources(self) -> list[dict]:
        return [
            {
                "uri": r.uri_pattern.value,
                "name": r.name,
                "description": r.description,
                "mimeType": r.mime_type,
            }
            for r in self._resources
        ]

    def get_handler(self, name: str) -> Callable[[dict], dict] | None:
        tool = self._tools.get(name)
        return tool.handler if tool else None

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    # ── Dispatch (SSOT del handler) ──

    def dispatch(self, name: str, args: dict) -> tuple[dict, list[str]]:
        """Invoca el handler del tool. Returns (result, errors)."""
        tool = self._tools.get(name)
        if tool is None:
            return {}, [str(UnknownToolError(name))]
        try:
            result = tool.handler(args)
            return result, []
        except Exception as e:
            from .events import ToolFailed

            self._events.append(
                ToolFailed(tool_name=name, error_type=type(e).__name__)
            )
            return {}, [str(e)]

    # ── Eventos ──

    def collect_events(self) -> list[object]:
        events = list(self._events)
        self._events.clear()
        return events
