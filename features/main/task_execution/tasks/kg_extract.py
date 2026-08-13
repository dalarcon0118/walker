"""kg_extract — Extract knowledge from code via CPG and guide the LLM to populate CKG.

Flow:
  1. Reads the full CPG (functions, classes, files, modules) via CpgQueryService
  2. Reads the current CKG (to avoid duplicates) via CodeKnowledgeGraph
  3. Returns a structured analysis for the LLM to:
     a) Identify architecture patterns
     b) Detect coding conventions
     c) Extract evident design decisions
     d) Document components and relationships
     e) Capture implicit business rules
  4. The LLM analyzes and saves via save_to_ckg

Composición (D3): la task es un adapter delgado — el acceso a datos pasa por
los SERVICIOS DE MÓDULO, nunca por libs ni por el Searcher DI:
  - CPG → cpg_service(chain_dir) → CpgQueryService.grep (features.code_analysis)
  - CKG → ckg_service(project_root) → CodeKnowledgeGraph.query (features.cognitive_knowledge)
  - project_id → ProjectService.from_project_id (features.project)

CONTRATO DE SALIDA (vs la versión previa con Searcher DI):
  - CKG: query() del módulo excluye nodos superseded/deprecated por defecto
    (el Searcher crudo devolvía todos los docs) — deseable para "evitar
    duplicados", pero es un filtro activo, no un dump.
  - cpg_analysis: ante fallo parcial de un tipo se devuelve lista vacía y se
    loggea (patrón returns); ya no se inyectan claves _error_cpg_* en el
    output. _error_ckg sí se preserva por compatibilidad con consumidores.

Usage:
    walker <project>/kg_extract
    walker <project>/kg_extract '{"scope":"functions"}'
"""

from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from shared.common.shared_domain import ExecutionContext, TaskResult
from shared.common.shared_domain.validated_vo import TaskInput
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


# ── Composición: servicios de módulo (composition roots) ──────────────


@safe
def _resolve_cpg_core(chain_dir: str):
    """Core puro: resuelve el CpgQueryService del módulo (puede lanzar)."""
    from features.code_analysis import cpg_service

    return cpg_service(chain_dir=chain_dir)


@safe
def _resolve_ckg_core(project_root: str):
    """Core puro: resuelve el CodeKnowledgeGraph del módulo (puede lanzar)."""
    from features.cognitive_knowledge import ckg_service

    return ckg_service(project_root)


@safe
def _resolve_project_root_core(project_id: str) -> str:
    """Core puro: project_root desde project_id via ProjectService (puede lanzar)."""
    project = ProjectService.from_project_id(project_id)
    return str(project.paths.project_root)


@safe
def _resolve_services_core(chain_dir: str, project_id: str) -> tuple[Any, Any, str]:
    """Core puro: resuelve los servicios de módulo CPG + CKG (puede lanzar).

    Punto único de composición: los consumidores no deciden cómo se arma el
    CpgQueryService ni el CodeKnowledgeGraph — delegan en los composition
    roots de cada módulo (cpg_service / ckg_service).

    Returns:
        (cpg, ckg, project_root) — cpg/ckg pueden ser None si no hay
        contexto resolvible; project_root se usa solo como diagnóstico.
    """
    project_root = ""
    if chain_dir:
        from features.project import ProjectService as _PS

        paths = _PS.resolve_paths(chain_dir)
        project_root = str(paths.project_root)
    elif project_id:
        project_root = _safe_build(
            _resolve_project_root_core(project_id), "project_root"
        ) or ""

    if not project_root or not os.path.isdir(project_root):
        return None, None, project_root

    # CpgQueryService se construye desde un chain_dir O desde el project_root
    # (que es el dir cognitivo del proyecto: <repo>/.cognitive/projects/<pid>
    # con data/project.json). cpg_service resuelve paths en ambos casos.
    cpg_context = chain_dir if chain_dir else project_root
    return _safe_build(_resolve_cpg_core(cpg_context), "CPG"), _safe_build(
        _resolve_ckg_core(project_root), "CKG"
    ), project_root


def _resolve_services(
    chain_dir: str = "", project_id: str = ""
) -> tuple[Any | None, Any | None, str]:
    """Wrapper: decide con is_successful y loggea el fallo (convention-001)."""
    result = _resolve_services_core(chain_dir, project_id)
    if is_successful(result):
        return result.value_or((None, None, ""))
    error = result.failure()
    logger.warning(
        "kg_extract: no se pudo resolver el proyecto (%s: %s)",
        type(error).__name__, error,
    )
    return None, None, ""


def _safe_build(result, label: str):
    """Wrapper: decide con is_successful y loggea el fallo (convention-001)."""
    if is_successful(result):
        return result.value_or(None)
    error = result.failure()
    logger.warning(
        "kg_extract: %s no disponible (%s: %s)", label, type(error).__name__, error,
    )
    return None


# ── Consultas CPG via CpgQueryService (módulo code_analysis) ──────────


@safe
def _query_cpg_analysis_core(cpg: Any, scope: str) -> dict:
    """Core puro: consulta el CPG por tipo via grep del módulo (puede lanzar).

    CpgQueryService.grep("type:function") devuelve matches con el shape
    {type, name, file, line} — el contrato del módulo code_analysis.
    """
    analysis = {
        "functions": [],
        "classes": [],
        "files": [],
        "modules": [],
    }

    if scope in ("all", "functions"):
        r = cpg.grep("type:function")
        for m in r.get("matches", []):
            analysis["functions"].append(
                {
                    "name": m.get("name", ""),
                    "file": m.get("file", ""),
                    "line": m.get("line", 0),
                }
            )
        r = cpg.grep("type:method")
        for m in r.get("matches", []):
            analysis["functions"].append(
                {
                    "name": m.get("name", ""),
                    "file": m.get("file", ""),
                    "line": m.get("line", 0),
                    "method": True,
                }
            )

    if scope in ("all", "classes"):
        r = cpg.grep("type:class")
        for m in r.get("matches", []):
            analysis["classes"].append(
                {
                    "name": m.get("name", ""),
                    "file": m.get("file", ""),
                    "line": m.get("line", 0),
                }
            )

    if scope in ("all", "files"):
        r = cpg.grep("type:file")
        for m in r.get("matches", []):
            analysis["files"].append(
                {"path": m.get("file", m.get("name", ""))}
            )
        r = cpg.grep("type:module")
        for m in r.get("matches", []):
            analysis["modules"].append(
                {"name": m.get("name", ""), "file": m.get("file", "")}
            )

    return analysis


def _query_cpg_analysis(cpg: Any, scope: str) -> dict:
    """Consulta el CPG con fallback explícito (Result pattern)."""
    result = _query_cpg_analysis_core(cpg, scope)
    if is_successful(result):
        return result.value_or({
            "functions": [], "classes": [], "files": [], "modules": [],
        })
    error = result.failure()
    logger.warning(
        "kg_extract: análisis CPG falló (%s: %s)", type(error).__name__, error,
    )
    return {"functions": [], "classes": [], "files": [], "modules": []}


# ── Consultas CKG via CodeKnowledgeGraph (módulo cognitive_knowledge) ──


@safe
def _query_existing_ckg_core(ckg: Any) -> list[dict]:
    """Core puro: consulta el CKG actual via query del módulo (puede lanzar)."""
    existing_facts = []
    for s in ckg.query({}):
        existing_facts.append(
            {
                "type": s.get("type", ""),
                "statement": s.get("statement", ""),
                "scope": s.get("scope", ""),
            }
        )
    return existing_facts


def _query_existing_ckg(ckg: Any, analysis: dict) -> list[dict]:
    """Consulta el CKG actual con fallback explícito (Result pattern)."""
    result = _query_existing_ckg_core(ckg)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    analysis["_error_ckg"] = str(error)
    logger.warning(
        "kg_extract: CKG query falló (%s: %s)", type(error).__name__, error,
    )
    return []


# ── Totales ────────────────────────────────────────────────────────────


@safe
def _grep_total_core(cpg: Any, ntype: str) -> int:
    """Core puro: total de nodos de un tipo via grep del módulo (puede lanzar)."""
    r = cpg.grep(f"type:{ntype}")
    return int(r.get("total", 0))


def _grep_total_safe(cpg: Any, ntype: str, fallback: int) -> int:
    """Wrapper: total con fallback explícito al conteo local (convention-001)."""
    result = _grep_total_core(cpg, ntype)
    if is_successful(result):
        return result.value_or(fallback)
    error = result.failure()
    logger.warning(
        "kg_extract: total de '%s' falló (%s: %s); se usa el local",
        ntype, type(error).__name__, error,
    )
    return fallback


@safe
def _query_project_stats_core(cpg: Any, analysis: dict, existing_facts: list) -> dict:
    """Core puro: consulta los totales del proyecto (puede lanzar)."""
    counts = {"function": 0, "class": 0, "file": 0, "module": 0}
    totals = {
        "function": len(analysis["functions"]),
        "class": len(analysis["classes"]),
        "file": len(analysis["files"]),
        "module": len(analysis["modules"]),
    }
    for ntype in counts:
        counts[ntype] = _grep_total_safe(cpg, ntype, totals[ntype])
    return {
        "total_functions": counts["function"],
        "total_classes": counts["class"],
        "total_files": counts["file"],
        "total_modules": counts["module"],
        "existing_ckg_facts": len(existing_facts),
    }


def _query_project_stats(cpg: Any, analysis: dict, existing_facts: list) -> dict:
    """Consulta los totales con fallback explícito (Result pattern)."""
    result = _query_project_stats_core(cpg, analysis, existing_facts)
    if is_successful(result):
        return result.value_or({
            "total_functions": 0, "total_classes": 0, "total_files": 0,
            "total_modules": 0, "existing_ckg_facts": len(existing_facts),
        })
    error = result.failure()
    logger.warning(
        "kg_extract: stats falló (%s: %s)", type(error).__name__, error,
    )
    return {
        "total_functions": len(analysis.get("functions", [])),
        "total_classes": len(analysis.get("classes", [])),
        "total_files": len(analysis.get("files", [])),
        "total_modules": len(analysis.get("modules", [])),
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

    cpg, ckg, project_root = _resolve_services(chain_dir=chain_dir, project_id=project_id)
    if cpg is None or ckg is None:
        if project_id:
            return TaskResult.failure(
                "Project not found",
                data={
                    "error": f"Project '{project_id}' not found or has no .cognitive/ data",
                    "next_step": "Run 'walker prepare <path>' to register the project first",
                },
            )
        return TaskResult.failure(
            "No project context",
            data={
                "error": f"No chain_dir and no project_id available (project_root='{project_root}')",
                "next_step": "Run 'walker <project>/status' first to register project context",
            },
        )

    # ── 1. Query CPG ──
    analysis = _query_cpg_analysis(cpg, scope)

    # ── 2. Query current CKG (avoid duplicates) ──
    existing_facts = _query_existing_ckg(ckg, analysis)

    # ── 3. Project stats ──
    stats = _query_project_stats(cpg, analysis, existing_facts)

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
