"""use_cases/health_report_analyzer.py — HealthReportAnalyzer.

Analiza el health report y genera next steps priorizados.
SRP: Given a health report, produce actionable recommendations.

NO hardcodea nombres de task: las tareas se descubren dinamicamente
desde el TaskRegistry (task_catalog) buscando por 'purpose' (capacidad).
Si una capacidad no tiene task registrada, el paso se omite o se emite
sin comando — nunca se inventa una task inexistente.
"""
from __future__ import annotations


class HealthReportAnalyzer:
    """Analyze health report and generate prioritized next steps."""

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
                        f"(e.g. 'walker {project_id}/submit {{\"decision\":\"...\",\"evidence\":\"...\"}}')",
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
