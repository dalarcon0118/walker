"""app/orchestration/contracts.py — DTOs de contrato de las consultas por fase.

TERMINOLOGÍA (ADR-WALKER-043 §4.1): estos son DTOs de contrato, NO ViewModels.
Walker es backend — su cliente es un agente IA que consume JSON, no una vista.
ViewModel pertenece al patrón MVC/MVVM (capa de presentación); aquí usamos
DTOs de contrato que cruzan la frontera aplicación → transporte (TaskResult → JSON).

Cada DTO de fase (ADR-WALKER-043 §4.2):
  - `from_dict()` → valida y construye desde el dict del cuerpo/schema.
  - `as_dict()`   → serializa a JSON justo antes de la frontera.
  - `to_schema()` → genera el JSON Schema que el agente ve como output_schema
                    (SSOT: el contrato deriva del DTO, no se escribe a mano).

Las secciones internas del motor son Value Objects (CpgSectionVO, ...) con
estado ok/error; los DTOs de fase son la síntesis de negocio por fase
(ADR-WALKER-023 §3): IMPLEMENT, REFINE, VERIFY, SPRINT.PLANNING.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class SymbolDTO:
    """Símbolo del CPG (función/clase/método) de un archivo."""

    type: str
    name: str
    file: str
    line: int | None = None
    signature: str = ""
    calls: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "SymbolDTO":
        return cls(
            type=str(d.get("type", "")),
            name=str(d.get("name", "")),
            file=str(d.get("file", "")),
            line=d.get("line"),
            signature=str(d.get("signature", "")),
            calls=list(d.get("calls", []) or []),
        )

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["type", "name", "file"],
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string"},
                "name": {"type": "string"},
                "file": {"type": "string"},
                "line": {"type": ["integer", "null"]},
                "signature": {"type": "string"},
                "calls": {"type": "array", "items": {"type": "string"}},
            },
        }


@dataclass(frozen=True)
class KnowledgeDTO:
    """Ítem de conocimiento CKG (convención/decision/pattern)."""

    id: str
    type: str
    statement: str
    scope: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeDTO":
        return cls(
            id=str(d.get("id", "")),
            type=str(d.get("type", "")),
            statement=str(d.get("statement", "")),
            scope=str(d.get("scope", "")),
            tags=list(d.get("tags", []) or []),
        )

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["id", "type", "statement"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "type": {"type": "string"},
                "statement": {"type": "string"},
                "scope": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        }


@dataclass(frozen=True)
class SpecDTO:
    """Spec (tarea delta) con enriquecimiento de GetTaskContextUseCase."""

    id: str
    objective: str
    status: str
    files: list[str] = field(default_factory=list)
    dependencies: list[dict[str, str]] = field(default_factory=list)
    spring_goal: str = ""
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SpecDTO":
        return cls(
            id=str(d.get("id", "")),
            objective=str(d.get("objective", "")),
            status=str(d.get("status", "")),
            files=list(d.get("files", []) or []),
            dependencies=list(d.get("dependencies", []) or []),
            spring_goal=str(d.get("spring_goal", "")),
            description=str(d.get("description", "")),
        )

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["id", "objective", "status"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "objective": {"type": "string"},
                "status": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "dependencies": {"type": "array", "items": {"type": "object"}},
                "spring_goal": {"type": "string"},
                "description": {"type": "string"},
            },
        }


@dataclass(frozen=True)
class TraceEventDTO:
    """Evento de actividad (trace)."""

    event_type: str
    tool: str
    task_id: str = ""
    timestamp: str = ""
    duration_ms: int | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "TraceEventDTO":
        return cls(
            event_type=str(d.get("event_type", "")),
            tool=str(d.get("tool", "")),
            task_id=str(d.get("task_id", "")),
            timestamp=str(d.get("timestamp", "")),
            duration_ms=d.get("duration_ms"),
        )

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["event_type", "tool"],
            "additionalProperties": False,
            "properties": {
                "event_type": {"type": "string"},
                "tool": {"type": "string"},
                "task_id": {"type": "string"},
                "timestamp": {"type": "string"},
                "duration_ms": {"type": ["integer", "null"]},
            },
        }


# ── Secciones del motor (get_task_intelligence) ─────────────────────────────
# Cada sección es un Value Object inmutable con su estado (ok/error). El core
# @safe construye la sección con ok=True; _run_section degrada a ok=False ante
# Failure (convention-001), nunca excepción.


@dataclass(frozen=True)
class CpgSectionVO:
    """Sección CPG del motor: estructura/archivos/símbolos."""

    total: int = 0
    files: list[str] = field(default_factory=list)
    symbols: list[SymbolDTO] = field(default_factory=list)
    ok: bool = True
    error: str = ""


@dataclass(frozen=True)
class CkgSectionVO:
    """Sección CKG del motor: convenciones/decisiones relevantes."""

    total: int = 0
    items: list[KnowledgeDTO] = field(default_factory=list)
    ok: bool = True
    error: str = ""


@dataclass(frozen=True)
class DeltaSectionVO:
    """Sección Delta del motor: specs del spring que tocan file/task."""

    total: int = 0
    items: list[SpecDTO] = field(default_factory=list)
    ok: bool = True
    error: str = ""


@dataclass(frozen=True)
class TraceSectionVO:
    """Sección Trace del motor: actividad reciente por tool/task/file."""

    total: int = 0
    tools_top: list[tuple[str, int]] = field(default_factory=list)
    events: list[TraceEventDTO] = field(default_factory=list)
    ok: bool = True
    error: str = ""


@dataclass(frozen=True)
class ScopeVO:
    """Alcance de la consulta (eco de la entrada)."""

    file: str | None = None
    task_id: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class SummaryVO:
    """Totales por capa para el resumen rápido."""

    cpg_total: int = 0
    ckg_total: int = 0
    delta_total: int = 0
    trace_total: int = 0


@dataclass(frozen=True)
class IntelligenceResultVO:
    """Resultado tipado del motor (get_task_intelligence) con las 4 secciones."""

    scope: ScopeVO = field(default_factory=ScopeVO)
    summary: SummaryVO = field(default_factory=SummaryVO)
    cpg: CpgSectionVO = field(default_factory=CpgSectionVO)
    ckg: CkgSectionVO = field(default_factory=CkgSectionVO)
    delta: DeltaSectionVO = field(default_factory=DeltaSectionVO)
    trace: TraceSectionVO = field(default_factory=TraceSectionVO)
    insights: list[Any] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "type": "task_intelligence",
            "scope": asdict(self.scope),
            "summary": asdict(self.summary),
            "cpg": {
                "ok": self.cpg.ok,
                "error": self.cpg.error or None,
                "total": self.cpg.total,
                "files": list(self.cpg.files),
                "symbols": [asdict(s) for s in self.cpg.symbols],
            },
            "ckg": {
                "ok": self.ckg.ok,
                "error": self.ckg.error or None,
                "total": self.ckg.total,
                "items": [asdict(i) for i in self.ckg.items],
            },
            "delta": {
                "ok": self.delta.ok,
                "error": self.delta.error or None,
                "total": self.delta.total,
                "items": [asdict(i) for i in self.delta.items],
            },
            "trace": {
                "ok": self.trace.ok,
                "error": self.trace.error or None,
                "total": self.trace.total,
                "tools_top": list(self.trace.tools_top),
                "events": [asdict(e) for e in self.trace.events],
            },
            "insights": list(self.insights),
        }


# ── DTOs de fase (ADR-WALKER-023 §3) — contrato de las tools públicas ──────
#
# Cada DTO es el contrato de la tool de fase (ADR-WALKER-043 §1):
#   output_schema se deriva de to_schema() (SSOT), no se escribe a mano;
#   from_dict() valida el body antes de pasar al motor.


@dataclass(frozen=True)
class TaskFileIntelligenceDTO:
    """Fase IMPLEMENT — contexto completo de un archivo antes de editarlo."""

    PHASE = "IMPLEMENT"
    QUESTION = "Contexto completo de un archivo antes de editar"

    file: str
    symbols: list[SymbolDTO] = field(default_factory=list)
    convenciones: list[str] = field(default_factory=list)
    specs: list[str] = field(default_factory=list)
    activity: list[TraceEventDTO] = field(default_factory=list)
    insights: list[Any] = field(default_factory=list)
    risk: str = "LOW"

    def __post_init__(self):
        if self.risk not in ("LOW", "MED", "HIGH"):
            raise ValueError(f"Invalid risk: {self.risk!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "TaskFileIntelligenceDTO":
        return cls(
            file=str(d.get("file", "")),
            symbols=[SymbolDTO.from_dict(s) for s in d.get("symbols", []) or []],
            convenciones=list(d.get("convenciones", []) or []),
            specs=list(d.get("specs", []) or []),
            activity=[TraceEventDTO.from_dict(e) for e in d.get("activity", []) or []],
            insights=list(d.get("insights", []) or []),
            risk=str(d.get("risk", "LOW")),
        )

    def as_dict(self) -> dict:
        return {
            "phase": self.PHASE,
            "question": self.QUESTION,
            "file": self.file,
            "entities": {
                "symbols": [asdict(s) for s in self.symbols],
                "convenciones": list(self.convenciones),
                "specs": list(self.specs),
                "activity": [asdict(e) for e in self.activity],
            },
            "insights": list(self.insights),
            "risk": self.risk,
            "ok": True,
            "error": None,
        }

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["phase", "file", "entities", "risk", "ok"],
            "additionalProperties": False,
            "properties": {
                "phase": {"const": cls.PHASE},
                "question": {"const": cls.QUESTION},
                "file": {"type": "string", "description": "Ruta del archivo consultado"},
                "entities": {
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": SymbolDTO.to_schema(),
                        },
                        "convenciones": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "specs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "activity": {
                            "type": "array",
                            "items": TraceEventDTO.to_schema(),
                        },
                    },
                },
                "insights": {"type": "array"},
                "risk": {"type": "string", "enum": ["LOW", "MED", "HIGH"]},
                "ok": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
            },
        }


@dataclass(frozen=True)
class FailureDiagnosisDTO:
    """Fase REFINE — diagnóstico de fallo de una tarea."""

    PHASE = "REFINE"
    QUESTION = "Diagnóstico de fallo de una tarea"

    task_id: str
    spec: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    convenciones_aplicables: list[str] = field(default_factory=list)
    insights: list[Any] = field(default_factory=list)
    diagnosis: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "FailureDiagnosisDTO":
        return cls(
            task_id=str(d.get("task_id", "")),
            spec=list(d.get("spec", []) or []),
            tools_used=list(d.get("tools_used", []) or []),
            convenciones_aplicables=list(d.get("convenciones_aplicables", []) or []),
            insights=list(d.get("insights", []) or []),
            diagnosis=str(d.get("diagnosis", "")),
        )

    def as_dict(self) -> dict:
        return {
            "phase": self.PHASE,
            "question": self.QUESTION,
            "task_id": self.task_id,
            "entities": {
                "spec": list(self.spec),
                "tools_used": list(self.tools_used),
                "convenciones_aplicables": list(self.convenciones_aplicables),
            },
            "insights": list(self.insights),
            "diagnosis": self.diagnosis,
            "ok": True,
            "error": None,
        }

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["phase", "task_id", "entities", "ok"],
            "additionalProperties": False,
            "properties": {
                "phase": {"const": cls.PHASE},
                "question": {"const": cls.QUESTION},
                "task_id": {"type": "string", "description": "Spec/TASK del sprint"},
                "entities": {
                    "type": "object",
                    "properties": {
                        "spec": {"type": "array", "items": {"type": "string"}},
                        "tools_used": {"type": "array", "items": {"type": "string"}},
                        "convenciones_aplicables": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "insights": {"type": "array"},
                "diagnosis": {"type": "string"},
                "ok": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
            },
        }


@dataclass(frozen=True)
class QaCoverageDTO:
    """Fase VERIFY — cobertura de criterios de verificación de una tarea."""

    PHASE = "VERIFY"
    QUESTION = "Cobertura de criterios de verificación de una tarea"

    task_id: str
    specs: list[SpecDTO] = field(default_factory=list)
    activity: list[TraceEventDTO] = field(default_factory=list)
    tools_top: list[tuple[str, int]] = field(default_factory=list)
    insights: list[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "QaCoverageDTO":
        return cls(
            task_id=str(d.get("task_id", "")),
            specs=[SpecDTO.from_dict(s) for s in d.get("specs", []) or []],
            activity=[TraceEventDTO.from_dict(e) for e in d.get("activity", []) or []],
            tools_top=[(t, int(n)) for t, n in (d.get("tools_top", []) or [])],
        )

    def as_dict(self) -> dict:
        return {
            "phase": self.PHASE,
            "question": self.QUESTION,
            "task_id": self.task_id,
            "entities": {
                "specs": [asdict(s) for s in self.specs],
                "activity": [asdict(e) for e in self.activity],
                "tools_top": list(self.tools_top),
            },
            "insights": list(self.insights),
            "ok": True,
            "error": None,
        }

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["phase", "task_id", "entities", "ok"],
            "additionalProperties": False,
            "properties": {
                "phase": {"const": cls.PHASE},
                "question": {"const": cls.QUESTION},
                "task_id": {"type": "string"},
                "entities": {
                    "type": "object",
                    "properties": {
                        "specs": {"type": "array", "items": SpecDTO.to_schema()},
                        "activity": {
                            "type": "array",
                            "items": TraceEventDTO.to_schema(),
                        },
                        "tools_top": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "prefixItems": [
                                    {"type": "string"},
                                    {"type": "integer"},
                                ],
                            },
                        },
                    },
                },
                "insights": {"type": "array"},
                "ok": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
            },
        }


@dataclass(frozen=True)
class SprintEvidenceDTO:
    """Fase SPRINT.PLANNING — evidencia previa para planear el sprint."""

    PHASE = "SPRINT.PLANNING"
    QUESTION = "Evidencia de conocimiento previo para planear"

    scope: str
    goal: str
    conocimiento_existente: list[KnowledgeDTO] = field(default_factory=list)
    tareas_relacionadas: list[SpecDTO] = field(default_factory=list)
    insights: list[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "SprintEvidenceDTO":
        return cls(
            scope=str(d.get("scope", "")),
            goal=str(d.get("goal", "")),
            conocimiento_existente=[
                KnowledgeDTO.from_dict(k)
                for k in d.get("conocimiento_existente", []) or []
            ],
            tareas_relacionadas=[
                SpecDTO.from_dict(s) for s in d.get("tareas_relacionadas", []) or []
            ],
        )

    def as_dict(self) -> dict:
        return {
            "phase": self.PHASE,
            "question": self.QUESTION,
            "scope": self.scope or None,
            "goal": self.goal or None,
            "entities": {
                "conocimiento_existente": [
                    asdict(k) for k in self.conocimiento_existente
                ],
                "tareas_relacionadas": [asdict(s) for s in self.tareas_relacionadas],
            },
            "insights": list(self.insights),
            "ok": True,
            "error": None,
        }

    @classmethod
    def to_schema(cls) -> dict:
        return {
            "type": "object",
            "required": ["phase", "entities", "ok"],
            "additionalProperties": False,
            "properties": {
                "phase": {"const": cls.PHASE},
                "question": {"const": cls.QUESTION},
                "scope": {"type": ["string", "null"]},
                "goal": {"type": ["string", "null"]},
                "entities": {
                    "type": "object",
                    "properties": {
                        "conocimiento_existente": {
                            "type": "array",
                            "items": KnowledgeDTO.to_schema(),
                        },
                        "tareas_relacionadas": {
                            "type": "array",
                            "items": SpecDTO.to_schema(),
                        },
                    },
                },
                "insights": {"type": "array"},
                "ok": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
            },
        }