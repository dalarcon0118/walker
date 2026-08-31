"""
contracts/tool.py — DTOs del contrato de tools que consume el agente IA.

Son el boundary DTO: lo que `tools/list` devuelve y lo que el LLM usa
para construir llamadas. Sin lógica — puros dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDefinition:
    """DTO — definición de un tool expuesto al agente (tools/list).

    Es la proyección del entity Tool hacia el wire protocol.
    """

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    @staticmethod
    def create(name: str, description: str, input_schema: dict | None = None) -> tuple["ToolDefinition | None", list[str]]:
        """Factory con validación básica. Returns (dto, errors)."""
        errors: list[str] = []
        if not name or not str(name).strip():
            errors.append("tool name cannot be empty")
        if not description or not str(description).strip():
            errors.append("tool description cannot be empty")
        if errors:
            return None, errors
        return ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema or {"type": "object"},
        ), []

    def to_dict(self) -> dict:
        """Proyección al schema JSON-RPC (tools/list result)."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
