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
  4. The LLM analyzes and saves via kg_store

Usage:
    walker <project>/kg_extract
    walker <project>/kg_extract '{"scope":"functions"}'
"""

from __future__ import annotations
import json
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.project import ProjectService


def _get_cg(chain_dir: str):
    """Return CognitiveGraphSearcher from chain_dir."""
    from libs.infrastructure import CognitiveGraphSearcher
    paths = ProjectService.resolve_paths(chain_dir)
    return CognitiveGraphSearcher(paths.project_root)


def _project_cg(project_id: str):
    """Resolve CognitiveGraph from project_id (no chain_dir needed)."""
    from libs.infrastructure import CognitiveGraphSearcher
    from libs.path_resolver import get_walker_root
    import json, os
    registry_path = get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        with open(registry_path) as f:
            reg = json.load(f).get("projects", {}).get(project_id, {})
        repo_path = reg.get("repo_path", "")
        if repo_path:
            cog_path = os.path.join(repo_path, ".cognitive", "projects", project_id)
            if os.path.isdir(cog_path):
                return CognitiveGraphSearcher(str(cog_path))
    return None


def kg_extract(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Extract knowledge from code via CPG for the LLM to analyze and persist to CKG.

    No active sprint needed. Uses CPG to understand project structure.
    """
    chain_dir = ctx.chain_dir or ""
    project_id = data.get("_project_id", "")

    if chain_dir:
        try:
            cg = _get_cg(chain_dir)
        except Exception as e:
            return TaskResult.failure(f"CognitiveGraph not available: {e}")
    elif project_id:
        cg = _project_cg(project_id)
        if cg is None:
            return TaskResult.failure("Project not found", data={
                "error": f"Project '{project_id}' not found or has no .cognitive/ data",
                "next_step": "Run 'walker prepare <path>' to register the project first",
            })
    else:
        return TaskResult.failure("No project context", data={
            "error": "No chain_dir and no project_id available",
            "next_step": "Run 'walker <project>/status' first to register project context",
        })

    scope = data.get("scope", "all")  # all | functions | classes | files

    # ── 1. Query CPG ──
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
                analysis["functions"].append({
                    "name": s.get("name", ""),
                    "file": s.get("file", ""),
                    "line": s.get("line", 0),
                })
            r = cg.search("cpg", {"type": "method"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["functions"].append({
                    "name": s.get("name", ""),
                    "file": s.get("file", ""),
                    "line": s.get("line", 0),
                    "method": True,
                })
    except Exception as e:
        analysis["_error_cpg_functions"] = str(e)

    try:
        if scope in ("all", "classes"):
            r = cg.search("cpg", {"type": "class"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["classes"].append({
                    "name": s.get("name", ""),
                    "file": s.get("file", ""),
                    "line": s.get("line", 0),
                })
    except Exception as e:
        analysis["_error_cpg_classes"] = str(e)

    try:
        if scope in ("all", "files"):
            r = cg.search("cpg", {"type": "file"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["files"].append({
                    "path": s.get("file", s.get("name", "")),
                })
            r = cg.search("cpg", {"type": "module"})
            for h in r.get("hits", {}).get("hits", []):
                s = h["_source"]
                analysis["modules"].append({
                    "name": s.get("name", ""),
                    "file": s.get("file", ""),
                })
    except Exception as e:
        analysis["_error_cpg_files"] = str(e)

    # ── 2. Query current CKG (avoid duplicates) ──
    existing_facts = []
    try:
        r = cg.search("ckg", {})
        for h in r.get("hits", {}).get("hits", []):
            s = h["_source"]
            existing_facts.append({
                "type": s.get("type", ""),
                "statement": s.get("statement", ""),
                "scope": s.get("scope", ""),
            })
    except Exception as e:
        analysis["_error_ckg"] = str(e)

    # ── 3. Project stats ──
    total_functions = 0
    total_classes = 0
    total_files = 0
    total_modules = 0
    try:
        r = cg.search("cpg", {"type": "function"})
        total_functions = r.get("hits", {}).get("total", len(analysis["functions"]))
    except Exception:
        pass
    try:
        r = cg.search("cpg", {"type": "class"})
        total_classes = r.get("hits", {}).get("total", len(analysis["classes"]))
    except Exception:
        pass
    try:
        r = cg.search("cpg", {"type": "file"})
        total_files = r.get("hits", {}).get("total", len(analysis["files"]))
    except Exception:
        pass
    try:
        r = cg.search("cpg", {"type": "module"})
        total_modules = r.get("hits", {}).get("total", len(analysis["modules"]))
    except Exception:
        pass

    stats = {
        "total_functions": total_functions,
        "total_classes": total_classes,
        "total_files": total_files,
        "total_modules": total_modules,
        "existing_ckg_facts": len(existing_facts),
    }

    # ── 4. LLM Questionnaire ──
    questionnaire = {
        "instructions": (
            "You received the full code structure via CPG. "
            "Analyze it and answer the following questions. "
            "Your answers will be saved in the CKG (Knowledge Graph) "
            "so they are available in future sprints. "
            "Use 'walker <project>/kg_store' to save each answer. "
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

    return TaskResult.success(data={
        "task": "kg_extract",
        "description": "Code analysis via CPG to extract knowledge",
        "stats": stats,
        "existing_ckg_facts_count": len(existing_facts),
        "existing_ckg_facts": existing_facts[:20],
        "cpg_analysis": analysis,
        "questionnaire": questionnaire,
        "action_required": (
            "Review the CPG analysis and answer each question in the questionnaire. "
            "Save each answer using kg_store: "
            "walker <project>/kg_store '{\"type\":\"<type>\",\"statement\":\"<answer>\",\"scope\":\"<scope>\"}'"
        ),
    })


def register(registry: Any) -> None:
    registry.register("kg_extract", kg_extract, metadata={
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
                "Investigating a bug without CKG context -> kg_extract + analyze + kg_store",
                "After implementing a task -> kg_extract to document learnings",
            ],
            "synergy": [
                "kg_extract -> kg_store saves answers to CKG",
                "kg_extract -> kg_search queries generated knowledge",
                "kg_extract -> kg_multi cross-references CKG + CPG for full context",
            ],
        },
        "tags": ["kg", "cognitive-graph", "ckg", "extract"],
        "purpose": ["ckg_populate", "ckg_extract"],
    })
