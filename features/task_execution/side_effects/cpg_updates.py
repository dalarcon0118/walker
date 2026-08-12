"""CPG side-effects: update_cpg, build_project_graph, update_code_index, update_delta_graph.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers degradan con fallback explícito + log (convention-001).
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from libs.shared_domain import ExecutionContext
from features.project import ProjectService

logger = logging.getLogger(__name__)


@safe
def _resolve_project_root_core(chain_dir: str) -> Path:
    """Core puro: resuelve el project_root desde chain_dir (puede lanzar)."""
    paths = ProjectService.resolve_paths(chain_dir)
    return Path(paths.project_root)


def _resolve_project_root(ctx: ExecutionContext) -> Path:
    if ctx.chain_dir:
        result = _resolve_project_root_core(ctx.chain_dir)
        if is_successful(result):
            return result.value_or(Path(ctx.chain_dir).parent.parent)
        logger.warning(
            "cpg_updates: resolve_paths fallo (%s: %s); fallback a parent.parent",
            type(result.failure()).__name__, result.failure(),
        )
        return Path(ctx.chain_dir).parent.parent
    return Path.cwd()


@safe
def _verify_cpg_mirror_core(project_root: Path, source_fingerprint: str) -> bool:
    """Core puro: verifica que el CPG recién construido es espejo del código.

    Fingerprint_match = el source_fingerprint del build coincide con el
    fingerprint vivo del watcher (ADR-043, SPEC-002 cpg_indexed).

    Can lanzar (FileWatcher IO) — el wrapper degrada con fallback explícito.
    """
    if not source_fingerprint:
        return False
    from features.project import FileWatcher

    watcher = FileWatcher(str(project_root))
    live_fingerprint = watcher.get_status().get("fingerprint", "") or ""
    return source_fingerprint == live_fingerprint


def _verify_cpg_mirror(ctx: ExecutionContext, source_fingerprint: str) -> bool:
    """Wrapper convention-001: nunca lanza; degrada a False con log."""
    result = _verify_cpg_mirror_core(_resolve_project_root(ctx), source_fingerprint)
    if is_successful(result):
        return result.value_or(False)
    logger.warning(
        "cpg_updates: no se pudo verificar el fingerprint del espejo (%s: %s)",
        type(result.failure()).__name__, result.failure(),
    )
    return False


@safe
def _load_project_config_core(proj_file: Path) -> dict:
    """Core puro: carga la config del project.json (puede lanzar JSONDecodeError)."""
    with open(proj_file) as f:
        data = json.load(f)
    loc = data.get("project", {}).get("location", {})
    return {"code_paths": loc.get("code_paths", []),
            "code_extensions": loc.get("code_extensions",
                                       ["py", "js", "ts", "tsx", "css", "json", "md"])}


def _load_project_config(project_root: Path) -> dict:
    proj_file = project_root / "project.json"
    config = {"code_paths": [], "code_extensions": ["py", "js", "ts", "tsx", "css", "json", "md"]}
    if proj_file.exists():
        result = _load_project_config_core(proj_file)
        if is_successful(result):
            return result.value_or(config)
        logger.warning(
            "cpg_updates: project.json ilegible (%s: %s); config por defecto",
            type(result.failure()).__name__, result.failure(),
        )
    return config


def _cpg_build_paths(config: dict, project_root: Path) -> list[str]:
    code_paths = config.get("code_paths", [])
    return [str(project_root / p) for p in code_paths] if code_paths else [str(project_root)]


@safe
def _mark_cpg_indexed_core(project_root: Path) -> None:
    """Core puro: marca CPG como up_to_date en el watcher (puede lanzar)."""
    from features.project import FileWatcher
    FileWatcher(str(project_root)).mark_indexed("cpg")


def update_cpg(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    project_root = _resolve_project_root(ctx)
    config = _load_project_config(project_root)
    scope = params.get("scope", "incremental")
    try:
        from features.code_analysis import cpg_service
        from features.engine.domain.side_effect_contract import (
            ok, failed, degraded,
        )
        builder = cpg_service(project_dir=str(project_root))
        if scope == "full":
            paths = _cpg_build_paths(config, project_root)
            data = builder.build(paths=paths)
            files_updated = data.get("stats", {}).get("files_scanned", 0)
        else:
            # incremental: usa watcher + delta sync (BuildGraphUseCase.update)
            data = builder.update()
            files_updated = data.get("files_updated", 0)
        # Mark CPG as up_to_date in watcher (best-effort con log)
        marked = _mark_cpg_indexed_core(project_root)
        if not is_successful(marked):
            logger.warning(
                "cpg_updates: mark_indexed('cpg') fallo (%s: %s)",
                type(marked.failure()).__name__, marked.failure(),
            )

        # ── Contrato estándar: el workflow lee signals (no metadata interna).
        quality_report = data.get("quality_report", {})
        gates = quality_report.get("gates", [])
        failed_gates = [g for g in gates if not g.get("passed", True)]
        quality_degraded = bool(quality_report.get("degraded", False))
        quality_passed = bool(quality_report.get("passed", True)) and not quality_degraded

        # ── Certificación del espejo (SPEC-002 cpg_indexed): el CPG recién
        # construido debe coincidir con el estado actual del código. Se
        # compara el source_fingerprint del build contra el fingerprint
        # vivo del watcher; fingerprint_match = espejo verificado.
        source_fingerprint = str(data.get("source_fingerprint", "") or "")
        fingerprint_match = _verify_cpg_mirror(ctx, source_fingerprint)

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
        cpg_metadata = {"quality_report": quality_report}
        if source_fingerprint:
            cpg_metadata["source_fingerprint"] = source_fingerprint

        if quality_degraded:
            return degraded(
                type="update_cpg",
                scope="code_index",
                summary=(
                    f"CPG indexado ({files_updated} files); calidad degradada "
                    f"(qlty sin config o no disponible)"
                ),
                signals=signals,
                metadata=cpg_metadata,
            )
        if failed_gates:
            names = ", ".join(signals["failed_gate_names"])
            return ok(
                type="update_cpg",
                scope="code_index",
                summary=(
                    f"CPG indexado ({files_updated} files); {len(failed_gates)} "
                    f"gates de calidad fallan ({names}) — recomendado refactorizar"
                ),
                signals=signals,
                metadata=cpg_metadata,
            )
        return ok(
            type="update_cpg",
            scope="code_index",
            summary=f"CPG indexado ({files_updated} files); calidad OK",
            signals=signals,
            metadata=cpg_metadata,
        )
    except Exception as e:
        return failed(
            type="update_cpg",
            scope="code_index",
            summary=f"update_cpg falló: {str(e)[:120]}",
            signals={"error": str(e)[:200]},
            metadata={"error": str(e)},
        )


def build_project_graph(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    return update_cpg(ctx, {"scope": "full"})


def update_code_index(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    return update_cpg(ctx, {"scope": "incremental"})


def update_delta_graph(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    from features.engine.domain.side_effect_contract import ok, failed
    project_root = _resolve_project_root(ctx)
    try:
        from features.code_analysis import cpg_service
        svc = cpg_service(chain_dir=ctx.chain_dir or "", project_dir=str(project_root))
        result = svc.grep(f"function:{params.get('task_id', ctx.task_id or '')}")
        return ok(
            type="update_delta_graph",
            scope="code_index",
            summary="delta graph actualizado",
            signals={"updated": True, "result": result},
        )
    except Exception as e:
        return failed(
            type="update_delta_graph",
            scope="code_index",
            summary=f"update_delta_graph falló: {str(e)[:120]}",
            signals={"error": str(e)[:200]},
        )


def register(engine: Any) -> None:
    engine.register("update_cpg", update_cpg)
    engine.register("build_project_graph", build_project_graph)
    engine.register("update_code_index", update_code_index)
    engine.register("update_delta_graph", update_delta_graph)
