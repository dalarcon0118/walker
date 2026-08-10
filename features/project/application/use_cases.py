"""
application/use_cases.py — Project domain use cases.

SRP: Each use case orchestrates domain logic + infrastructure.
     Tasks delegate here instead of containing inline logic.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


# ── Constants ──────────────────────────────────────────────────────────────

IGNORE_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        ".expo",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        "coverage",
        ".cognitive",
    }
)
CODE_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java")


# ── ProjectDetector ────────────────────────────────────────────────────────


class ProjectDetector:
    """Detect project properties by scanning the filesystem.

    Replaces inline _detect_language / _detect_framework / _detect_code_paths.
    SRP: Pure detection logic — no side effects beyond filesystem reads.
    """

    @staticmethod
    def detect_language(project_path: str) -> str:
        """Detect primary language from file extensions."""
        scores: dict[str, int] = {}
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [
                d for d in dirs if not d.startswith(".") and d not in IGNORE_DIRS
            ]
            for f in files:
                if f.endswith(".py"):
                    scores["python"] = scores.get("python", 0) + 1
                elif f.endswith((".ts", ".tsx")):
                    scores["typescript"] = scores.get("typescript", 0) + 1
                elif f.endswith((".js", ".jsx")):
                    scores["javascript"] = scores.get("javascript", 0) + 1
                elif f.endswith(".rs"):
                    scores["rust"] = scores.get("rust", 0) + 1
                elif f.endswith(".go"):
                    scores["go"] = scores.get("go", 0) + 1
                elif f.endswith(".java"):
                    scores["java"] = scores.get("java", 0) + 1
        return max(scores, key=scores.get) if scores else "unknown"

    @staticmethod
    def detect_framework(project_path: str) -> str:
        """Detect framework from config files."""
        pkg_json = os.path.join(project_path, "package.json")
        if os.path.exists(pkg_json):
            try:
                with open(pkg_json) as f:
                    pkg = json.load(f)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                if "react-native" in deps:
                    return "react-native"
                if "next" in deps:
                    return "next.js"
                if "react" in deps:
                    return "react"
                if "vue" in deps:
                    return "vue"
                if "@angular/core" in deps:
                    return "angular"
            except Exception:
                pass
        if os.path.exists(os.path.join(project_path, "Cargo.toml")):
            return "rust/cargo"
        if os.path.exists(os.path.join(project_path, "go.mod")):
            return "go"
        if os.path.exists(os.path.join(project_path, "Gemfile")):
            return "ruby/rails"
        return "unknown"

    @staticmethod
    def detect_code_paths(project_path: str) -> list[str]:
        """Detect source code directories (top-level dirs with code files)."""
        code_dirs: list[str] = []
        for entry in sorted(os.listdir(project_path)):
            entry_path = os.path.join(project_path, entry)
            if (
                entry.startswith(".")
                or entry in IGNORE_DIRS
                or not os.path.isdir(entry_path)
            ):
                continue
            has_code = False
            for root, dirs, files in os.walk(entry_path):
                dirs[:] = [
                    d for d in dirs if not d.startswith(".") and d not in IGNORE_DIRS
                ]
                for f in files:
                    if f.endswith(CODE_EXTENSIONS):
                        has_code = True
                        break
                if has_code:
                    break
            if has_code:
                code_dirs.append(entry)
        return code_dirs

    @classmethod
    def detect_all(cls, project_path: str) -> dict[str, Any]:
        """Run all detections at once."""
        return {
            "language": cls.detect_language(project_path),
            "framework": cls.detect_framework(project_path),
            "code_paths": cls.detect_code_paths(project_path),
        }


# ── HealthReportAnalyzer ───────────────────────────────────────────────────


class HealthReportAnalyzer:
    """Analyze health report and generate prioritized next steps.

    SRP: Given a health report, produce actionable recommendations.

    NO hardcodea nombres de task: las tareas se descubren dinamicamente
    desde el TaskRegistry (task_catalog) buscando por 'purpose' (capacidad).
    Si una capacidad no tiene task registrada, el paso se omite o se emite
    sin comando — nunca se inventa una task inexistente.
    """

    _CAPABILITIES: dict[str, list[str]] = {
        "ckg_populate": ["ckg_populate", "ckg_extract", "ckg_init"],
        "cpg_build": ["cpg_build", "cpg_scan", "code_index"],
        "sprint_create": ["sprint_create", "sprint_plan"],
        "project_update": ["project_update", "config_update"],
    }

    @staticmethod
    def _resolve_task(task_catalog: list[dict], capability: str) -> str:
        """Busca una task registrada que cumpla una capacidad (por metadata.purpose).

        Retorna el nombre real de la task, o "" si ninguna la cumple.
        """
        if not task_catalog:
            return ""
        purposes = HealthReportAnalyzer._CAPABILITIES.get(capability, [capability])
        for task in task_catalog:
            task_purposes = task.get("purpose", [])
            if isinstance(task_purposes, str):
                task_purposes = [task_purposes]
            if any(p in purposes for p in task_purposes):
                return task.get("name", "")
        return ""

    @staticmethod
    def _task_cmd(project_id: str, task_name: str) -> str:
        return f"walker {project_id}/{task_name}"

    @staticmethod
    def analyze(
        health: dict,
        project_id: str,
        chain_dir: str,
        task_catalog: list[dict] | None = None,
    ) -> list[dict]:
        """Return prioritized next steps based on health data.

        Lower priority number = higher urgency.
        Each step: {priority, action, reason, command}
        """
        steps: list[dict] = []
        checks = health.get("checks", {})
        warnings = health.get("warnings", [])

        # Priority 0: Project not initialized (missing project.json) → prepare
        if any("project.json: not found" in w for w in warnings):
            steps.append(
                {
                    "priority": 0,
                    "action": "Run walker prepare to initialize project",
                    "reason": "project.json not found — project has not been initialized",
                    "command": f"walker {project_id}/prepare",
                }
            )
            steps.sort(key=lambda s: s["priority"])
            return steps  # nothing else matters until project is prepared

        # Prioridades 1-5 (cada una es un helper SRP)
        HealthReportAnalyzer._prio_ckg(steps, checks, warnings, task_catalog, project_id)
        HealthReportAnalyzer._prio_cpg_files(steps, checks, task_catalog, project_id)
        HealthReportAnalyzer._prio_cpg_missing(steps, checks, task_catalog, project_id)
        HealthReportAnalyzer._prio_sprint(steps, health, task_catalog, project_id)
        HealthReportAnalyzer._prio_config(steps, health, task_catalog, project_id)

        steps.sort(key=lambda s: s["priority"])
        return steps

    @staticmethod
    def _prio_ckg(
        steps: list[dict],
        checks: dict,
        warnings: list[str],
        task_catalog: list[dict] | None,
        project_id: str,
    ) -> None:
        """Priority 1: CKG empty → populate."""
        ckg = checks.get("ckg", "")
        if ckg == "empty" or ckg == "0 nodes" or any("CKG" in w for w in warnings):
            task_name = HealthReportAnalyzer._resolve_task(task_catalog, "ckg_populate")
            if task_name:
                steps.append(
                    {
                        "priority": 1,
                        "action": f"Populate CKG: run task '{task_name}' to capture knowledge",
                        "reason": "CKG is empty — no knowledge base available for agent decisions",
                        "command": HealthReportAnalyzer._task_cmd(
                            project_id, task_name
                        ),
                    }
                )

    @staticmethod
    def _prio_cpg_files(
        steps: list[dict], checks: dict, task_catalog: list[dict] | None, project_id: str
    ) -> None:
        """Priority 2: Files changed → suggest rebuild CPG (user decides)."""
        files = checks.get("files", "")
        if "changed" in files or "stale" in files:
            task_name = HealthReportAnalyzer._resolve_task(task_catalog, "cpg_build")
            if task_name:
                steps.append(
                    {
                        "priority": 2,
                        "action": f"Rebuild CPG to sync code index (task '{task_name}')",
                        "reason": f"Code index is stale — {files} file(s) changed since last CPG build",
                        "command": HealthReportAnalyzer._task_cmd(
                            project_id, task_name
                        ),
                    }
                )

    @staticmethod
    def _prio_cpg_missing(
        steps: list[dict], checks: dict, task_catalog: list[dict] | None, project_id: str
    ) -> None:
        """Priority 3: CPG missing, not scanned, or empty."""
        cpg = checks.get("cpg", "")
        if not cpg or "error" in cpg or cpg == "0 nodes" or "not scanned" in cpg:
            task_name = HealthReportAnalyzer._resolve_task(task_catalog, "cpg_build")
            if task_name:
                steps.append(
                    {
                        "priority": 3,
                        "action": f"Build or update Code Property Graph (task '{task_name}')",
                        "reason": "CPG is missing or empty — needed for code-aware analysis",
                        "command": HealthReportAnalyzer._task_cmd(
                            project_id, task_name
                        ),
                    }
                )

    @staticmethod
    def _prio_sprint(
        steps: list[dict], health: dict, task_catalog: list[dict] | None, project_id: str
    ) -> None:
        """Priority 4: Sprint status."""
        sprint = health.get("sprint")
        if sprint is None:
            task_name = HealthReportAnalyzer._resolve_task(
                task_catalog, "sprint_create"
            )
            if task_name:
                steps.append(
                    {
                        "priority": 4,
                        "action": f"Start a sprint to begin structured workflow (task '{task_name}')",
                        "reason": "No active sprint — project is not in a workflow cycle",
                        "command": HealthReportAnalyzer._task_cmd(
                            project_id, task_name
                        ),
                    }
                )
        elif isinstance(sprint, dict):
            node = sprint.get("node", "")
            gates = sprint.get("gates", [])
            # En un user gate (user_input) la vía real de avance es SUBMIT
            # con los campos que pide el sub-estado (ej. {"decision": ...}).
            # Las gates del grafo (transition <gate>) son rutas entre nodos
            # y NO se disparan directamente: su contract (ej. ckg_has_decision)
            # se cumple tras el submit. Sugerir submit evita confundir al PO.
            if sprint.get("execution_type") == "user_input":
                submit_hint = f"walker {project_id}/submit"
                steps.append(
                    {
                        "priority": 4,
                        "action": f"Respond at node '{node}' ({sprint.get('sub_state', '')}) — user decision required",
                        "reason": f"Sprint is active at {node}.{sprint.get('sub_state', '')} — read the prompt and submit your decision "
                        f'(e.g. \'walker {project_id}/submit {{"decision":"...","evidence":"..."}}\')',
                        "command": submit_hint,
                    }
                )
            elif gates:
                steps.append(
                    {
                        "priority": 4,
                        "action": f"Continue sprint at node '{node}' via available gate(s)",
                        "reason": f"Sprint is active at {node} — {len(gates)} gate(s) available to transition",
                        "command": f"walker {project_id}/transition <gate_name>",
                    }
                )

    @staticmethod
    def _prio_config(
        steps: list[dict], health: dict, task_catalog: list[dict] | None, project_id: str
    ) -> None:
        """Priority 5: Project config incomplete."""
        proj = health.get("project", {})
        if not proj:
            return
        missing = []
        if not proj.get("language"):
            missing.append("language")
        if not proj.get("framework"):
            missing.append("framework")
        if not proj.get("code_paths"):
            missing.append("code_paths")
        if missing:
            task_name = HealthReportAnalyzer._resolve_task(
                task_catalog, "project_update"
            )
            if task_name:
                steps.append(
                    {
                        "priority": 5,
                        "action": f"Complete project configuration: {', '.join(missing)}",
                        "reason": "Project identity/configuration is incomplete",
                        "command": HealthReportAnalyzer._task_cmd(
                            project_id, task_name
                        ),
                    }
                )


# ── Existing use cases ──────────────────────────────────────────────────


class DetectProjectContextUseCase:
    """Detect project context: languages, frameworks, git, code paths."""

    @staticmethod
    def execute(project_path: str) -> dict:
        has_git = os.path.isdir(os.path.join(project_path, ".git"))
        languages = set()
        framework = "unknown"
        code_paths = []

        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
            for f in files:
                if f.endswith(".py"):
                    languages.add("python")
                elif f.endswith((".js", ".jsx")):
                    languages.add("javascript")
                elif f.endswith((".ts", ".tsx")):
                    languages.add("typescript")
                elif f.endswith(".rs"):
                    languages.add("rust")
                elif f.endswith(".java"):
                    languages.add("java")
                elif f.endswith(".go"):
                    languages.add("go")
                if f == "package.json":
                    try:
                        with open(os.path.join(root, f)) as pf:
                            framework = "node/" + json.load(pf).get("name", "unknown")
                    except Exception:
                        framework = "node"
                elif f == "Cargo.toml":
                    framework = "rust/cargo"
                elif f == "go.mod":
                    framework = "go"
            if root == project_path:
                code_paths = [
                    d
                    for d in dirs
                    if d not in (".git", "__pycache__", "node_modules", "venv")
                ]
                break

        return {
            "detected": True,
            "path": project_path,
            "has_git": has_git,
            "has_code": len(languages) > 0,
            "languages": list(languages),
            "framework": framework,
            "code_paths": code_paths,
        }


class UpdateProjectUseCase:
    """Update project.json with schema validation.

    Validates all fields against PROJECT_SCHEMA before saving.
    """

    @staticmethod
    def execute(chain_dir: str, data: dict) -> dict:
        from ..domain.schema import ProjectSchemaValidator
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        project_json_path = paths.project_json_path
        if not project_json_path or not os.path.exists(project_json_path):
            raise FileNotFoundError(f"project.json not found at {project_json_path}")

        with open(project_json_path) as f:
            current = json.load(f)

        # Merge: update only provided fields
        project_data = current.get("project", current)
        for section, values in data.items():
            if isinstance(values, dict) and section in project_data:
                project_data[section].update(values)
            elif isinstance(values, dict):
                project_data[section] = values

        current["project"] = project_data

        # Validate
        errors = ProjectSchemaValidator.validate(current)
        if errors:
            raise ValueError(f"Project schema validation failed: {'; '.join(errors)}")

        current["project"]["meta"]["updated_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        with open(project_json_path, "w") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)

        return {
            "updated": True,
            "project_id": project_data.get("meta", {}).get("project_id", ""),
            "path": project_json_path,
        }

