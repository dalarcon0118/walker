"""
infrastructure/contracts.py — Contratos REST del CLI (analogía Express/NestJS).

El walker se consume como una ruta REST: /recurso/recurso2/recurso3.

  Request  → objeto estándar que normaliza el input del agente:
             method, path, params, query, body, flags.
  Response → objeto coherente que el agente siempre recibe:
             status_code, message (qué pasó), data (resultado)
             y next_actions (qué hacer ahora, con comandos exactos).

SSOT: todos los handlers devuelven Response; output.py lo renderiza.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Request:
    """Request REST estándar del walker.

    Analogía: Express req → { method, path, params, query, body, headers }.
    Aquí 'flags' juega el rol de headers/query extra del CLI.
    """
    method: str = "GET"
    path: str = ""
    params: dict[str, str] = field(default_factory=dict)   # :params del path
    query: dict[str, str] = field(default_factory=dict)    # ?key=value
    body: dict[str, Any] = field(default_factory=dict)     # JSON body
    flags: dict[str, Any] = field(default_factory=dict)    # --key value

    @property
    def project_id(self) -> str:
        return self.params.get("projectId", "")

    @property
    def task_name(self) -> str:
        return self.params.get("taskName", self.flags.get("task", ""))

    @property
    def sprint_id(self) -> str:
        return self.flags.get("sprint_id", "")

    @property
    def positional(self) -> list[str]:
        """Args posicionales (sin --): gate, body JSON, task name, etc."""
        return list(self.flags.get("positional", []) or [])

    @property
    def gate(self) -> str:
        """Gate para transition: params > flag > primer positional."""
        return (
            self.params.get("gate", "")
            or self.flags.get("gate", "")
            or (self.positional[0] if self.positional else "")
        )

    @property
    def repo_path(self) -> str:
        """Repo path resuelto (SSOT: handlers lo setean en flags)._"""
        return self.flags.get("_repo_path", "")

    def set_repo_path(self, path: str) -> None:
        """SSOT: almacena el repo_path resuelto para los handlers."""
        if path:
            self.flags["_repo_path"] = path

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Request {self.method} /{self.path} params={self.params} query={self.query}>"


@dataclass
class NextAction:
    """Una acción sugerida al agente: comando exacto + justificación."""
    command: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {"command": self.command, "reason": self.reason}


@dataclass
class Response:
    """Response REST estándar del walker.

    Analogía: Express res → res.status(200).json({ body, links }).
    La diferencia clave: SIEMPRE incluye next_actions para que el agente
    sepa qué hacer después sin adivinar.

    Campos:
      status_code : 200/400/404/500 (exit code del CLI lo refleja)
      message     : resumen legible de lo que pasó (qué pasó)
      data        : resultado detallado (formateado según type)
      next_actions: qué hacer ahora, con comandos exactos
      errors      : detalles de validación (opcional)
    """
    status_code: int = 200
    type: str = "ok"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    next_actions: list[NextAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ── Constructores de conveniencia ──

    @classmethod
    def ok(
        cls,
        type: str,
        message: str = "",
        data: dict | None = None,
        next_actions: list[NextAction] | list[dict] | None = None,
    ) -> "Response":
        return cls(
            status_code=200,
            type=type,
            message=message or "OK",
            data=data or {},
            next_actions=_coerce_actions(next_actions),
        )

    @classmethod
    def error(
        cls,
        message: str,
        status_code: int = 400,
        errors: list[str] | None = None,
        next_actions: list[NextAction] | list[dict] | None = None,
    ) -> "Response":
        return cls(
            status_code=status_code,
            type="error",
            message=message,
            errors=errors or [],
            next_actions=_coerce_actions(next_actions),
        )

    @property
    def success(self) -> bool:
        return self.status_code < 400

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "type": self.type,
            "status_code": self.status_code,
            "message": self.message,
            "data": self.data,
        }
        if self.next_actions:
            d["next_actions"] = [a.to_dict() for a in self.next_actions]
        if self.errors:
            d["errors"] = self.errors
        return d


def _coerce_actions(
    actions: list[NextAction] | list[dict] | None,
) -> list[NextAction]:
    """Acepta NextAction o dicts crudos {'command':..., 'reason':...}."""
    if not actions:
        return []
    out = []
    for a in actions:
        if isinstance(a, NextAction):
            out.append(a)
        elif isinstance(a, dict):
            out.append(NextAction(command=a.get("command", ""), reason=a.get("reason", "")))
    return out


def action(command: str, reason: str = "") -> NextAction:
    """Helper para construir NextAction conciso."""
    return NextAction(command=command, reason=reason)
