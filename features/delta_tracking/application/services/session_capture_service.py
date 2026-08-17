"""application/services/session_capture_service.py — Captura de sesión (SPEC-005 #4).

Application Service del flujo de captura informal de tareas del agente
(extraído de features/main/task_execution/tasks/capture_task.py — ADR-051 G4).

Orquesta:
  1. Spring destino: explícito (sprint_id del workflow) o informal del día
     (session-YYYYMMDD). Los session logs NUNCA compiten como sprint activo:
     se mantienen en planning y siempre informales.
  2. Alta de la tarea como spec en el DeltaGraph (fuente única del spring).
  3. Captura de decisiones en el CKG via CkgApplicationService.

La task queda como adapter delgado: resuelve proyecto y traduce payload → este
servicio. Result pattern (convention-001): degrada con fallback explícito + log.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class SessionCaptureService:
    """Fachada pública del flujo de captura de sesión en el delta."""

    @staticmethod
    def capture_session_task(
        project_root: str,
        project_id: str,
        session_id: str,
        sprint_id: str,
        objective: str,
        domain: str = "",
        spec_type: str = "feature",
        result_state: str = "",
        files: list | None = None,
        description: str = "",
        decisions: list | None = None,
    ) -> dict[str, Any]:
        """Captura una tarea del agente en el spring destino (formal o informal).

        Returns:
            dict con {sprint_id, sprint_dir, created_sprint, spec_count,
            indexed_tasks, decisions_captured, decisions}.
        """
        from features.delta_tracking import SpringService

        objective = (objective or "").strip()
        files = files or []
        decisions = decisions or []

        # Spring destino: explícito (p. ej. sprint activo del workflow) o
        # informal del día (session-YYYYMMDD).
        explicit_sprint = bool((sprint_id or "").strip())
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        sprint_id = (sprint_id or "").strip() or f"session-{date_str}"
        sprint_dir = (
            os.path.join(project_root, "data", "spring", sprint_id)
            if explicit_sprint
            else SessionCaptureService._sprint_dir(project_root, date_str)
        )

        spring_svc = SpringService(project_root)

        # Crear/actualizar el nodo spring en el delta (fuente única).
        existing = spring_svc.get_spring(sprint_id)
        created = existing is None
        if created:
            spring_svc.create_spring(
                sprint_id,
                goal=f"Session log {sprint_id} — trabajo del agente",
                status="planning",
                project_id=project_id,
                session_id=session_id or "cli",
                informal=not explicit_sprint,
            )
        else:
            existing_data = dict(existing)
            if not explicit_sprint and existing_data.get("status") == "active":
                existing_data["status"] = "planning"
                existing_data["active"] = False
            if not explicit_sprint:
                existing_data.setdefault("informal", True)
                existing_data.setdefault("session_id", session_id or "cli")
            spring_svc.update_spring(sprint_id, existing_data)

        # Alta de la tarea como spec en el delta.
        result = spring_svc.add_spec(
            sprint_id,
            objective=objective,
            domain=domain,
            spec_type=spec_type,
            result_state=result_state,
            files=files,
            description=description,
        )

        # Captura de decisiones en el CKG (best-effort, nunca bloquea la captura).
        from features.cognitive_knowledge.application.services.ckg_service import (
            CkgApplicationService,
        )

        captured_decisions = CkgApplicationService.capture_decisions(
            decisions,
            project_root,
            result.get("spec_id", ""),
            session_id or "",
            sprint_id,
        )

        return {
            "sprint_id": sprint_id,
            "sprint_dir": sprint_dir,
            "created_sprint": created,
            "spec_count": len(spring_svc.get_specs(sprint_id)),
            "indexed_tasks": 1,
            "decisions_captured": len(captured_decisions),
            "decisions": captured_decisions,
        }

    @staticmethod
    def _sprint_dir(project_root: str, date_str: str) -> str:
        """Directorio del spring informal session-{date} bajo <root>/data/spring/."""
        spring_base = os.path.join(project_root, "data", "spring")
        os.makedirs(spring_base, exist_ok=True)
        return os.path.join(spring_base, f"session-{date_str}")
