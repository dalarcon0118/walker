"""
domain/workflow_action.py — WorkflowAction + ActionCatalog (el puerto único).

SPEC-014 Fase 1 (ADR-WALKER-049): tasks y side effects son UNA capa — workflow
actions. Este módulo define:

  1. WorkflowAction: el modelo canónico de una acción del workflow.
     - kind: sub_state_action (se ejecuta en la parada, .action del grafo) o
       side_effect (se dispara al salir, .side_effects[] del grafo).
     - scope: public | internal | domain | system (SSOT de visibilidad).
     - handler: el callable (ctx, params) → Result.
     - compensate: name de la compensación declarada por el grafo (fuente
       única, SPEC-014 Fase 0) — vacío = sin compensación.
     - metadata: descripción/tags/schemas para el catálogo MCP/CLI.

  2. ActionCatalog: el puerto que el engine consume por DIP. Se construye UNA
     vez por la composition root; las vistas por scope/kind se DERIVAN del
     registro real — nunca listas hardcodeadas (SPEC-005 #7, patrón
     SystemTaskRegistry).

SRP: modelo puro + catálogo con Result pattern (returns) — sin IO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# ── Kinds de workflow action (SPEC-014 Fase 1) ───────────────────────────────
KIND_SUB_STATE_ACTION = "sub_state_action"  # .action del sub_state del grafo
KIND_SIDE_EFFECT = "side_effect"            # .side_effects[] del grafo

# Scopes válidos (SSOT de visibilidad, SPEC-005 #7)
SCOPES = ("public", "internal", "domain", "system")


@dataclass(frozen=True)
class WorkflowAction:
    """Una acción del workflow: task o side effect, unificados.

    El grafo las invoca por NAME en cualquiera de los dos puntos de
    invocación (.action o .side_effects[]) — una operación de negocio = un
    name (R3, ADR-049).
    """

    name: str
    kind: str = KIND_SUB_STATE_ACTION
    scope: str = "public"
    handler: Callable[..., Any] | None = None
    compensate: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "scope": self.scope,
            "compensate": self.compensate,
            "metadata": dict(self.metadata),
        }


class ActionCatalog:
    """Puerto único de resolución de workflow actions por nombre.

    El engine (state machine puro) NO sabe qué hace una acción: solo resuelve
    name → WorkflowAction. La composition root implementa el puerto poblándolo
    desde los registros reales (TaskRegistry + SideEffectEngine) — nunca
    duplica el handler, solo INDEXA la metadata para la vista unificada.

    Vistas derivadas del registro real (sin listas hardcodeadas):
      - by_kind(kind)      → sub_state_action | side_effect
      - by_scope(scope)    → public | internal | domain | system
      - names()            → todos los names
    """

    def __init__(self) -> None:
        self._actions: dict[str, WorkflowAction] = {}

    def register(
        self,
        name: str,
        kind: str = KIND_SUB_STATE_ACTION,
        scope: str = "public",
        handler: Callable[..., Any] | None = None,
        compensate: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Registra/sobreescribe una acción. Idempotente por name."""
        self._actions[name] = WorkflowAction(
            name=name,
            kind=kind,
            scope=scope,
            handler=handler,
            compensate=compensate,
            metadata=dict(metadata or {}),
        )

    def resolve(self, name: str) -> WorkflowAction | None:
        return self._actions.get(name)

    def names(self) -> list[str]:
        return sorted(self._actions.keys())

    def by_kind(self, kind: str) -> list[WorkflowAction]:
        return [a for a in self._actions.values() if a.kind == kind]

    def by_scope(self, scope: str) -> list[WorkflowAction]:
        return [a for a in self._actions.values() if a.scope == scope]

    def public_names(self) -> list[str]:
        """Names de scope public — lo que CLI/MCP pueden invocar."""
        return sorted(a.name for a in self._actions.values() if a.scope == "public")

    def domain_names(self) -> list[str]:
        """Names de scope internal/domain — lo que invoca el workflow."""
        return sorted(
            a.name for a in self._actions.values() if a.scope in ("internal", "domain")
        )

    def execute_action(
        self,
        name: str,
        ctx: Any,
        params: dict | None = None,
    ) -> dict | None:
        """Ejecuta una acción resuelta del catálogo (SPEC-014 Fase 1).

        Puerta única: el engine resuelve name → WorkflowAction → handler(ctx, params).
        Retorna dict con el resultado normalizado, o None si la acción no existe
        o el handler no está registrado.
        """
        action = self.resolve(name)
        if action is None or action.handler is None:
            return None
        return action.handler(ctx, params or {})

    def to_dict(self) -> dict:
        return {
            "total": len(self._actions),
            "actions": [a.to_dict() for a in sorted(self._actions.values(), key=lambda x: x.name)],
        }
