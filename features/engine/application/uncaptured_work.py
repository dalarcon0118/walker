"""application/uncaptured_work.py — Detector de trabajo sin capturar.

Anti-pérdida: el watcher registra eventos file_impact en el TraceGraph
cuando detecta archivos modificados, pero NADIE convierte esa evidencia en
specs del session-log salvo que el agente invoque capture_task
explícitamente. Este detector cierra esa brecha: compara los archivos
modificados (file_impact) contra los archivos cubiertos por las specs del
spring activo (delta). Si hay archivos tocados sin spec que los cubra →
trabajo sin capturar.

Se usa como GATE al salir del sprint (SPRINT.REVIEW/CLOSE_CONFIRM): el
engine bloquea el cierre si hay cambios sin capturar, y sugiere capture_task.

Contrato del resultado (UncapturedWorkResult):
  - status = "clean"      → detección completada, sin trabajo sin capturar.
  - status = "uncaptured" → detección completada, hay trabajo sin capturar
                            (el gate DEBE bloquear).
  - status = "error"      → la detección NO se pudo completar (fail-open
                            honesto: el gate NO bloquea, pero el error es
                            visible y trazable, no disfrazado de éxito).

SRP: Solo detecta. No muta estado, no registra nada.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UncapturedStatus(str, Enum):
    CLEAN = "clean"
    UNCAPTURED = "uncaptured"
    ERROR = "error"


@dataclass(frozen=True)
class UncapturedWorkResult:
    """Resultado de la detección de trabajo sin capturar.

    Objeto de dominio reutilizable: el consumidor (gate del engine, CLI,
    MCP) evalúa status + listas sin conocer el origen de los datos.
    """

    status: UncapturedStatus = UncapturedStatus.CLEAN
    impacted_files: list[str] = field(default_factory=list)
    covered_files: list[str] = field(default_factory=list)
    uncaptured_files: list[str] = field(default_factory=list)
    spring_id: str = ""
    has_spring: bool = False
    error: str = ""

    # ── Helpers de decisión ─────────────────────────────────────────

    @property
    def should_block(self) -> bool:
        """True solo cuando la detección se completó Y hay trabajo sin capturar."""
        return (
            self.status == UncapturedStatus.UNCAPTURED
            and len(self.uncaptured_files) > 0
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialización para API/CLI (mantiene el contrato por campos)."""
        return {
            "status": self.status.value,
            "should_block": self.should_block,
            "impacted_files": list(self.impacted_files),
            "covered_files": list(self.covered_files),
            "uncaptured_files": list(self.uncaptured_files),
            "spring_id": self.spring_id,
            "has_spring": self.has_spring,
            "error": self.error,
        }

    def gate_message(self) -> str:
        """Mensaje accionable cuando el gate bloquea el cierre del sprint."""
        if not self.should_block:
            return ""
        files_txt = "\n".join(f"    • {f}" for f in self.uncaptured_files[:15])
        extra = (
            f"\n    (+{len(self.uncaptured_files) - 15} más)"
            if len(self.uncaptured_files) > 15
            else ""
        )
        sprint_txt = f" (sprint {self.spring_id})" if self.spring_id else ""
        return (
            "GATE TRABAJO SIN CAPTURAR: hay cambios en el código que no están "
            "registrados como tareas en el session-log"
            f"{sprint_txt}. El cierre del sprint se BLOQUEA para no perder trabajo:\n"
            f"{files_txt}{extra}\n"
            "Acción: invoca capture_task para persistir este trabajo y vuelve a "
            "submit.\n"
            '  walker <project>/capture_task \'{"objective":"...","files":["<archivo>"]}\''
        )


def _normalize(path: str) -> str:
    """Normaliza una ruta para comparar: relativa, con / y sin prefijos cognitivos."""
    p = str(path or "").replace("\\", "/").strip()
    for marker in (".cognitive/projects/", "projects/"):
        idx = p.find(marker)
        if idx >= 0:
            p = p[idx + len(marker) :]
            parts = p.split("/", 1)
            if len(parts) == 2:
                p = parts[1]
            break
    return p.lstrip("/")


def _path_matches(file_path: str, covered: list[str]) -> bool:
    """¿El archivo está cubierto por alguna ruta declarada en specs?

    Cobertura por igualdad o por prefijo de directorio:
      - file == covered_path
      - file.startswith(covered_path + "/")  (archivo dentro del dir declarado)
    """
    fp = _normalize(file_path)
    if not fp:
        return False
    for c in covered:
        cp = _normalize(c)
        if not cp:
            continue
        if fp == cp or fp.startswith(cp.rstrip("/") + "/"):
            return True
    return False


def detect_uncaptured_work(chain_dir: str) -> UncapturedWorkResult:
    """Detecta trabajo sin capturar para el sprint activo.

    Args:
        chain_dir: directorio del sprint (workflow).

    Returns:
        UncapturedWorkResult (ver contrato en el docstring del módulo).
    """
    try:
        from libs.path_resolver import resolve_project_root
        from ..infrastructure.trace_log import TraceLogger
        from features.delta_tracking.application.spring_graph_service import (
            SpringGraphService,
        )

        # Resolver el project_root desde el project.json local (mismo
        # mecanismo que TraceLogger.replay). NO usar ProjectService.resolve_paths
        # porque pasa por el registry global y falla para proyectos no
        # registrados / fixtures aislados.
        project_root = resolve_project_root(chain_dir)
        data_dir = os.path.join(project_root, "data")

        # 1. Archivos modificados (dos fuentes complementarias):
        #    a. change_detected.json del watcher (estado ACTUAL acumulado)
        #    b. eventos file_impact del TraceGraph (historial del watcher)
        impacted: list[str] = []
        _change_file = os.path.join(data_dir, "change_detected.json")
        if os.path.isfile(_change_file):
            try:
                import json as _json

                with open(_change_file) as _f:
                    _cd = _json.load(_f)
                for f in _cd.get("files_changed", []) or []:
                    if f and f not in impacted:
                        impacted.append(f)
            except Exception:
                pass  # change_detected corrupto/ausente → seguir con trace

        tracer = TraceLogger()
        events = tracer.replay(project_root)
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if ev.get("event_type") != "file_impact":
                continue
            files = ev.get("files") or []
            for f in files:
                if f and f not in impacted:
                    impacted.append(f)

        # 2. Archivos cubiertos por specs del spring EN CURSO.
        #    Usar el spring del chain_dir actual (get_spring_id) con fallback
        #    al activo global: get_active_spring() es ambiguo cuando hay
        #    varios springs con status "active".
        spring_svc = SpringGraphService(project_root)
        covered: list[str] = []
        spring = None
        if spring_svc is not None:
            spring_id = spring_svc.get_spring_id(chain_dir)
            spring = spring_svc.get_spring(spring_id) or spring_svc.get_active_spring()
            if spring:
                spring_id = (
                    spring.get("spring_id")
                    or spring.get("_id")
                    or spring.get("sprint_id")
                    or spring_id
                )
                specs = spring_svc.get_specs(spring_id)
                for spec in specs:
                    for f in spec.get("files") or []:
                        if f and f not in covered:
                            covered.append(f)

        # 3. Diferencia → trabajo sin capturar
        uncaptured = [f for f in impacted if not _path_matches(f, covered)]

        status = (
            UncapturedStatus.UNCAPTURED if uncaptured else UncapturedStatus.CLEAN
        )
        return UncapturedWorkResult(
            status=status,
            impacted_files=impacted,
            covered_files=covered,
            uncaptured_files=uncaptured,
            spring_id=spring.get("spring_id", "")
            if spring
            else (spring_svc.get_spring_id(chain_dir) if spring_svc else ""),
            has_spring=spring is not None,
        )
    except Exception as e:
        # Fail-open honesto: la detección no se pudo completar. El gate NO
        # bloquea, pero el estado es "error" (visible y trazable), nunca
        # "clean" ni éxito.
        return UncapturedWorkResult(
            status=UncapturedStatus.ERROR,
            error=f"uncaptured_work check failed: {e}",
        )
