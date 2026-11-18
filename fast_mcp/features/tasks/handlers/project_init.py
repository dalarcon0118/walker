"""handlers/project_init.py — acciones del nodo PROJECT_INIT.

Detecta el contexto del proyecto y propone la configuración inicial SIN
escribir archivos del repo (la escritura es una decisión del usuario). El
CKG y el primer sprint son idempotentes y fail-open.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult

_MARKERS = (
    ("pyproject.toml", "python", "pytest", [".py"]),
    ("package.json", "javascript", "npm", [".js", ".mjs", ".ts"]),
    ("go.mod", "go", "go", [".go"]),
    ("Cargo.toml", "rust", "cargo", [".rs"]),
)


def _root(ctx: ExecutionContext, data: dict[str, Any]) -> Path:
    return Path(str(getattr(ctx, "chain_dir", "") or data.get("chain_dir") or ""))


def detect_project_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    root = _root(ctx, data)
    found = [
        {"file": name, "language": lang, "package_manager": pm, "extensions": exts}
        for name, lang, pm, exts in _MARKERS
        if (root / name).exists()
    ]
    if not found:
        return TaskResult.success(
            data={
                "language": "unknown",
                "framework": "",
                "package_manager": "",
                "root": str(root),
            }
        )
    first = found[0]
    return TaskResult.success(
        data={
            "language": first["language"],
            "framework": "",
            "package_manager": first["package_manager"],
            "root": str(root),
            "markers": [f["file"] for f in found],
            "code_extensions": first["extensions"],
        }
    )


def build_project_config(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    detected = detect_project_context(ctx, data).data or {}
    root = str(detected.get("root") or "")
    config = {
        "name": Path(root).name or "project",
        "language": detected.get("language") or "unknown",
        "code_extensions": detected.get("code_extensions") or [],
        "markers": detected.get("markers") or [],
    }
    return TaskResult.success(data={"ok": True, "config": config})


def initialize_knowledge_base(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    try:
        from fast_mcp.composition import memory_tool_runner

        result = memory_tool_runner("documenting_knowledge")(
            chain_dir=str(_root(ctx, data)),
            statement=str(data.get("statement") or "Knowledge base initialized."),
            type="project",
            session_id=str(getattr(ctx, "session_id", "") or ""),
        )
        if isinstance(result, dict) and (result.get("ok") is False or result.get("error")):
            return TaskResult.success(
                data={"ok": True, "skipped": True, "error": result.get("error")}
            )
        node_id = result.get("node_id") if isinstance(result, dict) else ""
        return TaskResult.success(data={"ok": True, "node_id": node_id or ""})
    except Exception as exc:  # noqa: BLE001 — el bootstrap no debe romper
        return TaskResult.success(
            data={"ok": True, "skipped": True, "error": f"{type(exc).__name__}: {exc}"}
        )


def create_first_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    root = str(_root(ctx, data))
    try:
        from fast_mcp.composition import build_sprint_service

        service = build_sprint_service(root)
        if service is None:
            return TaskResult.failure("create_first_sprint: sprint service no disponible")
        spring_id = str(data.get("spring_id") or "SPR-INIT")
        if service.get_spring(spring_id):
            return TaskResult.success(
                data={"ok": True, "spring_id": spring_id, "existing": True}
            )
        service.create_spring(
            spring_id=spring_id,
            goal=str(data.get("goal") or f"Initial sprint for {Path(root).name}"),
            status="planning",
            project_id=str(data.get("project_id") or ""),
        )
        return TaskResult.success(
            data={"ok": True, "spring_id": spring_id, "existing": False}
        )
    except Exception as exc:  # noqa: BLE001
        return TaskResult.failure(f"create_first_sprint: {type(exc).__name__}: {exc}")


def register(registry) -> None:
    for name, fn, desc in (
        ("detect_project_context", detect_project_context, "Detect language/framework markers"),
        ("build_project_config", build_project_config, "Propose project config (no writes)"),
        ("initialize_knowledge_base", initialize_knowledge_base, "Seed the CKG for a new project"),
        ("create_first_sprint", create_first_sprint, "Create the initial planning sprint"),
    ):
        registry.register(
            name,
            fn,
            metadata={"description": desc, "tags": ["project_init"], "scope": "internal"},
        )
