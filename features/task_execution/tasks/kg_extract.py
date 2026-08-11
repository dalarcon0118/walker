"""kg_extract — Extract knowledge from code via CPG and guide the LLM to populate CKG.

Flow:
  1. Reads the full CPG (functions, classes, files, modules)
  2. Reads the current CKG (to avoid duplicates)
  3. Returns a structured analysis for the LLM to:
     a) Identify architecture patterns
     b) Detect coding conventions
     c) Extract evident design decisions
     d) Document components and relationships
     e) Capture implicit business rules
  4. The LLM analyzes and saves via save_to_ckg

Usage:
    walker <project>/kg_extract
    walker <project>/kg_extract '{"scope":"functions"}'
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput
from features.project import ProjectService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KgExtractInput(TaskInput):
    """Input validado de kg_extract.

    project_id: id del proyecto (alternativa a chain_dir).
    scope: all | functions | classes | files (default all).
    """

    project_id: str = ""
    scope: str = "all"

    def _validate(self) -> str:
        if self.scope not in ("all", "functions", "classes", "files"):
            return "scope debe ser 'all' | 'functions' | 'classes' | 'files'"
        return ""


def _get_cg(chain_dir: str):
    """Return searcher from chain_dir, resuelto por DI (clave cognitive_searcher)."""
    from features.engine.bootstrap import get_module_service

    paths = ProjectService.resolve_paths(chain_dir)
    searcher_factory = get_module_service("cognitive_searcher")
    return searcher_factory(paths.project_root)


def _project_cg(project_id: str):
    """Resolve searcher from project_id (no chain_dir needed) via path_resolver DI."""
    from features.engine.bootstrap import get_module_service
    import json
    import os

    path_resolver = get_module_service("path_resolver")
    registry_path = path_resolver.get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        with open(registry_path) as f:
            reg = json.load(f).get("projects", {}).get(project_id, {})
        repo_path = reg.get("repo_path", "")
        if repo_path:
            cog_path = os.path.join(repo_path, ".cognitive", "projects", project_id)
            if os.path.isdir(cog_path):
                searcher_factory = get_module_service("cognitive_searcher")
                return searcher_factory(str(cog_path))
    return None


def _query_cpg_analysis(cg: Any, scope: str) -> dict:
    """Consulta el CPG: functions, classes, files, modules segun scope."""
    analysis = {
        "functions": [],
        "classes": [],
        "files": [],
        "modules": [],
    }

    try:
        if scope in ("all", "functions"):
            r = cg.search("cpg", {"type": "function"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["functions"].append(
                    {
                        "name": s.get("name", ""),
                        "file": s.get("file", ""),
                        "line": s.get("line", 0),
                    }
                )
            r = cg.search("cpg", {"type": "method"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["functions"].append(
                    {
                        "name": s.get("name", ""),
                        "file": s.get("file", ""),
                        "line": s.get("line", 0),
                        "method": True,
                    }
                )
    except Exception as e:
        analysis["_error_cpg_functions"] = str(e)

    try:
        if scope in ("all", "classes"):
            r = cg.search("cpg", {"type": "class"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["classes"].append(
                    {
                        "name": s.get("name", ""),
                        "file": s.get("file", ""),
                        "line": s.get("line", 0),
                    }
                )
    except Exception as e:
        analysis["_error_cpg_classes"] = str(e)

    try:
        if scope in ("all", "files"):
            r = cg.search("cpg", {"type": "file"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["files"].append({"path": s.get("file", s.get("name", ""))})
            r = cg.search("cpg", {"type": "module"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["modules"].append(
                    {"name": s.get("name", ""), "file": s.get("file", "")}
                )
    except Exception as e:
        analysis["_error_cpg_files"] = str(e)

    return analysis


def _query_existing_ckg(cg: Any, analysis: dict) -> list[dict]:
    """Consulta el CKG actual para evitar duplicados."""
    existing_facts = []
    try:
        r = cg.search("ckg", {})
        for h in r.get("hits", {}).get("hits", []):
            s = h["_source"]
            existing_facts.append(
                {
                    "type": s.get("type", ""),
                    "statement": s.get("statement", ""),
                    "scope": s.get("scope", ""),
                }
            )
    except Exception as e:
        analysis["_error_ckg"] = str(e)
    return existing_facts


def _query_project_stats(cg: Any, analysis: dict, existing_facts: list) -> dict:
    """Consulta los totales del proyecto para el resumen."""
    counts = {"function": 0, "class": 0, "file": 0, "module": 0}
    totals = {
        "function": len(analysis["functions"]),
        "class": len(analysis["classes"]),
        "file": len(analysis["files"]),
        "module": len(analysis["modules"]),
    }
    for ntype in counts:
        try:
            r = cg.search("cpg", {"type": ntype})
            counts[ntype] = r.get("hits", {}).get("total", totals[ntype])
        except Exception as e:
            logger.warning(
                "kg_extract: total de '%s' fallo (%s: %s); se usa el local",
                ntype, type(e).__name__, e,
            )
    return {
        "total_functions": counts["function"],
        "total_classes": counts["class"],
        "total_files": counts["file"],
        "total_modules": counts["module"],
        "existing_ckg_facts": len(existing_facts),
    }


def kg_extract(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Extract knowledge from code via CPG for the LLM to analyze and persist to CKG.

    No active sprint needed. Uses CPG to understand project structure.
    """
    chain_dir = ctx.chain_dir or ""
    input_result = KgExtractInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "input inválido")
    inp: KgExtractInput = input_result.data
    project_id = inp.project_id or data.get("_project_id", "")
    scope = inp.scope

    if chain_dir:
        try:
            cg = _get_cg(chain_dir)
        except Exception as e:
            return TaskResult.failure(f"CognitiveGraph not available: {e}")
    elif project_id:
        cg = _project_cg(project_id)
        if cg is None:
            return TaskResult.failure(
                "Project not found",
                data={
                    "error": f"Project '{project_id}' not found or has no .cognitive/ data",
                    "next_step": "Run 'walker prepare <path>' to register the project first",
                },
            )
    else:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "No chain_dir and no project_id available",
                "next_step": "Run 'walker <project>/status' first to register project context",
            },
        )

    # ── 1. Query CPG ──
    analysis = _query_cpg_analysis(cg, scope)

    # ── 2. Query current CKG (avoid duplicates) ──
    existing_facts = _query_existing_ckg(cg, analysis)

    # ── 3. Project stats ──
    stats = _query_project_stats(cg, analysis, existing_facts)

    # ── 4. LLM Questionnaire ──
    questionnaire = {
        "instructions": (
            "You received the full code structure via CPG. "
            "Analyze it and answer the following questions. "
            "Your answers will be saved in the CKG (Knowledge Graph) "
            "so they are available in future sprints. "
            "Use 'walker <project>/save_to_ckg' to save each answer. "
            "IMPORTANT: Do NOT save knowledge that already exists in CKG."
        ),
        "questions": [
            {
                "id": "architecture_patterns",
                "question": "What architecture patterns do you identify?",
                "examples": [
                    "Clean Architecture / DDD / Hexagonal",
                    "MVC / MVP / MVVM",
                    "Microservices / Modular monolith",
                    "Event-driven / CQRS",
                ],
                "ckg_type": "decision",
                "scope": "architecture",
            },
            {
                "id": "coding_conventions",
                "question": "What coding conventions do you observe?",
                "examples": [
                    "Naming conventions (snake_case, camelCase, PascalCase)",
                    "File structure (one file per class, per function)",
                    "Use of type hints / docstrings",
                    "Import style (absolute, relative)",
                ],
                "ckg_type": "convention",
                "scope": "coding-style",
            },
            {
                "id": "design_decisions",
                "question": "What design decisions are evident from the code?",
                "examples": [
                    "Why X technology was used over Y",
                    "How dependency injection is handled",
                    "Testing strategy",
                    "Error / result handling",
                ],
                "ckg_type": "decision",
                "scope": "design",
            },
            {
                "id": "key_components",
                "question": "What are the key components and their relationships?",
                "examples": [
                    "Main modules and their responsibilities",
                    "Data flow between components",
                    "Module dependencies",
                    "Extension / plugin points",
                ],
                "ckg_type": "component",
                "scope": "architecture",
            },
            {
                "id": "business_rules",
                "question": "What implicit business rules do you detect?",
                "examples": [
                    "Security validations",
                    "Authorization rules",
                    "Business logic in services vs entities",
                    "State and transition management",
                ],
                "ckg_type": "rule",
                "scope": "business",
            },
            {
                "id": "data_flow",
                "question": "How does data flow through the system?",
                "examples": [
                    "Layers: API -> UseCase -> Repository -> DB",
                    "Events: Producer -> Broker -> Consumer",
                    "Data transformations",
                    "Caching strategy",
                ],
                "ckg_type": "pattern",
                "scope": "data-flow",
            },
        ],
    }

    return TaskResult.success(
        data={
            "task": "kg_extract",
            "description": "Code analysis via CPG to extract knowledge",
            "stats": stats,
            "existing_ckg_facts_count": len(existing_facts),
            "existing_ckg_facts": existing_facts[:20],
            "cpg_analysis": analysis,
            "questionnaire": questionnaire,
            "action_required": (
                "Review the CPG analysis and answer each question in the questionnaire. "
                "Save each answer using save_to_ckg: "
                'walker <project>/save_to_ckg \'{"type":"<type>","statement":"<answer>","scope":"<scope>"}\''
            ),
        }
    )


def register(registry: Any) -> None:
    registry.register(
        "kg_extract",
        kg_extract,
        metadata={
            "description": "Extract knowledge from code via CPG to populate CKG (no sprint needed)",
            "usage": "walker <project>/kg_extract",
            "example": "walker <project>/kg_extract",
            "query_instructions": (
                "Returns: (1) full CPG analysis, (2) current CKG, "
                "(3) questionnaire for the LLM to answer and save to CKG."
            ),
            "use_cases": {
                "user": [
                    "Just registered a new project -> kg_extract to populate initial CKG",
                    "Seeing 'CKG is empty' in status -> kg_extract to generate knowledge",
                    "After a large refactor -> kg_extract to update CKG",
                    "Want to know what walker knows about the code -> kg_extract + kg_search",
                ],
                "ai_agent": [
                    "status shows 'CKG empty' -> run kg_extract automatically",
                    "Before planning a sprint -> kg_extract to understand architecture",
                    "Investigating a bug without CKG context -> kg_extract + analyze + save_to_ckg",
                    "After implementing a task -> kg_extract to document learnings",
                ],
                "synergy": [
                    "kg_extract -> save_to_ckg saves answers to CKG",
                    "kg_extract -> kg_search queries generated knowledge",
                    "kg_extract -> kg_multi cross-references CKG + CPG for full context",
                ],
            },
            "tags": ["kg", "cognitive-graph", "ckg", "extract"],
            "purpose": ["ckg_populate", "ckg_extract"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "ID del proyecto (alternativa a chain_dir)", "example": "mi-proyecto"},
                    "scope": {"type": "string", "description": "Alcance del análisis: all | functions | classes | files (default all)", "example": "functions"},
                },
                "required": [],
            },
        },
    )
