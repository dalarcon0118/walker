"""extract_knowledge — Extract knowledge from code via CPG and guide the LLM to populate CKG.

Flow:
  1. Resolves CpgService + CKG via the task-layer glue (infrastructure/kg_context)
  2. Delegates the analysis to CkgApplicationService.build_extract_payload
     (cross-feature CPG×CKG + stats + questionnaire — ADR-051 G4)
  3. Returns a structured analysis for the LLM to identify patterns,
     conventions, decisions, components, business rules and data flow
  4. The LLM analyzes and saves via documenting_knowledge

COMPOSICIÓN (D3): la task es un adapter delgado — el acceso a datos pasa por
los SERVICIOS DE MÓDULO, nunca por libs ni por el Searcher DI:
  - CPG → CpgService (features.code_analysis)
  - CKG → CkgApplicationService (features.cognitive_knowledge)
  - project_id → ProjectService (features.project)

CONTRATO DE SALIDA (vs la versión previa con Searcher DI):
  - CKG: query() del módulo excluye nodos superseded/deprecated por defecto.
  - cpg_analysis: ante fallo parcial de un tipo se devuelve lista vacía y se
    loggea (patrón returns); _error_ckg se preserva por compatibilidad.

Usage:
    walker <project>/extract_knowledge
    walker <project>/extract_knowledge '{"scope":"functions"}'
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any

from shared.core.engine.domain.execution import ExecutionContext, TaskResult
from shared.tasks.domain.validated_vo import TaskInput

from shared.tasks.infrastructure.kg_context import (
    resolve_cpg_ckg_services,
)

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


def extract_knowledge(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
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

    cpg, ckg, project_root = resolve_cpg_ckg_services(
        chain_dir=chain_dir, project_id=project_id
    )
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

    # Análisis cross-feature CPG×CKG + stats + questionnaire → Application Service.
    payload = ckg.build_extract_payload(cpg, scope, chain_dir)

    return TaskResult.success(
        data={
            "task": "extract_knowledge",
            "description": "Code analysis via CPG to extract knowledge",
            **payload,
            "action_required": (
                "Review the CPG analysis and answer each question in the questionnaire. "
                "Save each answer using documenting_knowledge: "
                'walker <project>/documenting_knowledge \'{"type":"<type>","statement":"<answer>","scope":"<scope>"}\''
            ),
        }
    )


def register(registry: Any) -> None:
    registry.register(
        "extract_knowledge",
        extract_knowledge,
        metadata={
            "description": "Extract knowledge from code via CPG to populate CKG (no sprint needed)",
            "usage": "walker <project>/extract_knowledge",
            "example": "walker <project>/extract_knowledge",
            "query_instructions": (
                "Returns: (1) full CPG analysis, (2) current CKG, "
                "(3) questionnaire for the LLM to answer and save to CKG."
            ),
            "tags": ["knowledge", "cognitive-graph", "extract"],
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
