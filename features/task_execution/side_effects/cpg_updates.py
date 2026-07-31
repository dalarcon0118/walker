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
        return {"ok": True, "files_updated": files_updated, "scope": scope,
                "total_nodes": data.get("stats", {}).get("total_nodes", 0),
                "total_edges": data.get("stats", {}).get("total_edges", 0)}
    except Exception as e:
        return {"ok": False, "files_updated": 0, "scope": scope, "error": str(e)}


def build_project_graph(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    return update_cpg(ctx, {"scope": "full"})


def update_code_index(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    return update_cpg(ctx, {"scope": "incremental"})


def update_delta_graph(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    project_root = _resolve_project_root(ctx)
    try:
        from features.code_analysis import CpgQueryService
        svc = CpgQueryService(ctx.chain_dir or str(project_root))
        result = svc.grep(f"function:{params.get('task_id', ctx.task_id or '')}")
        return {"ok": True, "updated": True, "result": result}
    except Exception as e:
        return {"ok": False, "updated": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("update_cpg", update_cpg)
    engine.register("build_project_graph", build_project_graph)
    engine.register("update_code_index", update_code_index)
    engine.register("update_delta_graph", update_delta_graph)
