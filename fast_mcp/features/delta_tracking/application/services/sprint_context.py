"""application/services/sprint_context.py — resolución ÚNICA del sprint corriente.

El mismo proyecto podía reportar tres estados distintos al mismo tiempo:
`status` veía un sprint activo, `cockpit` imprimía "(sin sprint)" y
`workflow_state` pintaba `Goal:` vacío con `0/0` tareas. La causa no era el
storage: cada superficie resolvía "cuál es el sprint" con su propia política.

`resolve_sprint_context()` es la política canónica (ADR-WALKER-077/085):

    1. sprint explícito (si existe),
    2. sprint de la sesión (`session.spring_id`),
    3. sprint activo del proyecto,
    4. sprint en planning (determinista: `updated_at` desc, `spring_id` desc).

Nunca deriva el id del basename del `chain_dir`: el directorio es el proyecto,
no el sprint. `resolved_by` dice qué regla ganó; `session_linked` dice si el
sprint quedó atribuido a la sesión que consulta; `diagnostics` publica la
degradación (sesión no vinculada, sprint sin atribución, puntero roto) en vez de
esconderla.

Es una función de lectura: no crea, no corrige y no escribe estado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        try:
            return dict(value.to_dict())
        except Exception:  # noqa: BLE001 — una entidad ilegible es ausencia
            return {}
    return {}


def spring_id_of(spring: Any) -> str:
    """ID del sprint aceptando dict o entidad (`spring_id` o `id`)."""
    data = _as_dict(spring)
    if not data and hasattr(spring, "spring_id"):
        return str(getattr(spring, "spring_id", "") or "")
    return str(data.get("spring_id") or data.get("id") or "")


def _owns(spring: Any, session_id: str) -> bool:
    """¿El sprint está atribuido a la sesión? (`session_id` o `context.session_ids`)."""
    if not session_id:
        return False
    data = _as_dict(spring)
    if str(data.get("session_id") or "") == session_id:
        return True
    context = data.get("context") or {}
    sessions = context.get("session_ids") if isinstance(context, dict) else []
    return session_id in (sessions or [])


def _attributed(spring: Any) -> bool:
    data = _as_dict(spring)
    if str(data.get("session_id") or "").strip():
        return True
    context = data.get("context") or {}
    return bool(isinstance(context, dict) and context.get("session_ids"))


def _pick_planning(springs: list[Any]) -> Any | None:
    planning = [
        s for s in springs
        if str(_as_dict(s).get("status") or "") == "planning"
    ]
    if not planning:
        return None
    planning.sort(
        key=lambda s: (
            str(_as_dict(s).get("updated_at") or ""),
            spring_id_of(s),
        ),
        reverse=True,
    )
    return planning[0]


@dataclass(frozen=True)
class SprintContext:
    """Sprint resuelto + evidencia de cómo se resolvió.

    `spring` puede ser `{}` cuando el service es un doble de test que solo
    expone la sesión; en producción `get_spring` siempre devuelve la entidad.
    """

    spring: dict = field(default_factory=dict)
    specs: tuple[dict, ...] = ()
    spring_id: str = ""
    resolved_by: str = "none"  # explicit|session|active|planning|none
    session_id: str = ""
    session_linked: bool = False
    diagnostics: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.spring_id)

    @property
    def goal(self) -> str:
        return str(self.spring.get("goal") or "")

    @property
    def status(self) -> str:
        return str(self.spring.get("status") or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "spring_id": self.spring_id,
            "goal": self.goal,
            "status": self.status,
            "resolved_by": self.resolved_by,
            "session_id": self.session_id,
            "session_linked": self.session_linked,
            "spec_count": len(self.specs),
            "diagnostics": list(self.diagnostics),
        }


def resolve_sprint_context(
    service: Any,
    chain_dir: str = "",
    *,
    session_id: str = "",
    spring_id: str = "",
    allow_active_fallback: bool = True,
) -> SprintContext:
    """Resuelve el sprint corriente con la política única del proyecto.

    Args:
        service: `SprintService` (o doble con `get_session`/`get_spring`/
            `get_active_spring`/`list_springs`/`get_specs`).
        chain_dir: solo para diagnostics; el id NO se deriva del basename.
        session_id: sesión que consulta (SSOT de pertenencia).
        spring_id: sprint explícito pedido por el llamador.
        allow_active_fallback: si False, solo resuelve por sesión/explícito
            (para superficies que deben reportar estrictamente la sesión).
    """
    diagnostics: list[str] = []
    if service is None:
        return SprintContext(
            session_id=session_id,
            diagnostics=("sprint_service_unavailable",),
        )

    can_get_spring = callable(getattr(service, "get_spring", None))

    session_spring = ""
    if session_id and callable(getattr(service, "get_session", None)):
        try:
            record = _as_dict(service.get_session(session_id))
            session_spring = str(record.get("spring_id") or "")
        except Exception:  # noqa: BLE001 — la sesión ilegible se declara
            diagnostics.append("session_unreadable")

    spring: Any = None
    resolved_by = "none"
    id_hint = ""

    # 1. Sprint explícito.
    requested = str(spring_id or "").strip()
    if requested:
        if can_get_spring:
            try:
                spring = service.get_spring(requested)
            except Exception:  # noqa: BLE001
                spring = None
            if spring is None:
                diagnostics.append(f"unknown_spring:{requested}")
        else:
            id_hint = requested
            resolved_by = "explicit"
    if spring is not None:
        resolved_by = "explicit"

    # 2. Sprint de la sesión (ADR-WALKER-077: la pertenencia manda).
    if spring is None and session_spring:
        if can_get_spring:
            try:
                spring = service.get_spring(session_spring)
            except Exception:  # noqa: BLE001
                spring = None
            if spring is None:
                diagnostics.append(f"session_points_to_missing_spring:{session_spring}")
            else:
                resolved_by = "session"
        else:
            # Doble de test sin `get_spring`: el id de la sesión alcanza para
            # las superficies que solo publican el gate.
            id_hint = session_spring
            resolved_by = "session"

    # 3. Sprint activo del proyecto (solo sin sesión o sin sprint de sesión).
    if spring is None and not id_hint and allow_active_fallback and callable(
        getattr(service, "get_active_spring", None)
    ):
        try:
            spring = service.get_active_spring()
        except Exception:  # noqa: BLE001
            spring = None
            diagnostics.append("active_spring_unreadable")
        if spring is not None:
            resolved_by = "active"

    # 4. Planning determinista (mismo criterio en todas las superficies).
    if spring is None and not id_hint and callable(getattr(service, "list_springs", None)):
        try:
            spring = _pick_planning(list(service.list_springs(status="planning") or []))
        except Exception:  # noqa: BLE001
            spring = None
            diagnostics.append("spring_list_unreadable")
        if spring is not None:
            resolved_by = "planning"

    resolved_id = spring_id_of(spring) or id_hint
    if not resolved_id:
        return SprintContext(
            session_id=session_id,
            resolved_by="none",
            diagnostics=tuple(diagnostics),
        )

    if not _as_dict(spring) and can_get_spring:
        # `get_spring` falló pero el id existe: no se inventan goal/specs.
        diagnostics.append(f"spring_unreadable:{resolved_id}")

    specs: tuple[dict, ...] = ()
    if callable(getattr(service, "get_specs", None)):
        try:
            specs = tuple(
                s for s in (service.get_specs(resolved_id) or []) if isinstance(s, dict)
            )
        except Exception:  # noqa: BLE001 — se reporta como ausencia de specs
            diagnostics.append("specs_unreadable")

    linked = _owns(spring, session_id) or bool(
        session_id and session_spring and session_spring == resolved_id
    )
    if session_id and not linked:
        diagnostics.append("session_not_linked")
    if not _attributed(spring) and resolved_by in ("active", "planning"):
        diagnostics.append("sprint_unattributed")

    return SprintContext(
        spring=_as_dict(spring),
        specs=specs,
        spring_id=resolved_id,
        resolved_by=resolved_by,
        session_id=session_id,
        session_linked=linked,
        diagnostics=tuple(diagnostics),
    )


__all__ = ["SprintContext", "resolve_sprint_context", "spring_id_of"]
