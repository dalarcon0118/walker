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
        )

    def merge(self, other: dict) -> Spec:
        """Refina campos desde un dict (legacy o partial update)."""
        data = self.to_dict()
        data.update({k: v for k, v in other.items() if v is not None})
        if "status" in other and other["status"] is not None:
            data["status"] = other["status"]
        return Spec.from_dict(data)


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
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
