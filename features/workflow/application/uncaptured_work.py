"""features/workflow/application/uncaptured_work.py — Detector de trabajo sin capturar.

INCUMBENCIA DEL HOST (ADR-WALKER-067): detectar trabajo sin capturar (escaneo
de data/, delta y trace) es un concern de negocio del host, NO del engine. El
engine solo ejecuta la mecánica del workflow; el host valida externamente
(antes/después de llamar al engine) y decide si bloquear el cierre del sprint.

El DOMINIO (UncapturedStatus/UncapturedWorkResult) vive en features/workflow/domain.
Este módulo es la APLICACIÓN: el detector (usa path_resolver / TraceLogger /
ProjectService libremente).
"""
from __future__ import annotations

import json
import os
from typing import Any

from features.workflow.domain.uncaptured import UncapturedStatus, UncapturedWorkResult


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
    """¿El archivo está cubierto por alguna ruta declarada en specs?"""
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


def _collect_impacted_files(data_dir: str, project_root: str) -> list[str]:
    """Archivos modificados desde change_detected.json + eventos file_impact."""
    from shared.core.engine.infrastructure.trace_log import TraceLogger

    impacted: list[str] = []
    _change_file = os.path.join(data_dir, "change_detected.json")
    if os.path.isfile(_change_file):
        try:
            with open(_change_file) as _f:
                _cd = json.load(_f)
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
    return impacted


def _load_delta(paths: Any) -> dict:
    """Carga el delta (spring + specs) desde data/delta.json.gz (legacy).

    El detector NO depende del registry global (fixtures aislados): lee el
    delta directamente del data_dir resuelto por ProjectPaths.
    """
    import gzip

    candidates = [
        os.path.join(paths.data_dir, "delta.json.gz"),
        os.path.join(paths.data_dir, "delta.json"),
        os.path.join(paths.delta_dir, "delta.json.gz"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            if path.endswith(".gz"):
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    payload = json.load(f)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            if isinstance(payload, dict) and "docs" in payload:
                return payload["docs"]
            return payload if isinstance(payload, dict) else {}
        except Exception:
            continue
    return {}


def _collect_covered_files(paths, chain_dir: str) -> tuple[list[str], Any]:
    """Archivos cubiertos por las specs del spring en curso.

    M-04 (Fase 5): recibe `paths` (ProjectPaths del puerto IProjectContext)
    en vez de un Project concreto; las specs del spring viven en el delta
    del proyecto (data/delta.json.gz).
    """
    covered: list[str] = []
    spring = None
    if paths is not None:
        spring_id = paths.sprint_id or ""
        delta = _load_delta(paths)
        if delta:
            spring = delta.get(spring_id) if spring_id else None
            if spring is None:
                spring = next(
                    (
                        d
                        for d in delta.values()
                        if isinstance(d, dict)
                        and d.get("type") == "spring"
                        and d.get("status") == "active"
                    ),
                    None,
                )
            if spring:
                spring_id = (
                    spring.get("spring_id")
                    or spring.get("_id")
                    or spring.get("sprint_id")
                    or spring_id
                )
            for doc in delta.values():
                if not isinstance(doc, dict):
                    continue
                if doc.get("type") != "spec":
                    continue
                if spring_id and doc.get("spring_id") != spring_id:
                    continue
                for f in doc.get("files") or []:
                    if f and f not in covered:
                        covered.append(f)
    return covered, spring


def detect_uncaptured_work(chain_dir: str) -> UncapturedWorkResult:
    """Detecta trabajo sin capturar para el sprint activo (host)."""
    try:
        from app.path_resolver import resolve_project_root

        # Resolver el project_root desde el project.json local (mismo mecanismo
        # que TraceLogger.replay). NO usar ProjectService.resolve_paths porque
        # pasa por el registry global y falla para proyectos no registrados /
        # fixtures aislados.
        project_root = resolve_project_root(chain_dir)
        data_dir = os.path.join(project_root, "data")

        # 1. Archivos modificados (change_detected.json + eventos file_impact).
        impacted = _collect_impacted_files(data_dir, project_root)

        # 2. Archivos cubiertos por specs del spring EN CURSO.
        from shared.core.engine.domain.ports_registry import resolve_port
        from shared.core.engine.domain.ports import IProjectContext

        port = resolve_port(IProjectContext)
        paths = port.resolve_paths(project_root) if port is not None else None
        covered, spring = _collect_covered_files(paths, chain_dir)

        # 3. Diferencia → trabajo sin capturar.
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
            else (paths.sprint_id if paths else ""),
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