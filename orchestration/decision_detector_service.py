"""orchestration/decision_detector_service.py — DecisionDetectorService.

Cross-feature orchestrator para detect_decisions (ADR-051 Fase 4 G10).

Orquesta 2 features: project (path resolution, watcher) × cognitive_knowledge (CKG).
El side-effect decision_detector.py se reduce a un thin adapter.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_ARCH_HINTS: list[tuple[str, str]] = [
    ("contracts", "contrato"),
    ("schema", "schema"),
    ("domain", "dominio"),
    ("workflow", "workflow"),
    ("nodes/", "workflow"),
    ("config", "configuracion"),
    ("repositories", "persistencia"),
    ("providers", "composicion"),
    ("services", "servicios"),
    ("engine/application", "motor"),
    ("engine/domain", "motor-dominio"),
    ("shared/core/path_resolver", "resolucion-paths"),
    ("shared/core/cognitive_graph", "grafo-cognitivo"),
    ("shared/common/context", "contrato"),
    ("task_execution", "tasks"),
    ("side_effects", "side-effects"),
    ("delta_tracking", "delta"),
    ("cognitive_knowledge", "ckg"),
    ("code_analysis", "cpg"),
    ("cli_interface", "cli"),
]

_NOISE = {".pyc", "__pycache__", "tests/", "test_", ".bak", "package-lock", "yarn.lock"}


class DecisionDetectorService:
    """Detecta decisiones implícitas desde archivos cambiados.

    Orquesta ProjectService (paths) y CkgApplicationService (CKG).
    No contiene lógica de negocio propia — solo coordina.
    """

    @staticmethod
    def detect_decisions(
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Detecta decisiones implícitas desde los cambios del watcher."""
        params = params or {}
        files: list[str] = params.get("files", []) or []
        project_root = ""

        try:
            from features.project import ProjectService

            if chain_dir:
                paths = ProjectService.resolve_paths(chain_dir)
                project_root = str(paths.project_root)
            elif params.get("project_id"):
                from shared.core.path_resolver import get_project_dir

                project_root = str(get_project_dir(params["project_id"]))
        except Exception as e:
            logger.warning(
                "decision_detector: no se pudo resolver project_root (%s: %s)",
                type(e).__name__, e,
            )
        if not project_root:
            return {"ok": False, "error": "cannot resolve project_root", "detected": 0}

        if not files:
            try:
                change_path = os.path.join(project_root, "data", "change_detected.json")
                if os.path.exists(change_path):
                    with open(change_path) as f:
                        cd = json.load(f)
                    files = cd.get("new_changes", []) or cd.get("files_changed", [])
            except Exception as e:
                logger.warning(
                    "decision_detector: no se pudo leer change_detected.json (%s: %s)",
                    type(e).__name__, e,
                )
        if not files:
            return {"ok": True, "detected": 0, "reason": "no changed files"}

        decisions = _detect_decision_from_changes(files)
        if not decisions:
            return {"ok": True, "detected": 0, "reason": "no architecture-relevant changes"}

        captured = []
        try:
            from features.cognitive_knowledge.application.services.ckg_service import CkgApplicationService

            for dec in decisions:
                node = {
                    "type": "decision",
                    "label": f"decision:{dec['statement'][:40]}",
                    "description": dec["rationale"],
                    "metadata": {
                        "task_id": task_id,
                        "source": f"watcher-detector:{session_id or ''}",
                        "area": dec["area"],
                        "files": dec["files"],
                    },
                }
                save_r = CkgApplicationService.save_nodes(project_root, [node])
                captured.append(
                    {
                        "node_id": save_r.get("node_ids", [None])[0],
                        "area": dec["area"],
                        "statement": dec["statement"][:60],
                    }
                )
        except Exception as e:
            logger.warning(
                "decision_detector: registro en CKG fallo (%s: %s)",
                type(e).__name__, e,
            )
            return {
                "ok": False,
                "error": str(e),
                "detected": len(decisions),
                "captured": captured,
            }

        return {
            "ok": True,
            "detected": len(decisions),
            "captured": captured,
            "source_files": files[:10],
        }


def _classify(file: str) -> str:
    """Clasifica un archivo en un area de arquitectura (o '' si es ruido)."""
    if any(n in file for n in _NOISE):
        return ""
    for hint, area in _ARCH_HINTS:
        if hint in file:
            return area
    if "/features/" in file or file.startswith("features/"):
        return "features"
    return ""


def _detect_decision_from_changes(files: list[str]) -> list[dict]:
    """Detecta decisiones implicitas desde archivos cambiados.

    Returns:
        Lista de dicts {statement, rationale, area} — una por area de
        arquitectura afectada con >=1 archivo relevante.
    """
    areas: dict[str, list[str]] = {}
    for f in files:
        area = _classify(f)
        if area:
            areas.setdefault(area, []).append(f)

    decisions = []
    for area, area_files in sorted(areas.items()):
        statement = (
            f"ADR-{area}: el trabajo informal modifico archivos de "
            f"{area} ({len(area_files)} archivos)"
        )
        rationale = (
            f"Detectado automaticamente desde watcher changes. Archivos: "
            f"{', '.join(area_files[:5])}" + ("..." if len(area_files) > 5 else "")
        )
        decisions.append(
            {
                "area": area,
                "statement": statement,
                "rationale": rationale,
                "files": area_files,
            }
        )
    return decisions
