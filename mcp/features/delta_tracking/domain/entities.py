"""domain/entities.py — Entidades puras de dominio (sin dependencias de infra).

Spring y Spec son dataclasses inmutables que representan el estado
de negocio del DeltaGraph. No conocen CognitiveGraph ni ODM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from features.delta_tracking.domain.values import (
    TYPE_SPRING, TYPE_SPEC,
    is_valid_spring_status, is_valid_spec_status,
    spec_doc_id, spec_id_from_index,
)


@dataclass
class Spec:
    """Especificacion de tarea dentro de un spring."""

    spring_id: str
    objective: str
    domain: str = ""
    spec_type: str = "feature"
    spec_index: int = 0
    result_state: str = ""
    files: list[str] = field(default_factory=list)
    status: str = "draft"
    description: str = ""
    predicates: dict | None = None
    depends_on: list[str] = field(default_factory=list)
    updated_at: str = ""

    # Campos de estado specified
    problem_statement: str = ""
    evidence: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    quality_gates: list[str] = field(default_factory=list)
    impacted_artifacts: dict = field(default_factory=dict)
    task_context: dict = field(default_factory=dict)
    risks: dict = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)

    # Campos de estado approved (decisiones + constitución de la spec, SPEC-005)
    decision: dict = field(default_factory=dict)
    architecture: dict = field(default_factory=dict)
    implementation: dict = field(default_factory=dict)
    # Constitución (SPEC-005): invariantes del CKG y snapshot top-N del CPG + refs delta
    invariants: list[str] = field(default_factory=list)
    project_snapshot: dict = field(default_factory=dict)

    # Campos de estado in_progress
    loop_verification: list[str] = field(default_factory=list)
    loop_acceptance: str = "all"

    # Campos de estado in_review
    review_metrics: dict = field(default_factory=dict)
    review_conventions: list[str] = field(default_factory=list)
    review_architecture: list[str] = field(default_factory=list)
    review_verdict: str = ""

    # Campos de estado completed
    commit_hash: str = ""
    commit_url: str = ""
    completed_at: str = ""

    # Vinculación con ADR
    generated_from: str = ""
    generates_specs: list[str] = field(default_factory=list)
    related_decisions: list[str] = field(default_factory=list)

    # Trazabilidad
    transformation_map: dict = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self):
        if not is_valid_spec_status(self.status):
            raise ValueError(f"Invalid spec status: {self.status}")

    @property
    def spec_id(self) -> str:
        return spec_id_from_index(self.spec_index)

    @property
    def doc_id(self) -> str:
        return spec_doc_id(self.spring_id, self.spec_id)

    @property
    def type(self) -> str:
        return TYPE_SPEC

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "spring_id": self.spring_id,
            "spec_id": self.spec_id,
            "spec_key": self.doc_id,
            "spec_index": self.spec_index,
            "objective": self.objective,
            "domain": self.domain,
            "spec_type": self.spec_type,
            "status": self.status,
            "result_state": self.result_state,
            "files": self.files or [],
            "description": self.description,
            "predicates": self.predicates or {},
            "depends_on": self.depends_on or [],
            "updated_at": self.updated_at or _now(),
            # Campos specified
            "problem_statement": self.problem_statement,
            "evidence": self.evidence or [],
            "acceptance_criteria": self.acceptance_criteria or [],
            "quality_gates": self.quality_gates or [],
            "impacted_artifacts": self.impacted_artifacts or {},
            "task_context": self.task_context or {},
            "risks": self.risks or {},
            "preconditions": self.preconditions or [],
            # Campos approved
            "decision": self.decision or {},
            "architecture": self.architecture or {},
            "implementation": self.implementation or {},
            "invariants": self.invariants or [],
            "project_snapshot": self.project_snapshot or {},
            # Campos in_progress
            "loop_verification": self.loop_verification or [],
            "loop_acceptance": self.loop_acceptance,
            # Campos in_review
            "review_metrics": self.review_metrics or {},
            "review_conventions": self.review_conventions or [],
            "review_architecture": self.review_architecture or [],
            "review_verdict": self.review_verdict,
            # Campos completed
            "commit_hash": self.commit_hash,
            "commit_url": self.commit_url,
            "completed_at": self.completed_at,
            # Vinculación ADR
            "generated_from": self.generated_from,
            "generates_specs": self.generates_specs or [],
            "related_decisions": self.related_decisions or [],
            # Trazabilidad
            "transformation_map": self.transformation_map or {},
            "created_at": self.created_at or _now(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Spec:
        return cls(
            spring_id=data.get("spring_id", ""),
            objective=data.get("objective", ""),
            domain=data.get("domain", ""),
            spec_type=data.get("spec_type", "feature"),
            spec_index=data.get("spec_index", 0),
            result_state=data.get("result_state", ""),
            files=data.get("files", []),
            status=data.get("status", "draft"),
            description=data.get("description", ""),
            predicates=data.get("predicates"),
            depends_on=data.get("depends_on", []),
            updated_at=data.get("updated_at", ""),
            # Campos specified
            problem_statement=data.get("problem_statement", ""),
            evidence=data.get("evidence", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            quality_gates=data.get("quality_gates", []),
            impacted_artifacts=data.get("impacted_artifacts", {}),
            task_context=data.get("task_context", {}),
            risks=data.get("risks", {}),
            preconditions=data.get("preconditions", []),
            # Campos approved
            decision=data.get("decision", {}),
            architecture=data.get("architecture", {}),
            implementation=data.get("implementation", {}),
            invariants=data.get("invariants", []),
            project_snapshot=data.get("project_snapshot", {}),
            # Campos in_progress
            loop_verification=data.get("loop_verification", []),
            loop_acceptance=data.get("loop_acceptance", "all"),
            # Campos in_review
            review_metrics=data.get("review_metrics", {}),
            review_conventions=data.get("review_conventions", []),
            review_architecture=data.get("review_architecture", []),
            review_verdict=data.get("review_verdict", ""),
            # Campos completed
            commit_hash=data.get("commit_hash", ""),
            commit_url=data.get("commit_url", ""),
            completed_at=data.get("completed_at", ""),
            # Vinculación ADR
            generated_from=data.get("generated_from", ""),
            generates_specs=data.get("generates_specs", []),
            related_decisions=data.get("related_decisions", []),
            # Trazabilidad
            transformation_map=data.get("transformation_map", {}),
            created_at=data.get("created_at", ""),
        )


@dataclass
class Spring:
    """Sprint de negocio en el DeltaGraph."""

    spring_id: str
    goal: str = ""
    status: str = "planning"
    active: bool = False
    project_id: str = ""
    session_id: str = ""
    display_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    context: dict | None = None
    informal: bool = False
    ckg_status: str = "up_to_date"

    def __post_init__(self):
        if not is_valid_spring_status(self.status):
            raise ValueError(f"Invalid spring status: {self.status}")

    @property
    def type(self) -> str:
        return TYPE_SPRING

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "spring_id": self.spring_id,
            "goal": self.goal,
            "status": self.status,
            "active": self.active,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "display_name": self.display_name,
            "created_at": self.created_at or _now(),
            "updated_at": self.updated_at or _now(),
            "context": self.context or {},
            "informal": self.informal,
            "ckg_status": self.ckg_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Spring:
        return cls(
            spring_id=data.get("spring_id", data.get("_id", "")),
            goal=data.get("goal", ""),
            status=data.get("status", "planning"),
            active=data.get("active", False),
            project_id=data.get("project_id", ""),
            session_id=data.get("session_id", ""),
            display_name=data.get("display_name", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            context=data.get("context"),
            informal=data.get("informal", False),
            ckg_status=data.get("ckg_status", "up_to_date"),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
