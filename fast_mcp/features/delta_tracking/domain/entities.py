"""domain/entities.py — Pure domain entities (no infra dependencies).

Spring and Spec are dataclasses representing the business state
of the DeltaGraph. They don't know about CognitiveGraph or ODM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from fast_mcp.features.delta_tracking.domain.values import (
    TYPE_SPRING,
    TYPE_SPEC,
    TYPE_SESSION,
    is_valid_spring_status,
    is_valid_spec_status,
    spec_doc_id,
    spec_id_from_index,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Spec:
    """Task specification within a sprint."""

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

    # specified fields
    problem_statement: str = ""
    evidence: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    quality_gates: list[str] = field(default_factory=list)
    impacted_artifacts: dict = field(default_factory=dict)
    task_context: dict = field(default_factory=dict)
    risks: dict = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)

    # approved fields (decisions + constitution)
    decision: dict = field(default_factory=dict)
    architecture: dict = field(default_factory=dict)
    implementation: dict = field(default_factory=dict)
    invariants: list[str] = field(default_factory=list)
    project_snapshot: dict = field(default_factory=dict)

    # in_progress fields
    loop_verification: list[str] = field(default_factory=list)
    loop_acceptance: str = "all"

    # in_review fields
    review_metrics: dict = field(default_factory=dict)
    review_conventions: list[str] = field(default_factory=list)
    review_architecture: list[str] = field(default_factory=list)
    review_verdict: str = ""

    # completed fields
    commit_hash: str = ""
    commit_url: str = ""
    completed_at: str = ""

    # ADR linkage
    generated_from: str = ""
    generates_specs: list[str] = field(default_factory=list)
    related_decisions: list[str] = field(default_factory=list)

    # Traceability
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
            "problem_statement": self.problem_statement,
            "evidence": self.evidence or [],
            "acceptance_criteria": self.acceptance_criteria or [],
            "quality_gates": self.quality_gates or [],
            "impacted_artifacts": self.impacted_artifacts or {},
            "task_context": self.task_context or {},
            "risks": self.risks or {},
            "preconditions": self.preconditions or [],
            "decision": self.decision or {},
            "architecture": self.architecture or {},
            "implementation": self.implementation or {},
            "invariants": self.invariants or [],
            "project_snapshot": self.project_snapshot or {},
            "loop_verification": self.loop_verification or [],
            "loop_acceptance": self.loop_acceptance,
            "review_metrics": self.review_metrics or {},
            "review_conventions": self.review_conventions or [],
            "review_architecture": self.review_architecture or [],
            "review_verdict": self.review_verdict,
            "commit_hash": self.commit_hash,
            "commit_url": self.commit_url,
            "completed_at": self.completed_at,
            "generated_from": self.generated_from,
            "generates_specs": self.generates_specs or [],
            "related_decisions": self.related_decisions or [],
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
            problem_statement=data.get("problem_statement", ""),
            evidence=data.get("evidence", []),
            acceptance_criteria=data.get("acceptance_criteria", []),
            quality_gates=data.get("quality_gates", []),
            impacted_artifacts=data.get("impacted_artifacts", {}),
            task_context=data.get("task_context", {}),
            risks=data.get("risks", {}),
            preconditions=data.get("preconditions", []),
            decision=data.get("decision", {}),
            architecture=data.get("architecture", {}),
            implementation=data.get("implementation", {}),
            invariants=data.get("invariants", []),
            project_snapshot=data.get("project_snapshot", {}),
            loop_verification=data.get("loop_verification", []),
            loop_acceptance=data.get("loop_acceptance", "all"),
            review_metrics=data.get("review_metrics", {}),
            review_conventions=data.get("review_conventions", []),
            review_architecture=data.get("review_architecture", []),
            review_verdict=data.get("review_verdict", ""),
            commit_hash=data.get("commit_hash", ""),
            commit_url=data.get("commit_url", ""),
            completed_at=data.get("completed_at", ""),
            generated_from=data.get("generated_from", ""),
            generates_specs=data.get("generates_specs", []),
            related_decisions=data.get("related_decisions", []),
            transformation_map=data.get("transformation_map", {}),
            created_at=data.get("created_at", ""),
        )


@dataclass
class Session:
    """Sesión de agente dentro de un proyecto (delta-sprint registry).

    Es la unidad de trabajo del AGENTE, hermana de Spring (unidad del proyecto).
    Antes la sesión existía solo como string disperso en specs/trazas; acá tiene
    ciclo de vida (active/closed), jerarquía (parent_session_id) y el sprint al
    que está ligada.
    """

    session_id: str
    project_id: str = ""
    agent: str = ""
    model: str = ""
    status: str = "active"
    goal: str = ""
    spring_id: str = ""
    parent_session_id: str = ""
    started_at: str = ""
    updated_at: str = ""
    ended_at: str = ""
    outcome: str = ""
    context: dict | None = None

    @property
    def type(self) -> str:
        return TYPE_SESSION

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "agent": self.agent,
            "model": self.model,
            "status": self.status,
            "goal": self.goal,
            "spring_id": self.spring_id,
            "parent_session_id": self.parent_session_id,
            "started_at": self.started_at or _now(),
            "updated_at": self.updated_at or _now(),
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "context": self.context or {},
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        return cls(
            session_id=data.get("session_id", data.get("_id", "")),
            project_id=data.get("project_id", ""),
            agent=data.get("agent", ""),
            model=data.get("model", ""),
            status=data.get("status", "active"),
            goal=data.get("goal", ""),
            spring_id=data.get("spring_id", ""),
            parent_session_id=data.get("parent_session_id", ""),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
            ended_at=data.get("ended_at", ""),
            outcome=data.get("outcome", ""),
            context=data.get("context"),
        )


@dataclass
class Spring:
    """Business sprint in the DeltaGraph."""

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
