"""CPG side-effects: update_cpg, build_project_graph, update_code_index, update_delta_graph."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from libs.shared_domain import ExecutionContext
from features.project import ProjectService


def _resolve_project_root(ctx: ExecutionContext) -> Path:
    if ctx.chain_dir:
        try:
            paths = ProjectService.resolve_paths(ctx.chain_dir)
            return Path(paths.project_root)
        except Exception:
            return Path(ctx.chain_dir).parent.parent
    return Path.cwd()


def _load_project_config(project_root: Path) -> dict:
    proj_file = project_root / "project.json"
    config = {"code_paths": [], "code_extensions": ["py", "js", "ts", "tsx", "css", "json", "md"]}
    if proj_file.exists():
        try:
            with open(proj_file) as f: data = json.load(f)
            loc = data.get("project", {}).get("location", {})
            config["code_paths"] = loc.get("code_paths", [])
            config["code_extensions"] = loc.get("code_extensions", config["code_extensions"])
        except Exception: pass
    return config


def _cpg_build_paths(config: dict, project_root: Path) -> list[str]:
    code_paths = config.get("code_paths", [])
    return [str(project_root / p) for p in code_paths] if code_paths else [str(project_root)]


def update_cpg(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    project_root = _resolve_project_root(ctx)
    config = _load_project_config(project_root)
    scope = params.get("scope", "incremental")
    try:
        from features.code_analysis import CPGBuilder
        from features.engine.domain.side_effect_contract import (
            ok, failed, degraded,
        )
        builder = CPGBuilder(str(project_root))
        if scope == "full":
            paths = _cpg_build_paths(config, project_root)
            data = builder.build(paths=paths)
            files_updated = data.get("stats", {}).get("files_scanned", 0)
        else:
            data = builder.update()
            files_updated = len(data.get("tracked_files", {}))
        # Mark CPG as up_to_date in watcher (SSOT: project owns watcher state)
        try:
            from features.project.infrastructure.watcher import FileWatcher
            FileWatcher(str(project_root)).mark_indexed("cpg")
        except Exception:
            pass

        # ── Contrato estándar: el workflow lee signals (no metadata interna).
        quality_report = data.get("quality_report", {})
        gates = quality_report.get("gates", [])
        failed_gates = [g for g in gates if not g.get("passed", True)]
        quality_degraded = bool(quality_report.get("degraded", False))
        quality_passed = bool(quality_report.get("passed", True)) and not quality_degraded

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
            "total_nodes": data.get("stats", {}).get("total_nodes", 0),
            "total_edges": data.get("stats", {}).get("total_edges", 0),
        }

        if quality_degraded:
            return degraded(
                type="update_cpg",
                scope="code_index",
                summary=(
                    f"CPG indexado ({files_updated} files); calidad degradada "
                    f"(qlty sin config o no disponible)"
                ),
                signals=signals,
                metadata={"quality_report": quality_report},
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
                metadata={"quality_report": quality_report},
            )
        return ok(
            type="update_cpg",
            scope="code_index",
            summary=f"CPG indexado ({files_updated} files); calidad OK",
            signals=signals,
            metadata={"quality_report": quality_report},
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
        from features.code_analysis import CpgQueryService
        svc = CpgQueryService(ctx.chain_dir or str(project_root))
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
