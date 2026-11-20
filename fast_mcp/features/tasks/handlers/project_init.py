"""handlers/project_init.py — acciones del nodo PROJECT_INIT.

Detecta el contexto del proyecto y propone la configuración inicial SIN
escribir archivos del repo (la escritura es una decisión del usuario). El
CKG y el primer sprint son idempotentes y fail-open.
"""
from __future__ import annotations

import json
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


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _detect_tooling(root: Path, language: str) -> dict[str, str]:
    """Test framework / formatter / linter desde evidencia, no desde supuestos.

    Solo devuelve lo que encuentra: un campo vacío significa "no detectado",
    nunca un default inventado. El llamador decide si lo propone o lo descarta.
    """
    pyproject = _read(root / "pyproject.toml").lower()
    pkg = _read(root / "package.json").lower()
    reqs = " ".join(
        _read(root / name).lower()
        for name in ("requirements.txt", "requirements-dev.txt", "setup.cfg", "tox.ini")
    )
    blob = f"{pyproject} {reqs}"
    out: dict[str, str] = {}

    if language == "python":
        if (
            (root / "pytest.ini").exists()
            or (root / "conftest.py").exists()
            or (root / "tests").is_dir()
            or "[tool.pytest" in pyproject
            or "pytest" in blob
        ):
            out["test_framework"] = "pytest"
        if "ruff" in blob or (root / "ruff.toml").exists():
            out["linter"] = "ruff"
            out["formatter"] = "ruff"
        elif "black" in blob:
            out["formatter"] = "black"
        if "linter" not in out:
            if "flake8" in blob or (root / ".flake8").exists():
                out["linter"] = "flake8"
            elif "pylint" in blob:
                out["linter"] = "pylint"
    elif language in ("javascript", "typescript"):
        if "vitest" in pkg:
            out["test_framework"] = "vitest"
        elif "jest" in pkg:
            out["test_framework"] = "jest"
        elif "mocha" in pkg:
            out["test_framework"] = "mocha"
        if "prettier" in pkg or any((root / n).exists() for n in (".prettierrc", ".prettierrc.json")):
            out["formatter"] = "prettier"
        if "eslint" in pkg or any((root / n).exists() for n in (".eslintrc", ".eslintrc.json", ".eslintrc.js")):
            out["linter"] = "eslint"
    return out


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
    """Propone la configuración inicial desde evidencia (no escribe el repo).

    El schema de `project.json` define identidad, design, architecture, coding,
    quality y dependencies; acá se propone lo que se puede derivar con evidencia
    (lenguaje, package manager, test framework, formatter, linter). Lo que no
    se puede inferir (layer_rules, patrones, preferencias) queda afuera: lo
    confirma el usuario en REVIEW.
    """
    detected = detect_project_context(ctx, data).data or {}
    root = Path(str(detected.get("root") or _root(ctx, data)))
    language = str(detected.get("language") or "unknown")
    tooling = _detect_tooling(root, language)
    config = {
        "name": root.name or "project",
        "language": language,
        "framework": str(detected.get("framework") or ""),
        "package_manager": str(detected.get("package_manager") or ""),
        "code_extensions": detected.get("code_extensions") or [],
        "markers": detected.get("markers") or [],
        "test_framework": tooling.get("test_framework", ""),
        "formatter": tooling.get("formatter", ""),
        "linter": tooling.get("linter", ""),
    }
    patch: dict[str, Any] = {}
    identity = {}
    if language and language != "unknown":
        identity["language"] = language
    if config["framework"]:
        identity["framework"] = config["framework"]
    if identity:
        patch["identity"] = identity
    coding = {
        key: config[key]
        for key in ("test_framework", "formatter", "linter")
        if config[key]
    }
    if coding:
        patch["coding"] = coding
    return TaskResult.success(
        data={
            "ok": True,
            "config": config,
            "manifest_patch": patch,
            "evidence": {"root": str(root), "markers": config["markers"],
                         "tooling": tooling},
        }
    )


def _merge_empty(
    target: dict[str, Any], patch: dict[str, Any],
    applied: list[str], skipped: list[str], prefix: str = "",
) -> None:
    """Completa solo campos vacíos: jamás pisa un valor curado a mano."""
    for key, value in patch.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            node = target.get(key)
            if not isinstance(node, dict):
                node = {}
                target[key] = node
            _merge_empty(node, value, applied, skipped, prefix=f"{path}.")
            continue
        if value in (None, "", [], {}):
            continue
        current = target.get(key)
        if current in (None, "", [], {}):
            target[key] = value
            applied.append(path)
        elif current != value:
            skipped.append(f"{path} (curado: {current!r})")


def apply_project_config(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Aplica la propuesta de BUILD_PROJECT al `project.json` (paso REVIEW).

    Idempotente y conservador: completa únicamente lo vacío y reporta qué dejó
    intacto. El manifest es la constitución del proyecto y lo curado a mano no
    se sobrescribe por detección automática.
    """
    intent = str(data.get("intent") or "").strip()
    if intent and intent != "project_ready":
        return TaskResult.success(
            data={"ok": True, "applied": [], "skipped": [], "reason": f"intent={intent}"}
        )

    proposal = build_project_config(ctx, data).data or {}
    patch = proposal.get("manifest_patch") or {}
    if not patch:
        return TaskResult.success(
            data={"ok": True, "applied": [], "skipped": [],
                  "reason": "sin evidencia para aplicar"}
        )

    from datetime import datetime, timezone

    from fast_mcp.shared_kernel.project_support import ProjectService

    try:
        paths = ProjectService.resolve_paths(str(_root(ctx, data)))
        manifest_path = Path(str(paths.data_dir)) / "project.json"
    except Exception as exc:  # noqa: BLE001 — sin layout no hay manifest
        return TaskResult.failure(
            f"apply_project_config: {type(exc).__name__}: {exc}"
        )
    if not manifest_path.is_file():
        return TaskResult.failure(
            "apply_project_config: no hay project.json; prepará el proyecto primero"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return TaskResult.failure(
            f"apply_project_config: project.json inválido ({type(exc).__name__}: {exc})"
        )

    project = manifest.setdefault("project", {})
    applied: list[str] = []
    skipped: list[str] = []
    _merge_empty(project, patch, applied, skipped)
    project.setdefault("meta", {})["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return TaskResult.success(
        data={
            "ok": True,
            "applied": applied,
            "skipped": skipped,
            "project_json": str(manifest_path),
        }
    )


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
        ("apply_project_config", apply_project_config, "Apply proposed config to project.json"),
        ("initialize_knowledge_base", initialize_knowledge_base, "Seed the CKG for a new project"),
        ("create_first_sprint", create_first_sprint, "Create the initial planning sprint"),
    ):
        registry.register(
            name,
            fn,
            metadata={"description": desc, "tags": ["project_init"], "scope": "internal"},
        )
