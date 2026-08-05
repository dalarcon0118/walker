"""
contracts/response.py — DTOs de respuesta del protocolo.

El boundary de lo que el agente recibe: Response, NextAction, ErrorResponse.
Sin lógica — puros dataclasses con return pattern en factories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NextAction:
    """DTO — acción sugerida al agente para continuar."""

    run: str
    description: str = ""

    def to_dict(self) -> dict:
        d: dict[str, str] = {"run": self.run}
        if self.description:
            d["description"] = self.description
        return d


@dataclass(frozen=True)
class Response:
    """DTO — respuesta canónica del protocolo.

    `type`: tipo de resultado (status, task_result, project_health, ...)
    `data`: payload
    `status_code`: 200/400/404/500
    `next_actions`: sugerencias al agente
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    status_code: int = 200
    message: str = ""
    next_actions: list[NextAction] = field(default_factory=list)

    @staticmethod
    def ok(
        type_name: str,
        data: dict | None = None,
        message: str = "",
        next_actions: list[NextAction] | None = None,
    ) -> "Response":
        return Response(
            type=type_name,
            data=data or {},
            status_code=200,
            message=message,
            next_actions=next_actions or [],
        )

    @staticmethod
    def error(
        type_name: str,
        message: str,
        status_code: int = 400,
        data: dict | None = None,
    ) -> "Response":
        return Response(
            type=type_name,
            data=data or {},
            status_code=status_code,
            message=message,
            next_actions=[],
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "type": self.type,
            "status_code": self.status_code,
        }
        if self.data:
            d["data"] = self.data
        if self.message:
            d["message"] = self.message
        if self.next_actions:
            d["next_actions"] = [na.to_dict() for na in self.next_actions]
        return d


@dataclass(frozen=True)
class ErrorResponse:
    """DTO — error estructurado del protocolo."""

    code: int
    message: str
    error_type: str = ""
    data: dict[str, Any] | None = None

    @staticmethod
    def create(code: int, message: str, error_type: str = "") -> tuple["ErrorResponse | None", list[str]]:
        """Factory con return pattern. Returns (error, errors)."""
        errors: list[str] = []
        if not message or not str(message).strip():
            errors.append("error message cannot be empty")
        if errors:
            return None, errors
        return ErrorResponse(code=code, message=message, error_type=error_type), []

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.error_type:
            d["type"] = self.error_type
        if self.data:
            d["data"] = self.data
        return d
