"""
application/use_cases.py — Project domain use cases.

SRP: Each use case orchestrates domain logic + infrastructure.
     Tasks delegate here instead of containing inline logic.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone
from typing import Any

from libs.shared_domain import TaskResult


# ── Constants ──────────────────────────────────────────────────────────────

IGNORE_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", ".expo", ".venv", "venv",
    "dist", "build", ".next", "coverage", ".cognitive",
})
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
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in IGNORE_DIRS]
            for f in files:
                if f.endswith(".py"): scores["python"] = scores.get("python", 0) + 1
                elif f.endswith((".ts", ".tsx")): scores["typescript"] = scores.get("typescript", 0) + 1
                elif f.endswith((".js", ".jsx")): scores["javascript"] = scores.get("javascript", 0) + 1
                elif f.endswith(".rs"): scores["rust"] = scores.get("rust", 0) + 1
                elif f.endswith(".go"): scores["go"] = scores.get("go", 0) + 1
                elif f.endswith(".java"): scores["java"] = scores.get("java", 0) + 1
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
                if "react-native" in deps: return "react-native"
                if "next" in deps: return "next.js"
                if "react" in deps: return "react"
                if "vue" in deps: return "vue"
                if "@angular/core" in deps: return "angular"
            except Exception:
                pass
        if os.path.exists(os.path.join(project_path, "Cargo.toml")): return "rust/cargo"
        if os.path.exists(os.path.join(project_path, "go.mod")): return "go"
        if os.path.exists(os.path.join(project_path, "Gemfile")): return "ruby/rails"
        return "unknown"

    @staticmethod
    def detect_code_paths(project_path: str) -> list[str]:
        """Detect source code directories (top-level dirs with code files)."""
        code_dirs: list[str] = []
        for entry in sorted(os.listdir(project_path)):
            entry_path = os.path.join(project_path, entry)
            if entry.startswith(".") or entry in IGNORE_DIRS or not os.path.isdir(entry_path):
                continue
            has_code = False
            for root, dirs, files in os.walk(entry_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in IGNORE_DIRS]
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
    def analyze(health: dict, project_id: str, chain_dir: str,
                task_catalog: list[dict] | None = None) -> list[dict]:
        """Return prioritized next steps based on health data.

        Lower priority number = higher urgency.
        Each step: {priority, action, reason, command}
        """
        steps: list[dict] = []
        checks = health.get("checks", {})
        warnings = health.get("warnings", [])

        # Priority 0: Project not initialized (missing project.json) → prepare
        if any("project.json: not found" in w for w in warnings):
            steps.append({
                "priority": 0,
                "action": "Run walker prepare to initialize project",
                "reason": "project.json not found — project has not been initialized",
                "command": f"walker {project_id}/prepare",
            })
            steps.sort(key=lambda s: s["priority"])
            return steps  # nothing else matters until project is prepared

        # Priority 1: CKG empty → populate (task descubierta por capacidad)
        ckg = checks.get("ckg", "")
        if ckg == "empty" or ckg == "0 nodes" or any("CKG" in w for w in warnings):
            task_name = HealthReportAnalyzer._resolve_task(task_catalog, "ckg_populate")
            if task_name:
                steps.append({
                    "priority": 1,
                    "action": f"Populate CKG: run task '{task_name}' to capture knowledge",
                    "reason": "CKG is empty — no knowledge base available for agent decisions",
                    "command": HealthReportAnalyzer._task_cmd(project_id, task_name),
                })

        # Priority 2: Files changed → rebuild CPG (task descubierta por capacidad)
        files = checks.get("files", "")
        if "rebuilt" in files:
            pass  # Auto-rebuild incremental ya ejecutado
        elif "stale" in files or "changed" in files:
            task_name = HealthReportAnalyzer._resolve_task(task_catalog, "cpg_build")
            if task_name:
                steps.append({
                    "priority": 2,
                    "action": f"Rebuild CPG to sync code index (task '{task_name}')",
                    "reason": f"Code index is stale — {files} file(s) changed since last CPG build",
                    "command": HealthReportAnalyzer._task_cmd(project_id, task_name),
                })

        # Priority 3: CPG missing, not scanned, or empty
        cpg = checks.get("cpg", "")
        if not cpg or "error" in cpg or cpg == "0 nodes" or "not scanned" in cpg:
            task_name = HealthReportAnalyzer._resolve_task(task_catalog, "cpg_build")
            if task_name:
                steps.append({
                    "priority": 3,
                    "action": f"Build or update Code Property Graph (task '{task_name}')",
                    "reason": "CPG is missing or empty — needed for code-aware analysis",
                    "command": HealthReportAnalyzer._task_cmd(project_id, task_name),
                })

        # Priority 4: Sprint status
        sprint = health.get("sprint")
        if sprint is None:
            task_name = HealthReportAnalyzer._resolve_task(task_catalog, "sprint_create")
            if task_name:
                steps.append({
                    "priority": 4,
                    "action": f"Start a sprint to begin structured workflow (task '{task_name}')",
                    "reason": "No active sprint — project is not in a workflow cycle",
                    "command": HealthReportAnalyzer._task_cmd(project_id, task_name),
                })
        elif isinstance(sprint, dict):
            node = sprint.get("node", "")
            gates = sprint.get("gates", [])
            if gates:
                steps.append({
                    "priority": 4,
                    "action": f"Continue sprint at node '{node}' via available gate(s)",
                    "reason": f"Sprint is active at {node} — {len(gates)} gate(s) available to transition",
                    "command": f"walker {project_id}/transition <gate_name>",
                })

        # Priority 5: Project config incomplete (task descubierta por capacidad)
        proj = health.get("project", {})
        if proj:
            missing = []
            if not proj.get("language"): missing.append("language")
            if not proj.get("framework"): missing.append("framework")
            if not proj.get("code_paths"): missing.append("code_paths")
            if missing:
                task_name = HealthReportAnalyzer._resolve_task(task_catalog, "project_update")
                if task_name:
                    steps.append({
                        "priority": 5,
                        "action": f"Complete project configuration: {', '.join(missing)}",
                        "reason": "Project identity/configuration is incomplete",
                        "command": HealthReportAnalyzer._task_cmd(project_id, task_name),
                    })

        steps.sort(key=lambda s: s["priority"])
        return steps


# ── PrepareProjectUseCase ──────────────────────────────────────────────────

class PrepareProjectUseCase:
    """Initialize a project: detect properties, create .cognitive/, register, scan CPG, setup watcher.

    SRP: Single use case = prepare a project for first use.
    """

    @staticmethod
    def execute(project_path: str, args_flags: dict) -> dict:
        from features.project.infrastructure.watcher import FileWatcher, IgnoreRules
        from features.code_analysis import CPGBuilder
        from libs.path_resolver import get_walker_root as _get_walker_root

        pid = os.path.basename(os.path.abspath(project_path))
        name = args_flags.get("name", pid)
        walker_root = str(_get_walker_root())

        # 1. Detect project properties
        detections = ProjectDetector.detect_all(project_path)
        final_language = args_flags.get("language", detections["language"])
        final_framework = args_flags.get("framework", detections["framework"])

        # 2. Setup warnings
        setup_warnings: list[str] = []
        if not args_flags.get("language") and detections["language"] == "unknown":
            setup_warnings.append(
                f"language not detected. Set with: walker {pid}/task/update_project --identity.language <lang>"
            )
        if not args_flags.get("framework") and detections["framework"] == "unknown":
            setup_warnings.append(
                f"framework not detected. Set with: walker {pid}/task/update_project --identity.framework <fw>"
            )
        if not detections["code_paths"]:
            setup_warnings.append(
                f"code_paths not detected. Set with: walker {pid}/task/update_project --location.code_paths '[\"src\"]'"
            )
        else:
            setup_warnings.append(
                f"detected code paths: {detections['code_paths']}. "
                f"Verify with: walker {pid}/task/update_project --location.code_paths <json>"
            )

        # 3. Create .cognitive directory structure
        cognitive_dir = os.path.join(project_path, ".cognitive")
        projects_data_dir = os.path.join(cognitive_dir, "projects", pid, "data")
        os.makedirs(projects_data_dir, exist_ok=True)

        # 4. Write project.json
        _write_project_json(projects_data_dir, pid, name, final_language, final_framework, project_path, detections["code_paths"])

        # 5. Register in global registry
        global_registry_path = os.path.join(walker_root, "data", "projects.json")
        _register_in_global_registry(global_registry_path, pid, name, project_path)

        # 6. Copy .walkerignore template
        _copy_walkerignore(walker_root, project_path)

        # 7. Auto-scan CPG
        code_detected, cpg_nodes = _auto_scan_cpg(project_path)

        # 8. Check CKG status (delegates to cognitive_knowledge — SSOT)
        from features.cognitive_knowledge.application.use_cases import CheckCkgHealthUseCase
        ckg_health = CheckCkgHealthUseCase.execute(projects_data_dir)
        if ckg_health["node_count"] == 0 and ckg_health["status"] != "not scanned":
            setup_warnings.append("CKG is empty. Agent should capture knowledge")
        elif ckg_health["status"] == "empty":
            setup_warnings.append("CKG not initialized. Agent should capture knowledge")

        # 9. Init watcher baseline
        watcher = FileWatcher(project_path)
        watcher.scan()
        _reset_watcher_baseline(watcher)

        return {
            "type": "prepare_result",
            "project_id": pid,
            "path": project_path,
            "cognitive_dir": cognitive_dir,
            "project_json": os.path.join(projects_data_dir, "project.json"),
            "registered_in": global_registry_path,
            "code_detected": code_detected,
            "cpg_built": cpg_nodes,
            "watcher_installed": True,
            "files_indexed": watcher.get_status().get("total_changes", 0),
            "setup_warnings": setup_warnings,
            "detected": detections,
            "next_steps": [
                f"walker {pid}/task/update_project --identity.name 'My Project' --location.code_paths '[\"src\",\"tests\"]'",
                f"walker {pid}/status",
            ],
            "_warnings": setup_warnings,
        }


# ── PrepareProjectUseCase helpers (module-level, no side effects beyond IO) ─

def _write_project_json(data_dir: str, pid: str, name: str, language: str, framework: str,
                         repo_path: str, code_paths: list[str]) -> None:
    project_json = {
        "project": {
            "meta": {
                "project_id": pid, "version": "1.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "identity": {"name": name, "language": language, "framework": framework},
            "location": {"repo_path": repo_path, "code_paths": code_paths},
            "design": {"patterns": [], "layer_rules": {}, "forbidden_dependencies": []},
            "architecture": {"module_organization": ""},
            "coding": {"naming_convention": "", "formatter": "", "linter": "", "test_framework": ""},
            "quality_preferences": {},
            "team": {"owners": []},
            "dependencies": {"internal_modules": [], "external_services": []},
        }
    }
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump(project_json, f, indent=2, ensure_ascii=False)


def _register_in_global_registry(registry_path: str, pid: str, name: str, repo_path: str) -> None:
    registry: dict = {"projects": {}}
    if os.path.exists(registry_path):
        try:
            with open(registry_path) as f:
                registry = json.load(f)
        except Exception:
            pass
    now = datetime.now(timezone.utc).isoformat()
    registry.setdefault("projects", {})[pid] = {
        "project_id": pid, "name": name, "repo_path": repo_path,
        "active": True, "registered_at": now, "last_accessed": now,
    }
    registry["updated_at"] = now
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def _copy_walkerignore(walker_root: str, project_path: str) -> None:
    template = os.path.join(walker_root, "data", ".walkerignore")
    dest = os.path.join(project_path, ".walkerignore")
    if not os.path.exists(dest) and os.path.exists(template):
        try:
            with open(template) as f_src, open(dest, "w") as f_dst:
                f_dst.write(f_src.read())
        except Exception:
            pass


def _auto_scan_cpg(project_path: str) -> tuple[bool, int]:
    from features.project.infrastructure.watcher import IgnoreRules
    from features.code_analysis import CPGBuilder

    code_detected = False
    ign = IgnoreRules(project_path)
    for root, dirs, files in os.walk(project_path):
        dirs[:] = ign.filter_dirs(dirs)
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), project_path)
            if ign.is_ignored(rel):
                continue
            if f.endswith((".py", ".js", ".ts", ".rs", ".go", ".java", ".tsx", ".jsx", ".css", ".json", ".md")):
                code_detected = True
                break
        if code_detected:
            break

    cpg_nodes = 0
    if code_detected:
        try:
            builder = CPGBuilder(project_path)
            result = builder.update() if hasattr(builder, 'update') else builder.build()
            if isinstance(result, dict):
                stats = result.get("stats", result)
                cpg_nodes = stats.get("total_nodes", stats.get("nodes", 0))
        except Exception:
            cpg_nodes = 0
    return code_detected, cpg_nodes


# _check_ckg_status removed — delegated to CheckCkgHealthUseCase (cognitive_knowledge SSOT)


def _reset_watcher_baseline(watcher: Any) -> None:
    """After prepare, reset change_detected.json to zero."""
    baseline = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "files_changed": [],
        "total_changes": 0,
        "new_changes": [],
        "fingerprint": "",
        "ckg_status": "up_to_date",
        "cpg_status": "up_to_date",
    }
    with open(watcher.change_path, "w") as f:
        json.dump(baseline, f, indent=2)


# ── CheckHealthUseCase ─────────────────────────────────────────────────────

class CheckHealthUseCase:
    """Check project health: project.json, watcher, CPG, CKG, sprint, next steps.

    SRP: Single use case = produce a complete health report for a project.
    """

    @staticmethod
    def execute(project_id: str, repo_path: str, sprint_id: str = "") -> dict:
        from features.project.infrastructure.watcher import FileWatcher
        from features.code_analysis.application.use_cases import CheckCpgHealthUseCase
        from features.cognitive_knowledge.application.use_cases import CheckCkgHealthUseCase
        from features.engine.application.use_cases import CheckSprintStatusUseCase

        health: dict = {
            "type": "project_health",
            "project_id": project_id,
            "warnings": [],
            "checks": {},
        }

        projects_data_dir = os.path.join(repo_path, ".cognitive", "projects", project_id, "data")
        proj_json = os.path.join(projects_data_dir, "project.json")

        # 1. Check project.json
        _check_project_json(proj_json, project_id, repo_path, health)

        # 2. Rescan + auto-rebuild CPG incremental
        try:
            watcher = FileWatcher(repo_path)
            changes = watcher.scan()
            new_changes = changes.get("new_changes", [])
            total = changes.get("total_changes", 0)

            if new_changes:
                # Auto-rebuild incremental: solo archivos que cambiaron
                try:
                    from features.code_analysis import CPGBuilder
                    builder = CPGBuilder(repo_path)
                    result = builder.index_task_files(new_changes)
                    files_updated = result.get("files_updated", 0)
                    # Re-verificar fingerprint: si CPG ahora es espejo, limpia acumulados
                    watcher.mark_indexed("cpg")
                    health["checks"]["files"] = f"{total} changed → {files_updated} rebuilt"
                except Exception as rebuild_err:
                    health["warnings"].append(f"auto-rebuild CPG incremental falló: {rebuild_err}")
                    health["checks"]["files"] = f"{total} changed (rebuild error)"
            elif total > 0:
                health["checks"]["files"] = f"{total} changed (no new deltas)"
            else:
                health["checks"]["files"] = "up to date"
        except Exception as e:
            health["warnings"].append(f"watcher: {e}")

        # 3. Check CPG (delegates to code_analysis — SSOT)
        cpg_health = CheckCpgHealthUseCase.execute(projects_data_dir)
        health["checks"]["cpg"] = cpg_health["status"]
        if cpg_health["status"] == "not scanned":
            health["warnings"].append("CPG not scanned. Run walker prepare or update_cpg")

        # 4. Check CKG (delegates to cognitive_knowledge — SSOT)
        ckg_health = CheckCkgHealthUseCase.execute(projects_data_dir)
        health["checks"]["ckg"] = ckg_health["status"]
        if ckg_health["node_count"] == 0 and ckg_health["status"] != "not scanned":
            health["warnings"].append("CKG is empty. Agent should capture knowledge")
        elif ckg_health["status"] == "empty":
            health["warnings"].append("CKG not initialized. Agent should capture knowledge")

        # 5. Check Sprint (delegates to engine — SSOT)
        chain_dir = ""
        if sprint_id:
            from libs.path_resolver import get_sprint_dir
            chain_dir = str(get_sprint_dir(project_id, sprint_id))
        else:
            from ..application.service import ProjectService
            chain_dir = ProjectService.find_active_sprint(project_id) or ""

        sprint_health = CheckSprintStatusUseCase.execute(chain_dir, project_id)
        if sprint_health["active"]:
            health["sprint"] = {
                "node": sprint_health.get("node", "?"),
                "sub_state": sprint_health.get("sub_state", "?"),
                "history": sprint_health.get("history", []),
                "gates": sprint_health.get("gates", []),
                "prompt": sprint_health.get("prompt", ""),
                "prompt_id": sprint_health.get("prompt_id", ""),
                "required_fields": sprint_health.get("required_fields", []),
                "execution_type": sprint_health.get("execution_type", ""),
                "graph_progress": sprint_health.get("graph_progress", {}),
            }
        else:
            health["sprint"] = None
            if sprint_health.get("error"):
                health["warnings"].append(f"sprint: {sprint_health['error']}")

        # 6. Proactive next steps (tasks descubiertas dinamicamente desde registry)
        task_catalog = []
        try:
            from features.engine.bootstrap import bootstrap
            registry, _ = bootstrap()
            task_catalog = registry.discover().get("tasks", [])
        except Exception:
            pass  # Sin catalog: analyze() omitira pasos con task no resuelta
        health["next_steps"] = HealthReportAnalyzer.analyze(
            health, project_id, chain_dir, task_catalog=task_catalog)
        return health


def _check_project_json(proj_json: str, project_id: str, repo_path: str, health: dict) -> None:
    from ..domain.schema import ProjectSchemaValidator

    if not os.path.exists(proj_json):
        health["warnings"].append("project.json: not found in data/")
        return

    import json
    try:
        with open(proj_json) as f:
            pd = json.load(f)
        pdata = pd.get("project", pd)
        identity = pdata.get("identity", {})
        location = pdata.get("location", {})
        health["project"] = {
            "name": identity.get("name", project_id),
            "language": identity.get("language", ""),
            "framework": identity.get("framework", ""),
            "repo_path": location.get("repo_path", repo_path),
            "code_paths": location.get("code_paths", []),
        }
        schema_errors = ProjectSchemaValidator.validate(pd)
        if schema_errors:
            health["warnings"].extend([f"project.json: {e}" for e in schema_errors[:3]])
        else:
            health["checks"]["project_json"] = "ok"
    except Exception as e:
        health["warnings"].append(f"project.json: error reading: {e}")


# ── Existing use cases (preserved for backward compat) ─────────────────────

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
                if f.endswith(".py"): languages.add("python")
                elif f.endswith((".js", ".jsx")): languages.add("javascript")
                elif f.endswith((".ts", ".tsx")): languages.add("typescript")
                elif f.endswith(".rs"): languages.add("rust")
                elif f.endswith(".java"): languages.add("java")
                elif f.endswith(".go"): languages.add("go")
                if f == "package.json":
                    try:
                        with open(os.path.join(root, f)) as pf:
                            framework = "node/" + json.load(pf).get("name", "unknown")
                    except Exception:
                        framework = "node"
                elif f == "Cargo.toml": framework = "rust/cargo"
                elif f == "go.mod": framework = "go"
            if root == project_path:
                code_paths = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", "venv")]
                break

        return {
            "detected": True, "path": project_path, "has_git": has_git,
            "has_code": len(languages) > 0, "languages": list(languages),
            "framework": framework, "code_paths": code_paths,
        }


class DetectLegacyUseCase:
    """Detect legacy walker project structure."""

    @staticmethod
    def execute(project_path: str) -> dict:
        indicators = []
        if os.path.isdir(os.path.join(project_path, ".cognitive", "projects")):
            indicators.append("v3_projects_dir")
        if os.path.exists(os.path.join(project_path, "config", "config.json")):
            indicators.append("config_json")
        nodes_count = 0
        chains = []
        projects_list = []

        projects_dir = os.path.join(project_path, ".cognitive", "projects")
        if os.path.isdir(projects_dir):
            for d in sorted(os.listdir(projects_dir)):
                pdir = os.path.join(projects_dir, d)
                if os.path.isdir(pdir):
                    projects_list.append(d)
                    wf = os.path.join(pdir, "workflow.json")
                    if os.path.exists(wf):
                        try:
                            with open(wf) as f:
                                nodes_count += len(json.load(f).get("nodes", {}))
                        except Exception:
                            pass
                    for src in ["spring", "delta"]:
                        sdir = os.path.join(pdir, src)
                        if os.path.isdir(sdir):
                            for c in os.listdir(sdir):
                                from features.spring.infrastructure.repositories import SprintStateRepository
                                if SprintStateRepository().load_dict(os.path.join(sdir, c)):
                                    chains.append(c)

        return {"is_legacy": len(indicators) > 0, "indicators": indicators,
                "pid": projects_list[0] if projects_list else "",
                "nodes_count": nodes_count, "chains": chains, "projects": projects_list}


class ImportLegacyHistoryUseCase:
    """Import legacy chain history to trace files."""

    @staticmethod
    def execute(project_path: str, project_id: str, chains: list[str]) -> dict:
        import gzip
        traces_dir = os.path.join(project_path, ".cognitive", "projects", project_id, "data", "traces")
        os.makedirs(traces_dir, exist_ok=True)
        imported = 0
        for chain in chains:
            for src in ["spring", "delta"]:
                from features.spring.infrastructure.repositories import SprintStateRepository
                chain_dir = os.path.join(project_path, ".cognitive", "projects", project_id, src, chain)
                state = SprintStateRepository().load_dict(chain_dir)
                if state:
                    try:
                        trace_file = os.path.join(traces_dir, f"{chain}.json.gz")
                        with gzip.open(trace_file, "wt", encoding="utf-8") as gf:
                            json.dump({"events": [{"event_type": "legacy_import", "chain_id": chain, "state": state}]}, gf)
                        imported += 1
                        break
                    except Exception:
                        pass
        return {"project_id": project_id, "chains_imported": imported, "traces_path": traces_dir}


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

        current["project"]["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(project_json_path, "w") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)

        return {"updated": True, "project_id": project_data.get("meta", {}).get("project_id", ""),
                "path": project_json_path}


class ImportLegacyTasksUseCase:
    """Import legacy tasks into plan.json format."""

    @staticmethod
    def execute(project_path: str, project_id: str, chains: list[str]) -> dict:
        merged_tasks = []
        for chain in chains:
            for src in ["spring", "delta"]:
                from features.spring.infrastructure.repositories import SprintStateRepository
                chain_dir = os.path.join(project_path, ".cognitive", "projects", project_id, src, chain)
                st = SprintStateRepository().load_dict(chain_dir)
                if st:
                    try:
                        tasks = st.get("tasks", [])
                        if isinstance(tasks, dict):
                            for tid, tdata in tasks.items():
                                merged_tasks.append({"task_id": tid, **(tdata if isinstance(tdata, dict) else {"status": tdata})})
                        elif isinstance(tasks, list):
                            merged_tasks.extend(tasks)
                    except Exception:
                        pass
        plan_dir = os.path.join(project_path, ".cognitive", "projects", project_id, "data")
        os.makedirs(plan_dir, exist_ok=True)
        plan_path = os.path.join(plan_dir, "plan.json")
        with open(plan_path, "w") as f:
            json.dump({"version": "2.0", "tasks": merged_tasks}, f, indent=2)
        return {"project_id": project_id, "tasks_imported": len(merged_tasks), "plan_path": plan_path}
