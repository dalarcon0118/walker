"""
domain/events.py — Domain events for the Project aggregate.

Immutable, self-contained value objects that represent
something that happened in the domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ProjectRegistered:
    """Emitted when a new project is created/registered."""

    project_id: str
    name: str
    language: str
    framework: str
    repo_path: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True)
class ProjectProfileUpdated:
    """Emitted when a project's manifest (project.json) changes."""

    project_id: str
    changed_fields: list[str]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True)
class SprintDetected:
    """Emitted when the file watcher detects sprint directory activity."""

    project_id: str
    sprint_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
