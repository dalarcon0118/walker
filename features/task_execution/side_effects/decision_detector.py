"""decision_detector.py — Deteccion automatica de decisiones desde watcher changes.

PROPOSITO: detectar decisiones de arquitectura IMPLICITAS en el trabajo
informal del agente, SIN que el agente las reporte explícitamente.

MECANISMO: analiza los archivos cambiados (change_detected.json del
watcher) y aplica heurísticas sobre qué archivos de arquitectura se
tocaron. Si hay cambios estructurales (contracts, schema, domain,
workflow, config, repositories, providers), genera una ADR en el CKG.

RELACION CON capture_decision:
  - capture_decision  → decisiones EXPLICITAS en nodos del workflow formal
  - detect_decisions  → decisiones IMPLICITAS fuera del workflow (informal)

HEURISTICAS (archivos que indican decision de arquitectura):
  - contracts/*        → decision de contrato/validacion
  - schema/*           → decision de schema/estructura de datos
  - domain/            → decision de dominio de negocio
  - workflow*, nodes/  → decision de workflow/proceso
  - config*, project.json → decision de configuracion
  - repositories.py    → decision de persistencia
  - providers.py       → decision de inyeccion/composicion
  - services.py        → decision de servicios/use-cases
  - engine/*, libs/*, features/*/application/ → decision de arquitectura interna
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from typing import Any

from libs.shared_domain import ExecutionContext
from features.engine.domain.side_effect_contract import as_side_effect, SCOPE_DECISION


# ── Heuristicas: area de arquitectura → prefixo de decision ──────────

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
    ("libs/path_resolver", "resolucion-paths"),
    ("libs/cognitive_graph", "grafo-cognitivo"),
    ("libs/contracts", "contrato"),
    ("task_execution", "tasks"),
    ("side_effects", "side-effects"),
    ("delta_tracking", "delta"),
    ("cognitive_knowledge", "ckg"),
    ("code_analysis", "cpg"),
    ("cli_interface", "cli"),
]

# Archivos que NUNCA indican decision (ruido)
_NOISE = {".pyc", "__pycache__", "tests/", "test_", ".bak", "package-lock", "yarn.lock"}


def _classify(file: str) -> str:
    """Clasifica un archivo en un area de arquitectura (o '' si es ruido)."""
    if any(n in file for n in _NOISE):
        return ""
    for hint, area in _ARCH_HINTS:
        if hint in file:
            return area
    # Default: archivos en features/ con cambios pueden ser decisiones
    if "/features/" in file or file.startswith("features/"):
        return "features"
    return ""


def detect_decision_from_changes(files: list[str]) -> list[dict]:
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
            f"{', '.join(area_files[:5])}"
            + ("..." if len(area_files) > 5 else "")
        )
        decisions.append({
            "area": area,
            "statement": statement,
            "rationale": rationale,
            "files": area_files,
        })
    return decisions


@as_side_effect(scope=SCOPE_DECISION)
def detect_decisions(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Side-effect: detecta decisiones implicitas desde los cambios del watcher.

    Side-effects:
        - sin params: usa change_detected.json del proyecto (new_changes).
        - con params['files']: usa la lista provista explicitamente.

    Registra las decisiones en el CKG del proyecto (ciclo de vida).
    """
    chain_dir = ctx.chain_dir or ""
    project_root = ""
    try:
        from features.project import ProjectService
        if chain_dir:
            paths = ProjectService.resolve_paths(chain_dir)
            project_root = str(paths.project_root)
        elif params.get("project_id"):
            from libs.path_resolver import get_project_dir
            project_root = str(get_project_dir(params["project_id"]))
    except Exception:
        pass
    if not project_root:
        return {"ok": False, "error": "cannot resolve project_root", "detected": 0}

    # 1. Obtener archivos cambiados
    files = params.get("files", []) or []
    if not files:
        try:
            change_path = os.path.join(project_root, "data", "change_detected.json")
            if os.path.exists(change_path):
                with open(change_path) as f:
                    cd = json.load(f)
                files = cd.get("new_changes", []) or cd.get("files_changed", [])
        except Exception:
            pass
    if not files:
        return {"ok": True, "detected": 0, "reason": "no changed files"}

    # 2. Detectar decisiones
    decisions = detect_decision_from_changes(files)
    if not decisions:
        return {"ok": True, "detected": 0, "reason": "no architecture-relevant changes"}

    # 3. Registrar en CKG
    captured = []
    try:
        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
        ckg = CodeKnowledgeGraph(project_root)
        for dec in decisions:
            node = {
                "type": "decision",
                "label": f"decision:{dec['statement'][:40]}",
                "description": dec["rationale"],
                "metadata": {
                    "task_id": ctx.task_id or "",
                    "source": f"watcher-detector:{ctx.session_id or ''}",
                    "area": dec["area"],
                    "files": dec["files"],
                },
            }
            save_r = ckg.save_nodes([node])
            captured.append({
                "node_id": save_r.get("node_ids", [None])[0],
                "area": dec["area"],
                "statement": dec["statement"][:60],
            })
    except Exception as e:
        return {"ok": False, "error": str(e), "detected": len(decisions), "captured": captured}

    return {
        "ok": True,
        "detected": len(decisions),
        "captured": captured,
        "source_files": files[:10],
    }


def register(engine: Any) -> None:
    engine.register("detect_decisions", detect_decisions)
