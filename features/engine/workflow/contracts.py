"""engine/workflow/contracts.py — DTOs de respuesta de los 16 sub_states `llm_task`.

ADR-WALKER-043 §4.1: estos son DTOs de contrato (respuesta que el agente
devuelve vía submit), NO ViewModels.

PRINCIPIO SSOT (§4.2): el `output_schema` vive en el `*.node.json`
(workflow declarativo), y cada DTO de respuesta replica ese contrato con
`from_dict()` que VALIDA estructura: campos `required`, tipos básicos y
`enum`. Así los outputs de `llm_task` pasan por un DTO tipado antes de que
el engine avance (RF-B2 del SPEC-WALKER-043):

  - `from_dict()` → valida y construye desde el dict que responde el agente
                    (o que produce el action de la task).
  - `as_dict()`   → serializa justo antes de la frontera (submit/transition).
  - `to_schema()` → devuelve el JSON Schema de salida (compatible con el
                    `output_schema` declarado en el node.json).

Cobertura (16 sub_states `llm_task` — verificado en
features/engine/workflow/nodes/*.node.json):

  - start.SELECT
  - project_init.GATHER_SPECS
  - analyze.ANALYZE
  - analyze.KNOWLEDGE_CAPTURE
  - sprint_planning.GOAL
  - sprint_planning.ADD_TASKS
  - sprint_planning.REVIEW
  - design.DESIGN_ANALYZE
  - implement.WORK
  - implement.CODE_REVIEW_SCAN
  - implement.KNOWLEDGE_UPDATE
  - refine.DIAGNOSE
  - refine.KNOWLEDGE_CAPTURE
  - sprint.PLANNING
  - sprint.TASK_SPEC
  - sprint.RETRO

Los 4 sub_states de knowledge capture (ANALYZE.KNOWLEDGE_CAPTURE,
IMPLEMENT.KNOWLEDGE_UPDATE, REFINE.KNOWLEDGE_CAPTURE, SPRINT.RETRO)
comparten el envelope canónico `schema/knowledge-capture.schema.json` (GAP-6):
cada uno añade su propio campo de estado (status/captured) + contador.
"""

from __future__ import annotations


class ContractOutputError(ValueError):
    """Respuesta que NO cumple el output_schema declarado en el node.json."""


# ── Base común ──────────────────────────────────────────────────────────────


class WorkflowOutput:
    """Base DTO de respuesta de un sub_state `llm_task`.

    Subclases implementan:
      - `__init__(**data)`           → lo coerce y valida (lanza ValueError).
      - `describe()` (opcional)      → descripción para el prompt de respuesta.
    """

    NODE = ""
    SUB_STATE = ""
    # Campos que DEVUELVE el agente/engine. Ej. {"status", "summary"}
    FIELDS: tuple[str, ...] = ()

    @classmethod
    def requires(cls) -> tuple[str, ...]:
        """Campos obligatorios (output_schema.required del node.json)."""
        return tuple(f for f in cls.FIELDS if getattr(cls, "_required", False))

    @classmethod
    def _check_field_types(cls, data: dict) -> list[str]:
        """Valida tipos básicos/enum declarados por la subclase vía _TYPES."""
        errors: list[str] = []
        for name, spec in getattr(cls, "_TYPES", {}).items():
            val = data.get(name)
            if val is None:
                if name in (cls.requires()):
                    errors.append(f"{name} is required")
                continue
            expected = spec.get("type")
            if expected == "string" and not isinstance(val, str):
                errors.append(f"{name} must be string, got {type(val).__name__}")
            elif expected == "integer" and (
                not isinstance(val, int) or isinstance(val, bool)
            ):
                errors.append(f"{name} must be integer, got {type(val).__name__}")
            elif expected == "boolean" and not isinstance(val, bool):
                errors.append(f"{name} must be boolean, got {type(val).__name__}")
            elif expected == "array" and not isinstance(val, list):
                errors.append(f"{name} must be array, got {type(val).__name__}")
            elif expected == "object" and not isinstance(val, dict):
                errors.append(f"{name} must be object, got {type(val).__name__}")
            enum = spec.get("enum")
            if enum and val not in enum:
                errors.append(f"{name}={val!r} not in enum {enum}")
        return errors

    @classmethod
    def from_dict(cls, d: dict) -> "WorkflowOutput":
        """Construye y valida el DTO de respuesta.

        Raises:
            ContractOutputError: si el dict no cumple `required` / tipos.
        """
        data = dict(d or {})
        missing = [f for f in cls.requires() if data.get(f) in (None, "")]
        type_errors = cls._check_field_types(data)
        if missing:
            raise ContractOutputError(
                f"[{cls.NODE}.{cls.SUB_STATE}] output_schema required missing: "
                f"{', '.join(missing)}"
            )
        if type_errors:
            raise ContractOutputError(
                f"[{cls.NODE}.{cls.SUB_STATE}] output_schema violation: "
                f"{'; '.join(type_errors)}"
            )
        return cls.from_validated(data)

    @classmethod
    def from_validated(cls, data: dict) -> "WorkflowOutput":
        raise NotImplementedError

    def as_dict(self) -> dict:
        raise NotImplementedError

    @classmethod
    def to_schema(cls) -> dict:
        """JSON Schema equivalente a `output_schema` del node.json."""
        settings = getattr(cls, "_TYPES", {})
        props = {
            name: {k: v for k, v in spec.items() if k not in ("required",)}
            for name, spec in settings.items()
        }
        return {
            "type": "object",
            "properties": props,
            "required": list(cls.requires()),
        }


# ── START ───────────────────────────────────────────────────────────────────


class SelectOutput(WorkflowOutput):
    """start.SELECT — decisión de trabajar en un proyecto existente o crear."""

    NODE = "START"
    SUB_STATE = "SELECT"
    _TYPES = {
        "decision": {"type": "string", "enum": ["select", "create"]},
        "project_id": {"type": "string"},
    }
    FIELDS = ("decision", "project_id")
    decision_required = True  # output_schema.required = ["decision"]

    @classmethod
    def requires(cls) -> tuple[str, ...]:
        return ("decision",)

    @classmethod
    def from_validated(cls, data: dict) -> "SelectOutput":
        return cls(
            decision=data.get("decision", ""), project_id=data.get("project_id", "")
        )

    def __init__(self, decision: str, project_id: str = ""):
        self.decision = decision
        self.project_id = project_id
        if decision not in ("select", "create"):
            raise ContractOutputError(f"[START.SELECT] decision inválida: {decision!r}")

    def as_dict(self) -> dict:
        return {"decision": self.decision, "project_id": self.project_id}


# ── PROJECT_INIT ────────────────────────────────────────────────────────────


class GatherSpecsOutput(WorkflowOutput):
    """start_init.GATHER_SPECS — specs recopiladas del proyecto."""

    NODE = "PROJECT_INIT"
    SUB_STATE = "GATHER_SPECS"
    FIELDS = ("specs",)
    _TYPES = {"specs": {"type": "object"}}

    @classmethod
    def from_validated(cls, data: dict) -> "GatherSpecsOutput":
        return cls(specs=data.get("specs", {}))

    def __init__(self, specs: dict | None = None):
        if specs is not None and not isinstance(specs, dict):
            raise ContractOutputError(
                "[PROJECT_INIT.GATHER_SPECS] specs must be an object"
            )
        self.specs = specs or {}

    def as_dict(self) -> dict:
        return {"specs": self.specs}


# ── ANALYZE ─────────────────────────────────────────────────────────────────


class AnalyzeOutput(WorkflowOutput):
    """analyze.ANALYZE — resumen del análisis 6→3 completado."""

    NODE = "ANALYZE"
    SUB_STATE = "ANALYZE"
    FIELDS = ("status", "summary")
    _TYPES = {
        "status": {"type": "string", "enum": ["analyzed"]},
        "summary": {"type": "string"},
    }

    @classmethod
    def requires(cls) -> tuple[str, ...]:
        return ()

    @classmethod
    def from_validated(cls, data: dict) -> "AnalyzeOutput":
        return cls(
            status=data.get("status", "analyzed"), summary=data.get("summary", "")
        )

    def __init__(self, status: str = "analyzed", summary: str = ""):
        self.status = status
        self.summary = summary
        if status != "analyzed":
            raise ContractOutputError(
                f"[ANALYZE.ANALYZE] status must be 'analyzed', got {status!r}"
            )

    def as_dict(self) -> dict:
        return {"status": self.status, "summary": self.summary}


class AnalyzeKnowledgeCaptureOutput(WorkflowOutput):
    """analyze.KNOWLEDGE_CAPTURE — conocimiento capturado (canónico GAP-6)."""

    NODE = "ANALYZE"
    SUB_STATE = "KNOWLEDGE_CAPTURE"
    FIELDS = ("status", "total_items")
    _TYPES = {
        "status": {"type": "string", "enum": ["captured"]},
        "total_items": {"type": "integer"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "AnalyzeKnowledgeCaptureOutput":
        return cls(
            status=data.get("status", "captured"),
            total_items=data.get("total_items") or 0,
        )

    def __init__(self, status: str = "captured", total_items: int = 0):
        self.status = status
        self.total_items = total_items or 0
        if status != "captured":
            raise ContractOutputError(
                f"[ANALYZE.KNOWLEDGE_CAPTURE] status must be 'captured', got {status!r}"
            )

    def as_dict(self) -> dict:
        return {"status": self.status, "total_items": self.total_items}

    @classmethod
    def to_schema(cls) -> dict:
        schema = super().to_schema()
        schema["$ref"] = "../schema/knowledge-capture.schema.json"
        return schema


# ── SPRINT_PLANNING ─────────────────────────────────────────────────────────


class SprintGoalOutput(WorkflowOutput):
    """sprint_planning.GOAL — goal del sprint definido."""

    NODE = "SPRINT_PLANNING"
    SUB_STATE = "GOAL"
    FIELDS = ("status", "goal")
    _TYPES = {
        "status": {"type": "string", "enum": ["goal_defined"]},
        "goal": {"type": "string"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "SprintGoalOutput":
        return cls(status=data.get("status", "goal_defined"), goal=data.get("goal", ""))

    def __init__(self, status: str = "goal_defined", goal: str = ""):
        self.status = status
        self.goal = goal
        if status != "goal_defined":
            raise ContractOutputError(
                f"[SPRINT_PLANNING.GOAL] status must be 'goal_defined', got {status!r}"
            )

    def as_dict(self) -> dict:
        return {"status": self.status, "goal": self.goal}


class AddTaskOutput(WorkflowOutput):
    """sprint_planning.ADD_TASKS — tarea agregada o plan completado."""

    NODE = "SPRINT_PLANNING"
    SUB_STATE = "ADD_TASKS"
    FIELDS = ("status", "task_id", "task_count")
    _TYPES = {
        "status": {"type": "string", "enum": ["added", "completed"]},
        "task_id": {"type": "string"},
        "task_count": {"type": "integer"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "AddTaskOutput":
        return cls(
            status=data.get("status", "added"),
            task_count=data.get("task_count") or 0,
            task_id=data.get("task_id", ""),
        )

    def __init__(self, status: str = "added", task_count: int = 0, task_id: str = ""):
        self.status = status
        self.task_count = task_count or 0
        self.task_id = task_id
        if status not in ("added", "completed"):
            raise ContractOutputError(
                f"[SPRINT_PLANNING.ADD_TASKS] status must be 'added'|'completed', got {status!r}"
            )
        if status == "added" and not task_id:
            raise ContractOutputError(
                "[SPRINT_PLANNING.ADD_TASKS] task_id is required when status='added'"
            )

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "task_id": self.task_id,
            "task_count": self.task_count,
        }


class PlanReviewOutput(WorkflowOutput):
    """sprint_planning.REVIEW — plan aprobado o revisado."""

    NODE = "SPRINT_PLANNING"
    SUB_STATE = "REVIEW"
    FIELDS = ("status", "notes")
    _TYPES = {
        "status": {"type": "string", "enum": ["approved", "revised"]},
        "notes": {"type": "string"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "PlanReviewOutput":
        return cls(status=data.get("status", "approved"), notes=data.get("notes", ""))

    def __init__(self, status: str = "approved", notes: str = ""):
        self.status = status
        self.notes = notes
        if status not in ("approved", "revised"):
            raise ContractOutputError(
                f"[SPRINT_PLANNING.REVIEW] status must be 'approved'|'revised', got {status!r}"
            )

    def as_dict(self) -> dict:
        return {"status": self.status, "notes": self.notes}


# ── DESIGN ──────────────────────────────────────────────────────────────────


class DesignAnalyzeOutput(WorkflowOutput):
    """design.DESIGN_ANALYZE — análisis de diseño 6→3 completado."""

    NODE = "DESIGN"
    SUB_STATE = "DESIGN_ANALYZE"
    FIELDS = ("status", "summary")
    _TYPES = {
        "status": {"type": "string", "enum": ["analyzed"]},
        "summary": {"type": "string"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "DesignAnalyzeOutput":
        return cls(
            status=data.get("status", "analyzed"), summary=data.get("summary", "")
        )

    def __init__(self, status: str = "analyzed", summary: str = ""):
        self.status = status
        self.summary = summary
        if status != "analyzed":
            raise ContractOutputError(
                f"[DESIGN.DESIGN_ANALYZE] status must be 'analyzed', got {status!r}"
            )

    def as_dict(self) -> dict:
        return {"status": self.status, "summary": self.summary}


# ── IMPLEMENT ───────────────────────────────────────────────────────────────


class ImplementWorkOutput(WorkflowOutput):
    """implement.WORK — resultado de la implementación."""

    NODE = "IMPLEMENT"
    SUB_STATE = "WORK"
    FIELDS = ("result",)
    _TYPES = {"result": {"type": "object"}}

    @classmethod
    def from_validated(cls, data: dict) -> "ImplementWorkOutput":
        return cls(result=data.get("result", {}))

    def __init__(self, result: dict | None = None):
        if result is not None and not isinstance(result, dict):
            raise ContractOutputError("[IMPLEMENT.WORK] result must be an object")
        self.result = result or {}

    def as_dict(self) -> dict:
        return {"result": self.result}


class CodeReviewOutput(WorkflowOutput):
    """implement.CODE_REVIEW_SCAN — resultado del análisis de calidad."""

    NODE = "IMPLEMENT"
    SUB_STATE = "CODE_REVIEW_SCAN"
    FIELDS = ("passed", "findings", "summary")
    _TYPES = {
        "passed": {"type": "boolean"},
        "findings": {"type": "array"},
        "summary": {"type": "string"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "CodeReviewOutput":
        return cls(
            passed=data.get("passed", False),
            summary=data.get("summary", ""),
            findings=data.get("findings", []),
        )

    def __init__(self, passed: bool, summary: str = "", findings: list | None = None):
        self.passed = passed
        self.summary = summary
        self.findings = list(findings or [])
        if not isinstance(passed, bool):
            raise ContractOutputError(
                "[IMPLEMENT.CODE_REVIEW_SCAN] passed must be boolean"
            )

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "findings": self.findings,
            "summary": self.summary,
        }


class KnowledgeUpdateOutput(WorkflowOutput):
    """implement.KNOWLEDGE_UPDATE — conocimiento actualizado (GAP-6)."""

    NODE = "IMPLEMENT"
    SUB_STATE = "KNOWLEDGE_UPDATE"
    FIELDS = ("status", "total")
    _TYPES = {
        "status": {"type": "string", "enum": ["updated"]},
        "total": {"type": "integer"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "KnowledgeUpdateOutput":
        return cls(status=data.get("status", "updated"), total=data.get("total") or 0)

    def __init__(self, status: str = "updated", total: int = 0):
        self.status = status
        self.total = total or 0
        if status != "updated":
            raise ContractOutputError(
                f"[IMPLEMENT.KNOWLEDGE_UPDATE] status must be 'updated', got {status!r}"
            )

    def as_dict(self) -> dict:
        return {"status": self.status, "total": self.total}

    @classmethod
    def to_schema(cls) -> dict:
        schema = super().to_schema()
        schema["$ref"] = "../schema/knowledge-capture.schema.json"
        return schema


# ── REFINE ──────────────────────────────────────────────────────────────────


class DiagnoseOutput(WorkflowOutput):
    """refine.DIAGNOSE — causa raíz y objetivo de refinamiento."""

    NODE = "REFINE"
    SUB_STATE = "DIAGNOSE"
    FIELDS = ("target", "reason", "knowledge_items")
    _TYPES = {
        "target": {"type": "string"},
        "reason": {"type": "string"},
        "knowledge_items": {"type": "array"},
    }

    @classmethod
    def requires(cls) -> tuple[str, ...]:
        return ("target",)

    @classmethod
    def from_validated(cls, data: dict) -> "DiagnoseOutput":
        return cls(
            target=data.get("target", ""),
            reason=data.get("reason", ""),
            knowledge_items=list(data.get("knowledge_items", []) or []),
        )

    def __init__(
        self, target: str, reason: str = "", knowledge_items: list | None = None
    ):
        self.target = target
        self.reason = reason
        self.knowledge_items = list(knowledge_items or [])
        if not target:
            raise ContractOutputError("[REFINE.DIAGNOSE] target is required")

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "reason": self.reason,
            "knowledge_items": self.knowledge_items,
        }


class RefineKnowledgeCaptureOutput(WorkflowOutput):
    """refine.KNOWLEDGE_CAPTURE — lecciones aprendidas capturadas (GAP-6)."""

    NODE = "REFINE"
    SUB_STATE = "KNOWLEDGE_CAPTURE"
    FIELDS = ("captured", "items")
    _TYPES = {
        "captured": {"type": "boolean"},
        "items": {"type": "integer"},
    }

    @classmethod
    def requires(cls) -> tuple[str, ...]:
        return ("captured",)

    @classmethod
    def from_validated(cls, data: dict) -> "RefineKnowledgeCaptureOutput":
        return cls(captured=data.get("captured", True), items=data.get("items") or 0)

    def __init__(self, captured: bool = True, items: int = 0):
        self.captured = captured
        self.items = items or 0
        if not isinstance(captured, bool):
            raise ContractOutputError(
                "[REFINE.KNOWLEDGE_CAPTURE] captured must be boolean"
            )

    def as_dict(self) -> dict:
        return {"captured": self.captured, "items": self.items}

    @classmethod
    def to_schema(cls) -> dict:
        schema = super().to_schema()
        schema["$ref"] = "../schema/knowledge-capture.schema.json"
        return schema


# ── SPRINT ──────────────────────────────────────────────────────────────────


class SprintPlanningOutput(WorkflowOutput):
    """sprint.PLANNING — plan del sprint creado (goal + task titles)."""

    NODE = "SPRINT"
    SUB_STATE = "PLANNING"
    FIELDS = ("status", "sprint_id", "goal", "task_count")
    _TYPES = {
        "status": {"type": "string", "enum": ["planned"]},
        "sprint_id": {"type": "string"},
        "goal": {"type": "string"},
        "task_count": {"type": "integer"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "SprintPlanningOutput":
        return cls(
            status=data.get("status", "planned"),
            sprint_id=data.get("sprint_id", ""),
            goal=data.get("goal", ""),
            task_count=data.get("task_count") or 0,
        )

    def __init__(
        self,
        status: str = "planned",
        sprint_id: str = "",
        goal: str = "",
        task_count: int = 0,
    ):
        self.status = status
        self.sprint_id = sprint_id
        self.goal = goal
        self.task_count = task_count or 0
        if status != "planned":
            raise ContractOutputError(
                f"[SPRINT.PLANNING] status must be 'planned', got {status!r}"
            )

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "sprint_id": self.sprint_id,
            "goal": self.goal,
            "task_count": self.task_count,
        }


class TaskSpecOutput(WorkflowOutput):
    """sprint.TASK_SPEC — spec de la tarea actual especificada."""

    NODE = "SPRINT"
    SUB_STATE = "TASK_SPEC"
    FIELDS = ("task_id", "status", "approval_type", "decision_data")
    _TYPES = {
        "task_id": {"type": "string"},
        "status": {"type": "string"},
        "approval_type": {"type": "string", "enum": ["standard", "decision"]},
        "decision_data": {"type": "object"},
    }

    @classmethod
    def requires(cls) -> tuple[str, ...]:
        return ("task_id", "status")

    @classmethod
    def from_validated(cls, data: dict) -> "TaskSpecOutput":
        return cls(
            task_id=data.get("task_id", ""),
            status=data.get("status", ""),
            approval_type=data.get("approval_type", "standard"),
            decision_data=data.get("decision_data", {}),
        )

    def __init__(self, task_id: str, status: str, approval_type: str = "standard", decision_data: dict | None = None):
        self.task_id = task_id
        self.status = status
        self.approval_type = approval_type
        self.decision_data = decision_data or {}
        if not task_id or not status:
            raise ContractOutputError(
                "[SPRINT.TASK_SPEC] task_id and status are required (output_schema)"
            )

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "approval_type": self.approval_type,
            "decision_data": self.decision_data,
        }


class SprintRetroOutput(WorkflowOutput):
    """sprint.RETRO — retrospectiva del sprint (canónico GAP-6)."""

    NODE = "SPRINT"
    SUB_STATE = "RETRO"
    FIELDS = ("status", "items_count")
    _TYPES = {
        "status": {"type": "string", "enum": ["completed"]},
        "items_count": {"type": "integer"},
    }

    @classmethod
    def from_validated(cls, data: dict) -> "SprintRetroOutput":
        return cls(
            status=data.get("status", "completed"),
            items_count=data.get("items_count") or 0,
        )

    def __init__(self, status: str = "completed", items_count: int = 0):
        self.status = status
        self.items_count = items_count or 0
        if status != "completed":
            raise ContractOutputError(
                f"[SPRINT.RETRO] status must be 'completed', got {status!r}"
            )

    def as_dict(self) -> dict:
        return {"status": self.status, "items_count": self.items_count}

    @classmethod
    def to_schema(cls) -> dict:
        schema = super().to_schema()
        schema["$ref"] = "../schema/knowledge-capture.schema.json"
        return schema


# ── Registro sub_state → DTO ────────────────────────────────────────────────


WORKFLOW_OUTPUTS: dict[tuple[str, str], type[WorkflowOutput]] = {
    ("START", "SELECT"): SelectOutput,
    ("PROJECT_INIT", "GATHER_SPECS"): GatherSpecsOutput,
    ("ANALYZE", "ANALYZE"): AnalyzeOutput,
    ("ANALYZE", "KNOWLEDGE_CAPTURE"): AnalyzeKnowledgeCaptureOutput,
    ("SPRINT_PLANNING", "GOAL"): SprintGoalOutput,
    ("SPRINT_PLANNING", "ADD_TASKS"): AddTaskOutput,
    ("SPRINT_PLANNING", "REVIEW"): PlanReviewOutput,
    ("DESIGN", "DESIGN_ANALYZE"): DesignAnalyzeOutput,
    ("IMPLEMENT", "WORK"): ImplementWorkOutput,
    ("IMPLEMENT", "CODE_REVIEW_SCAN"): CodeReviewOutput,
    ("IMPLEMENT", "KNOWLEDGE_UPDATE"): KnowledgeUpdateOutput,
    ("REFINE", "DIAGNOSE"): DiagnoseOutput,
    ("REFINE", "KNOWLEDGE_CAPTURE"): RefineKnowledgeCaptureOutput,
    ("SPRINT", "PLANNING"): SprintPlanningOutput,
    ("SPRINT", "TASK_SPEC"): TaskSpecOutput,
    ("SPRINT", "RETRO"): SprintRetroOutput,
}


def get_workflow_output_dto(
    node_id: str, sub_state_key: str
) -> type[WorkflowOutput] | None:
    """Resuelve el DTO de salida de un sub_state `llm_task` (None si no hay)."""
    return WORKFLOW_OUTPUTS.get((node_id.upper(), sub_state_key.upper()))


def validate_llm_task_output(node_id: str, sub_state_key: str, data: dict) -> dict:
    """Valida la respuesta de un sub_state `llm_task` contra su DTO.

    Usado por el submitter antes de avanzar (F3 SPEC-043). Si no hay DTO
    declarado, NO bloquea (fail-open); si hay DTO y la respuesta no cumple,
    lanza ContractOutputError (→ ValidationError → 422/block+message).
    """
    dto_cls = get_workflow_output_dto(node_id, sub_state_key)
    if dto_cls is None:
        return data if isinstance(data, dict) else {"result": data}
    dto = dto_cls.from_dict(data)
    return dto.as_dict()
