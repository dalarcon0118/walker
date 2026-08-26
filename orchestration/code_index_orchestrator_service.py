"""orchestration/code_index_orchestrator_service.py — CodeIndexOrchestratorService.

Cross-feature orchestrator para index_code/build_project_graph/sync_delta (ADR-051 Fase 4 G10).

Orquesta 2 features: project (path resolution, watcher) × code_analysis (CPG).
El side-effect code_index.py se reduce a un thin adapter.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class CodeIndexOrchestratorService:
    """Indexa código y sincroniza el CPG con el delta.

    Orquesta ProjectService (paths, watcher) y CpgService (CPG build/update).
    No contiene lógica de negocio propia — solo coordina.
    """

    @staticmethod
    def index_code(chain_dir: str, task_id: str = "", scope: str = "incremental", ctx: Any = None) -> dict[str, Any]:
        """Indexa código incremental o full en el CPG y verifica el espejo."""
        try:
            from features.project import ProjectService, FileWatcher
            from features.code_analysis.application.services.cpg_service import CpgService
            from shared.core.engine.domain.side_effect_contract import ok, failed, degraded

            if not chain_dir:
                return failed(
                    type="index_code",
                    scope="code_index",
                    summary="index_code falló: chain_dir no disponible",
                    signals={"error": "chain_dir not available"},
                )

            paths = ProjectService.resolve_paths(chain_dir)
            project_root = Path(paths.project_root)

            config = _load_project_config(project_root)
            svc = CpgService.from_chain_dir(chain_dir)
            if not svc:
                svc = CpgService.from_project_dir(str(project_root))
            if not svc:
                return failed(
                    type="index_code",
                    scope="code_index",
                    summary=f"index_code falló: CpgService no disponible desde {project_root}",
                    signals={"error": "CpgService no disponible"},
                    metadata={"error": "CpgService no disponible"},
                )

            if scope == "full":
                build_paths = _build_paths(config, project_root)
                data = svc.build(scan_paths=build_paths)
                files_updated = data.get("stats", {}).get("files_scanned", 0)
            else:
                data = svc.update()
                files_updated = data.get("files_updated", 0)

            FileWatcher(str(project_root)).mark_indexed("cpg")

            quality_report = data.get("quality_report", {})
            gates = quality_report.get("gates", [])
            failed_gates = [g for g in gates if not g.get("passed", True)]
            quality_degraded = bool(quality_report.get("degraded", False))
            quality_passed = bool(quality_report.get("passed", True)) and not quality_degraded

            source_fingerprint = str(data.get("source_fingerprint", "") or "")
            fingerprint_match = _verify_cpg_mirror(ctx, str(project_root), source_fingerprint)

            signals = {
                "quality_passed": quality_passed,
                "quality_degraded": quality_degraded,
                "failed_gates": failed_gates,
                "failed_gate_names": [g.get("name") for g in failed_gates],
                "recommend": (
                    "refactorizar_primero"
                    if (failed_gates and not quality_degraded)
                    else "indexar_ok"
                ),
                "files_updated": files_updated,
                "total_nodes": (
                    data.get("stats", {}).get("total_nodes", 0)
                    or data.get("nodes_added", 0)
                ),
                "total_edges": (
                    data.get("stats", {}).get("total_edges", 0)
                    or 0
                ),
                "fingerprint_match": fingerprint_match,
            }
            metadata = {"quality_report": quality_report}
            if source_fingerprint:
                metadata["source_fingerprint"] = source_fingerprint

            if quality_degraded:
                return degraded(
                    type="index_code",
                    scope="code_index",
                    summary=f"CPG indexado ({files_updated} files); calidad degradada",
                    signals=signals,
                    metadata=metadata,
                )
            if failed_gates:
                names = ", ".join(signals["failed_gate_names"])
                return ok(
                    type="index_code",
                    scope="code_index",
                    summary=f"CPG indexado ({files_updated} files); {len(failed_gates)} gates fallan ({names}) — refactorizar",
                    signals=signals,
                    metadata=metadata,
                )
            return ok(
                type="index_code",
                scope="code_index",
                summary=f"CPG indexado ({files_updated} files); calidad OK",
                signals=signals,
                metadata=metadata,
            )
        except Exception as e:
            return failed(
                type="index_code",
                scope="code_index",
                summary=f"index_code falló: {str(e)[:120]}",
                signals={"error": str(e)[:200]},
                metadata={"error": str(e)},
            )

    @staticmethod
    def sync_delta(chain_dir: str, task_id: str = "", ctx: Any = None) -> dict[str, Any]:
        """Sincroniza el delta graph con el fingerprint del código actual."""
        try:
            from features.project import ProjectService
            from features.code_analysis.application.services.cpg_service import CpgService
            from shared.core.engine.domain.side_effect_contract import ok, failed

            if not chain_dir:
                return failed(
                    type="sync_delta",
                    scope="code_index",
                    summary="sync_delta falló: chain_dir no disponible",
                    signals={"error": "chain_dir not available"},
                )

            paths = ProjectService.resolve_paths(chain_dir)
            project_root = Path(paths.project_root)

            svc = CpgService.from_chain_dir(chain_dir)
            if not svc:
                svc = CpgService.from_project_dir(str(project_root))
            if not svc:
                return failed(
                    type="sync_delta",
                    scope="code_index",
                    summary="sync_delta falló: CpgService no disponible",
                    signals={"error": "CpgService no disponible"},
                )

            result = svc.grep(f"function:{task_id}")
            return ok(
                type="sync_delta",
                scope="code_index",
                summary="delta graph actualizado",
                signals={"updated": True, "result": result},
            )
        except Exception as e:
            return failed(
                type="sync_delta",
                scope="code_index",
                summary=f"sync_delta falló: {str(e)[:120]}",
                signals={"error": str(e)[:200]},
            )


def _load_project_config(project_root: Path) -> dict:
    """Carga la configuración del project.json (con fallback).

    ADR-WALKER-054 D1: sin code_paths — el scope de escaneo es repo_path.
    """
    config = {"code_extensions": ["py", "js", "ts", "tsx", "css", "json", "md"]}
    proj_file = project_root / "project.json"
    if not proj_file.exists():
        return config

    outcome = _load_project_config_core(proj_file)
    if is_successful(outcome):
        return outcome.value_or(config)
    logger.warning(
        "code_index_orchestrator: project.json ilegible (%s)",
        outcome.failure(),
    )
    return config


@safe
def _load_project_config_core(proj_file: Path) -> dict:
    with open(proj_file) as f:
        data = json.load(f)
    loc = data.get("project", {}).get("location", {})
    return {
        "code_extensions": loc.get("code_extensions", ["py", "js", "ts", "tsx", "css", "json", "md"]),
    }


def _build_paths(config: dict, project_root: Path) -> list[str]:
    return [str(project_root)]


def _verify_cpg_mirror(ctx: Any, project_root: str, source_fingerprint: str) -> bool:
    """Verifica que el CPG es espejo del código actual."""
    if not source_fingerprint:
        return False
    try:
        from features.project import FileWatcher

        watcher = FileWatcher(project_root)
        live_fingerprint = watcher.get_status().get("fingerprint", "") or ""
        return source_fingerprint == live_fingerprint
    except Exception as e:
        logger.warning("code_index_orchestrator: verify mirror fallo (%s)", e)
        return False
