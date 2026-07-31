"""Context providers — implementaciones concretas de ContextProvider.

Cada provider representa una CAPA de datos que el nodo puede declarar.
Los providers que cargan datos de capas indexadas (CPG, CKG, Trace, State, Delta)
DELEGAN en QueryManager. El TaskProvider lee plan.json directamente.

Estructura de cada provider:
  - instructions(): ENSEÑA al LLM qué contiene la capa y cómo construir queries
  - collect(): obtiene datos y los formatea como ContextPiece
  - LAYER (optativo): nombre de la capa en QueryManager si aplica
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Any

from .models import ContextPiece
from .registry import ContextProvider

logger = logging.getLogger(__name__)


# ── Helpers compartidos ──────────────────────────────────────────────


def _fmt_tokens(content: str) -> int:
    """Estimación de tokens (~4 chars/token)."""
    return len(content) // 4


def _format_ckg_results(result: dict, max_nodes: int = 20) -> str:
    """Formatea resultados de CKG como texto estructurado."""
    results = result.get("results", [])
    if not results:
        return "(no matching CKG nodes)"

    lines = []
    for r in results[:max_nodes]:
        ntype = r.get("type", "?")
        label = r.get("label", r.get("id", "?"))
        snippet = r.get("snippet", "")
        lines.append(f"  [{ntype}] {label}")
        if snippet:
            s = snippet[:120] + "..." if len(snippet) > 120 else snippet
            lines.append(f"    {s}")
    if len(results) > max_nodes:
        lines.append(f"  ... (+{len(results) - max_nodes} more)")
    return "\n".join(lines)


def _format_cpg_results(result: dict, max_results: int = 30) -> str:
    """Formatea resultados de CPG como texto estructurado."""
    # Los resultados vienen como {ok, data: {total, matches}} desde QueryEngine
    data = result if "matches" in result else result.get("data", {})
    matches = data.get("matches", [])
    if not matches:
        return "(no matching CPG nodes)"

    lines = []
    for m in matches[:max_results]:
        ntype = m.get("type", "?")
        name = m.get("name", "?")
        file_path = m.get("file", "")
        line = m.get("line", 0)
        lines.append(f"  {ntype} {name} ({file_path}:{line})")
        # Relaciones si existen
        rels = m.get("relations", {})
        for rel_type, targets in rels.items():
            preview = ", ".join(targets[:3])
            lines.append(f"    {rel_type}: {preview}")
        # Métricas si existen
        cyclo = m.get("cyclo")
        if cyclo:
            lines.append(f"    cyclomatic: {cyclo}")
    if len(matches) > max_results:
        lines.append(f"  ... (+{len(matches) - max_results} more)")
    return "\n".join(lines)


def _format_trace_results(result: dict, max_events: int = 8) -> str:
    """Formatea resultados de Trace como texto cronológico."""
    # Los resultados pueden venir como {ok, data: {results: {...}}} de QueryEngine
    data = result if "results" in result else result.get("data", {})
    all_results = []
    for items in data.get("results", {}).values():
        all_results.extend(items)
    if not all_results:
        return "(no trace events)"

    # Ordenar por timestamp si existe
    all_results.sort(
        key=lambda e: e.get("timestamp", e.get("at", "")),
        reverse=True,
    )
    lines = []
    for evt in all_results[:max_events]:
        ts = evt.get("timestamp", evt.get("at", ""))
        etype = evt.get("event_type", evt.get("type", "event"))
        data_str = json.dumps(evt.get("input", evt.get("message", "")))
        if len(data_str) > 150:
            data_str = data_str[:150] + "..."
        lines.append(f"  [{ts}] {etype}: {data_str}")
    if len(all_results) > max_events:
        lines.append(f"  ... (+{len(all_results) - max_events} more)")
    return "\n".join(lines)


def _format_state_result(result: dict) -> str:
    """Formatea resultado de State como YAML-like."""
    lines = []
    if "node" in result:
        lines.append(f"node: {result.get('node', '?')}")
    if "task_id" in result:
        lines.append(f"task_id: {result.get('task_id', '')}")
    if "sprint_id" in result:
        lines.append(f"sprint_id: {result.get('sprint_id', '')}")
    if "sprint_goal" in result and result.get("sprint_goal"):
        lines.append(f"sprint_goal: {result.get('sprint_goal', '')}")
    if "sprint_status" in result and result.get("sprint_status"):
        lines.append(f"sprint_status: {result.get('sprint_status', '')}")
    if "match" in result:
        lines.append(f"match: {result.get('match', False)}")
    history = result.get("history", [])
    if history:
        lines.append(f"history: {len(history)} events")
    return "\n".join(lines)


def _format_delta_results(result: dict) -> str:
    """Formatea resultados de DeltaGraph como texto."""
    data = result if "results" in result else result.get("data", {})
    all_results = []
    for items in data.get("results", {}).values():
        all_results.extend(items)
    if not all_results:
        return "(no delta results)"

    lines = []
    for r in all_results[:15]:
        task_id = r.get("task_id", r.get("id", "?"))
        objective = r.get("objective", r.get("description", ""))[:100]
        status = r.get("status", "?")
        lines.append(f"  {task_id} [{status}] {objective}")
    if len(all_results) > 15:
        lines.append(f"  ... (+{len(all_results) - 15} more)")
    return "\n".join(lines)


# ── Providers ────────────────────────────────────────────────────────


class TaskProvider(ContextProvider):
    """Capa: especificación completa de la tarea desde plan.json (22 campos del schema).

    NO usa QueryManager — lee plan.json directamente del chain_dir.
    Proporciona TODOS los campos del schema de tarea: identificación, roles,
    clasificación, dependencias, validación, impacto, conocimiento.

    Esta capa debe leerse FIRST antes de consultar CPG o CKG.
    """

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: task — full 22-field specification (objective, context, decisions, validation, risks, artifacts). Read FIRST."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        chain_dir = getattr(ctx, "chain_dir", None)
        if not chain_dir:
            return None

        plan_path: str = os.path.join(chain_dir, "plan.json")
        if not os.path.exists(plan_path):
            return None

        try:
            with open(plan_path) as f:
                plan: dict = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.error("TaskProvider: failed to parse plan.json at %s", plan_path)
            return None

        # Resolver task_id: config > ctx > primera tarea del plan
        task_id = config.get("task_id", "") or getattr(ctx, "task_id", "")
        tasks: list[dict] = plan.get("tasks", [])
        task: dict | None = None

        if task_id:
            for t in tasks:
                if t.get("task_id") == task_id:
                    task = t
                    break

        if not task and tasks:
            task = tasks[0]
            task_id = task.get("task_id", "")

        if not task:
            return None

        # Construir contenido compacto — todos los campos, sin anidamiento YAML
        parts: list[str] = []

        # Escalares y listas planas
        for field in (
            "task_id",
            "objective",
            "type",
            "domain",
            "result_state",
            "task_description",
            "role",
            "scrum_role",
            "work_type",
            "importer_files",
            "depends_on",
            "preconditions",
            "acceptance_criteria",
            "quality_gates",
            "loop_verification",
            "loop_acceptance",
        ):
            val = task.get(field)
            if val is None:
                continue
            if isinstance(val, list):
                parts.append(f"{field}: {', '.join(str(v) for v in val)}")
            else:
                parts.append(f"{field}: {val}")

        # validation → inline predicates
        val_obj = task.get("validation")
        if val_obj:
            preds = val_obj.get("predicates", [])
            if preds:
                parts.append(f"validation: {' • '.join(preds)}")

        # impacted_artifacts → compacto: files=[...] tests=[...]
        art_obj = task.get("impacted_artifacts")
        if art_obj:
            art_parts = []
            for sub_key in ("files", "apis", "data", "config", "tests", "docs"):
                items = art_obj.get(sub_key, [])
                if items:
                    art_parts.append(f"{sub_key}=[{', '.join(items)}]")
            if art_parts:
                parts.append(f"impacted_artifacts: {' '.join(art_parts)}")

        # risks → compacto
        risks = task.get("risks")
        if risks:
            risk_parts = [f"{k}={v}" for k, v in risks.items()]
            parts.append(f"risks: {' '.join(risk_parts)}")

        # decisions → una línea por decisión
        decisions = task.get("decisions")
        if decisions:
            for d in decisions:
                chosen = d.get("chosen", d.get("decision", ""))
                alt = d.get("alternatives", [])
                rationale = d.get("rationale", d.get("razon", ""))
                line = f"  • {chosen}"
                if alt:
                    line += f" | alts: {', '.join(alt[:5])}"
                if rationale:
                    line += f" | why: {rationale}"
                parts.append(line)

        # refinement → compacto
        ref = task.get("refinement")
        if ref:
            attempts = ref.get("attempts", 0)
            dead_ends = ref.get("dead_ends", [])
            ref_line = f"refinement: attempts={attempts}"
            if dead_ends:
                ref_line += f" dead_ends=[{'; '.join(dead_ends)}]"
            parts.append(ref_line)

        # task_context → inline pipe-separated
        tc = task.get("task_context")
        if tc:
            tc_parts = []
            for sub_key in (
                "observation",
                "structure",
                "behavior",
                "causality",
                "purpose",
                "principles",
            ):
                sub_val = tc.get(sub_key, "")
                if sub_val:
                    tc_parts.append(f"{sub_key}={sub_val}")
            if tc_parts:
                parts.append(f"task_context: {' | '.join(tc_parts)}")

        content = "\n".join(parts)
        return ContextPiece(
            provider="task",
            name="task specification",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=5,
        )


class StateProvider(ContextProvider):
    """Capa: estado del sprint actual. Layer: state.

    Delega en QueryManager para consultar sprint-state.json.
    """

    LAYER = "state"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: state — current workflow position, sprint goal, task inventory, project identity."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None
        filters = config.get("query", {})
        if isinstance(filters, str):
            filters = {}
        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = _format_state_result(result)
        return ContextPiece(
            provider="state",
            name="sprint state",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=0,
        )


class CKGProvider(ContextProvider):
    """Capa: Cognitive Knowledge Graph — conocimiento estructurado. Layer: ckg.

    Delega en QueryManager para consultar el CKG.
    """

    LAYER = "ckg"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: ckg — project knowledge graph (decisions, patterns, conventions, warnings). Check patterns BEFORE implementing."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        task_id = getattr(ctx, "task_id", "")
        raw_query = config.get("query", {})

        # Resolver placeholders en la query
        if isinstance(raw_query, dict):
            filters = dict(raw_query)
        elif isinstance(raw_query, str):
            filters = {}
            # Simple string query: convertir a filtro de texto
            q = raw_query.replace("{task_id}", task_id)
            if q:
                filters = {"label": q}
        else:
            filters = {}

        max_nodes = config.get("max_nodes", 20)
        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = _format_ckg_results(result, max_nodes)
        return ContextPiece(
            provider="ckg",
            name=f"ckg: {filters}",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=10,
        )


class CPGProvider(ContextProvider):
    """Capa: Code Property Graph — código fuente estructurado. Layer: cpg.

    Reemplaza a FileSystemProvider. En vez de listar archivos para que
    el LLM los lea, entrega datos estructurados del código (funciones,
    clases, imports, llamadas, bugs).
    """

    LAYER = "cpg"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: cpg — structured source code (functions, classes, calls, imports). REPLACES raw file reading."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        task_id = getattr(ctx, "task_id", "")
        raw_query = config.get("query", {})
        max_results = config.get("max_results", 30)

        if isinstance(raw_query, dict):
            filters = dict(raw_query)
        elif isinstance(raw_query, str):
            filters = {}
            q = raw_query.replace("{task_id}", task_id)
            if q:
                filters = {"text": q}
        else:
            filters = {}

        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = _format_cpg_results(result, max_results)
        return ContextPiece(
            provider="cpg",
            name=f"cpg: {filters}",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=30,
        )


class TraceProvider(ContextProvider):
    """Capa: TraceGraph — historial de interacciones. Layer: trace.

    Delega en QueryManager para consultar TraceGraph/indexed traces.
    """

    LAYER = "trace"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: trace — session history (tool calls, messages, file changes, state transitions). Use to detect failure patterns."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        task_id = getattr(ctx, "task_id", "")
        raw_query = config.get("query", {})
        max_events = config.get("max_events", 8)

        if isinstance(raw_query, dict):
            filters = dict(raw_query)
        elif isinstance(raw_query, str):
            filters = {}
            q = raw_query.replace("{task_id}", task_id)
            if q:
                filters = {"task": q}
        else:
            filters = {}

        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = _format_trace_results(result, max_events)
        return ContextPiece(
            provider="trace",
            name="session history",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=20,
        )


class DeltaProvider(ContextProvider):
    """Capa: DeltaGraph — contexto federado de tareas. Layer: delta.

    Opcional. Proporciona contexto histórico de tareas multi-sprint.
    """

    LAYER = "delta"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: delta — cross-sprint task history (objectives, statuses, dependencies, file impact). Check for prior similar work."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        raw_query = config.get("query", {})
        if isinstance(raw_query, dict):
            filters = dict(raw_query)
        else:
            filters = {}

        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = _format_delta_results(result)
        return ContextPiece(
            provider="delta",
            name="task history",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=40,
        )


def _derive_project_id(chain_dir: str) -> str | None:
    """Deriva el project_id desde chain_dir.

    chain_dir típico: .cognitive/projects/<project_id>/delta/chain-xxx/
    Subimos 3 niveles desde chain_dir hasta projects/, el nombre del dir es el project_id.
    """
    parent = (
        os.path.dirname(os.path.dirname(os.path.dirname(chain_dir)))
        if chain_dir
        else ""
    )
    if parent and parent.endswith("/projects"):
        project_id = os.path.basename(os.path.dirname(os.path.dirname(chain_dir)))
        return project_id
    # Fallback: buscar cualquier projects/<id>/project.json desde chain_dir
    parts = chain_dir.split(os.sep) if chain_dir else []
    for i, part in enumerate(parts):
        if part == "projects" and i + 1 < len(parts):
            return parts[i + 1]
    return None


class ProjectContextProvider(ContextProvider):
    """Capa: contexto y convenciones del proyecto.

    Lee project.json del proyecto activo y extrae las convenciones
    arquitectónicas, de codificación y reglas del proyecto.

    NO usa QueryManager — lee project.json directamente.
    Siempre disponible, incluso sin tarea activa.
    """

    def instructions(self, config: dict, ctx: Any) -> str:
        chain_dir = getattr(ctx, "chain_dir", None)
        if not chain_dir or (
            isinstance(chain_dir, str) and (".walker/sessions" in chain_dir or ".cognitive/sessions" in chain_dir)
        ):
            return ""
        return "Layer: project — architecture style, coding conventions, and design rules of this project. Follow these conventions in ALL work."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        chain_dir = getattr(ctx, "chain_dir", None)

        # ── MODO BOOTSTRAP: no hay proyecto activo → listar proyectos ──
        if not chain_dir or (
            isinstance(chain_dir, str) and (".walker/sessions" in chain_dir or ".cognitive/sessions" in chain_dir)
        ):
            return self._list_all_projects()

        # Encontrar project.json
        project_id = config.get("project_id", "") or _derive_project_id(chain_dir)
        if not project_id:
            return self._list_all_projects()

        # Posibles rutas de project.json
        # chain_dir típico: .../projects/<id>/delta/<chain>/
        # Subimos 2 niveles hasta projects/<id>/
        candidate_base = os.path.dirname(os.path.dirname(chain_dir))
        candidates = [
            os.path.join(candidate_base, "project.json"),
        ]
        # Si candidate_base no termina en <project_id>, intentar desde workspace
        workspace = getattr(ctx, "workspace", getattr(ctx, "repo_path", None))
        if workspace:
            candidates.append(
                os.path.join(
                    workspace, ".cognitive", "projects", project_id, "project.json"
                )
            )

        proj: dict | None = None
        for path in candidates:
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    proj = data.get("project", data)
                    break
                except (json.JSONDecodeError, OSError):
                    continue

        if not proj:
            return None

        # Extraer secciones relevantes
        parts: list[str] = []

        # Identity
        identity = proj.get("identity", {})
        name = identity.get("name", project_id)
        lang = identity.get("language", "")
        framework = identity.get("framework", "")
        parts.append(f"project: {name}")
        if lang:
            parts.append(f"language: {lang}")
        if framework:
            parts.append(f"framework: {framework}")

        # Location
        loc = proj.get("location", {})
        repo = loc.get("repo_path", "")
        code_paths = loc.get("code_paths", [])
        if repo:
            parts.append(f"root: {repo}")
        if code_paths:
            parts.append(f"code_paths: {', '.join(code_paths[:5])}")

        # Architecture patterns
        design = proj.get("design", {})
        patterns = design.get("patterns", [])
        if patterns:
            parts.append(f"architecture: {', '.join(patterns)}")

        # Layer rules (structural conventions)
        layers = design.get("layer_rules", {})
        if layers:
            layer_desc = []
            for layer, dirs in layers.items():
                layer_desc.append(f"{layer}=[{','.join(dirs[:3])}]")
            parts.append(f"layers: {' '.join(layer_desc)}")

        # Coding conventions
        coding = proj.get("coding", {})
        coding_items = []
        for key in (
            "naming_convention",
            "formatter",
            "linter",
            "test_framework",
            "type_checker",
            "import_style",
            "docstring_style",
            "max_line_length",
        ):
            val = coding.get(key)
            if val:
                label = (
                    key.replace("_convention", "")
                    .replace("_framework", "")
                    .replace("_style", "")
                    .replace("_", " ")
                )
                coding_items.append(f"{label}={val}")
        if coding_items:
            parts.append(f"coding: {' '.join(coding_items)}")

        # Forbidden dependencies (hard rules)
        forbidden = design.get("forbidden_dependencies", [])
        if forbidden:
            for rule in forbidden[:5]:
                parts.append(f"rule: {rule}")

        # ADRs (top 3 decisions)
        decisions = design.get("decisions", [])
        if decisions:
            for d in decisions[:3]:
                title = d.get("title", "")
                decision = d.get("decision", "")
                if title and decision:
                    summary = (
                        decision[:120] + "..." if len(decision) > 120 else decision
                    )
                    parts.append(f"adr: {title} — {summary}")

        # Architecture section from project.json
        arch = proj.get("architecture", {})
        module_org = arch.get("module_organization", "")
        if module_org:
            parts.append(f"modules: {module_org[:200]}")

        content = "\n".join(parts)
        return ContextPiece(
            provider="project",
            name=f"project context ({project_id})",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=1,
        )

    def _list_all_projects(self) -> ContextPiece | None:
        """Lista todos los proyectos disponibles (modo bootstrap/START)."""
        try:
            from _walker.cli.project import _list_projects

            projects = _list_projects()
        except Exception:
            return None

        if not projects:
            return ContextPiece(
                provider="project",
                name="projects list",
                instructions="Layer: project — list of available projects.",
                content="(no projects found)",
                tokens=3,
                priority=10,
            )

        lines = ["Available projects:"]
        for p in projects:
            pid = p["project_id"]
            active = [s for s in p["sprints"] if s["status"] == "active"]
            if active:
                lines.append(f"  🟢 {pid} — active sprint: {active[0]['id'][:55]}")
            elif p["sprints"]:
                lines.append(f"  ⚪ {pid} — {len(p['sprints'])} sprints (none active)")
            else:
                lines.append(f"  ⚪ {pid} — (no sprints)")

        lines.append("")
        lines.append("Use 'submit --decision select --project_id <id>' to pick one.")
        lines.append("Use 'submit --decision create' to start a new project.")

        content = "\n".join(lines)
        return ContextPiece(
            provider="project",
            name="projects list",
            instructions="",
            content=content,
            tokens=_fmt_tokens(content),
            priority=10,
        )


class SprintProvider(ContextProvider):
    """Capa: backlog completo del sprint activo.

    Proporciona lista de tareas con estado, goal, métricas de completado.
    NO usa QueryManager — lee sprint-state.json directamente del chain_dir.
    """

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: sprint — full sprint backlog with task statuses, completion metrics, and burndown. Use to understand sprint progress."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        chain_dir = getattr(ctx, "chain_dir", None)
        if not chain_dir:
            return None

        sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
        if not os.path.exists(sprint_state_path):
            return None

        try:
            with open(sprint_state_path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

        parts = []

        # Sprint identity
        sprint_id = state.get("sprint_id", "")
        goal = state.get("sprint_goal", "")
        status = state.get("sprint_status", "")
        parts.append(f'sprint: {sprint_id} — "{goal}"')
        parts.append(f"status: {status}")

        # Task inventory
        tasks = state.get("tasks", [])
        total = len(tasks)
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        in_progress = sum(1 for t in tasks if t.get("status") == "in_progress")
        pending = total - completed - in_progress
        parts.append(
            f"tasks: {total} total • {completed} completed • {in_progress} in_progress • {pending} pending"
        )
        if total > 0:
            parts.append(f"completion: {int(completed / total * 100)}%")

        # Task list with statuses
        if tasks:
            parts.append("")
            parts.append("tasks:")
            for t in tasks:
                tid = t.get("task_id", "?")
                tstatus = t.get("status", "?")
                tobjective = t.get("objective", t.get("task_description", ""))[:80]
                parts.append(f"  {tid} [{tstatus}] {tobjective}")

        content = "\n".join(parts)
        return ContextPiece(
            provider="sprint",
            name="sprint backlog",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=0,
        )


class DiffProvider(ContextProvider):
    """Capa: diff estructurado del código modificado.

    Proporciona archivos modificados, líneas añadidas/eliminadas,
    funciones cambiadas. Lee git diff desde el workspace.
    """

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: diff — code changes (files changed, lines added/removed, modified functions). Use for code review."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        workspace = getattr(ctx, "workspace", getattr(ctx, "repo_path", None))
        if not workspace:
            return None

        base_ref = config.get("base_ref", "HEAD~1")

        try:
            stat_result = subprocess.run(
                ["git", "diff", "--stat", f"{base_ref}..HEAD"],
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=10,
            )
            stat_output = stat_result.stdout.strip()

            diff_result = subprocess.run(
                ["git", "diff", f"{base_ref}..HEAD", "--", "*.py"],
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=10,
            )
            diff_output = diff_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

        if not stat_output:
            return ContextPiece(
                provider="diff",
                name="code diff",
                instructions=self.instructions(config, ctx),
                content="(no changes detected)",
                tokens=5,
                priority=25,
            )

        parts = [stat_output.split("\n")[0]]

        # Parsear archivos modificados del stat
        files = []
        for line in stat_output.split("\n")[1:]:
            line = line.strip()
            if line and not line.startswith(" ") and not line.startswith("\t"):
                fname = line.split("|")[0].strip()
                if fname:
                    files.append(fname)

        if files:
            parts.append("")
            parts.append("files:")
            for f in files[:15]:
                parts.append(f"  • {f}")
            if len(files) > 15:
                parts.append(f"  ... (+{len(files) - 15} more)")

        # Extraer funciones modificadas del diff detallado
        if diff_output:
            parts.append("")
            parts.append("changes (key functions):")
            for line in diff_output.split("\n"):
                clean = line.lstrip("+").strip()
                if clean.startswith(("def ", "class ", "    def ", "    class ")):
                    parts.append(f"  +{clean}")

        content = "\n".join(parts)
        return ContextPiece(
            provider="diff",
            name="code diff",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=25,
        )


class TestResultsProvider(ContextProvider):
    """Capa: resultados estructurados de la ejecución de tests.

    Lee del trace del último QA_RUN y parsea stdout para extraer
    métricas de passed/failed/errors y detalles de fallos.
    """

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: test_results — structured test output (passed/failed, coverage, error messages). Use for QA decisions."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None

        # Query al trace para encontrar el último evento qa_run
        result = query_manager.query_layer("trace", {"event": "qa_run"})
        if not result.get("ok", False):
            return None

        events = []
        for items in result.get("data", {}).get("results", {}).values():
            events.extend(items)

        if not events:
            return None

        latest = events[-1]
        data = latest.get("data", {})

        parts = []
        exit_code = data.get("exit_code", "?")
        stdout = data.get("stdout", "")

        parts.append(f"exit_code: {exit_code}")

        # Parsear resumen pytest del stdout
        passed = 0
        failed = 0
        errors = 0
        for line in stdout.split("\n"):
            nums = re.findall(r"(\d+)\s+(passed|failed|error)", line)
            for num, status in nums:
                if status == "passed":
                    passed = int(num)
                elif status == "failed":
                    failed = int(num)
                elif status == "error":
                    errors = int(num)

        total = passed + failed + errors
        parts.append(
            f"total: {total} • passed: {passed} • failed: {failed} • errors: {errors}"
        )

        # Extraer sección FAILURES del stdout
        if failed > 0 or errors > 0:
            in_failures = False
            failure_lines = []
            for line in stdout.split("\n"):
                if "FAILURES" in line or "FAILED" in line:
                    in_failures = True
                if in_failures:
                    failure_lines.append(line)
                    if len(failure_lines) > 30:
                        break

            if failure_lines:
                parts.append("")
                parts.append("failures:")
                for fl in failure_lines[:25]:
                    parts.append(f"  {fl}")

        content = "\n".join(parts)
        return ContextPiece(
            provider="test_results",
            name="test results",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=_fmt_tokens(content),
            priority=5,
        )
