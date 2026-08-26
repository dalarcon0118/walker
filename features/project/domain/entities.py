"""
domain/entities.py — Domain entities for the Project aggregate.

Project is the aggregate root — it enforces all domain invariants
and publishes domain events on state changes.

Uses return pattern: business methods return (result, errors) tuples.
Domain events are collected via `collect_events()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .values import ProjectId, ProjectManifest, ProjectPaths
from .events import ProjectRegistered, ProjectProfileUpdated, SprintDetected


@dataclass
class Project:
    """Aggregate root — un proyecto con identidad, perfil y rutas.

    Invariants:
      - project_id must be a valid ProjectId
      - manifest must be a valid ProjectManifest
      - Two Projects with the same project_id are the same aggregate

    Events are collected via _events list and retrieved via collect_events().
    """

    project_id: ProjectId
    manifest: ProjectManifest = field(default_factory=ProjectManifest)
    paths: ProjectPaths | None = None
    active_sprint_id: str = ""
    _events: list[object] = field(default_factory=list, repr=False, init=False)

    # ── Factory ──

    @staticmethod
    def register(
        project_id: ProjectId,
        manifest: ProjectManifest,
    ) -> tuple["Project | None", list[str]]:
        """Create a new Project aggregate. Returns (project, errors)."""
        errors: list[str] = []
        if not manifest.name:
            errors.append("manifest.name is required")
        if errors:
            return None, errors

        project = Project(project_id=project_id, manifest=manifest)
        project._events.append(
            ProjectRegistered(
                project_id=project_id.value,
                name=manifest.name,
                language=manifest.language,
                framework=manifest.framework,
                repo_path=manifest.repo_path,
            )
        )
        return project, []

    # ── Business methods ──

    def update_profile(self, changes: dict) -> list[str]:
        """Update manifest fields. Returns validation errors.

        Publishes ProjectProfileUpdated on success.
        """
        errors: list[str] = []
        changed: list[str] = []

        for field_name, new_value in changes.items():
            if hasattr(self.manifest, field_name):
                old_value = getattr(self.manifest, field_name)
                if old_value != new_value:
                    setattr(self.manifest, field_name, new_value)
                    changed.append(field_name)

        if errors:
            return errors

        if changed:
            self._events.append(
                ProjectProfileUpdated(
                    project_id=self.project_id.value,
                    changed_fields=changed,
                )
            )
        return []

    def set_active_sprint(self, sprint_id: str) -> tuple[str | None, list[str]]:
        """Set the active sprint for this project. Returns (previous, errors)."""
        if not sprint_id or not str(sprint_id).strip():
            return None, ["sprint_id cannot be empty"]
        previous = self.active_sprint_id
        if previous != sprint_id:
            self.active_sprint_id = sprint_id
            self._events.append(
                SprintDetected(
                    project_id=self.project_id.value,
                    sprint_id=sprint_id,
                )
            )
        return previous, []

    def attach_paths(self, paths: ProjectPaths) -> list[str]:
        """Associate resolved paths with this project."""
        self.paths = paths
        return []

    # ── Event sourcing ──

    def collect_events(self) -> list[object]:
        """Collect and clear domain events.

        Returns a copy of events and clears the internal list.
        Typical usage: after saving the aggregate, publish events.
        """
        events = list(self._events)
        self._events.clear()
        return events

    # ── Projection ──

    def to_dict(self) -> dict:
        """Project the aggregate to a flat dict (for API/display)."""
        d: dict = {
            "project_id": self.project_id.value,
            "name": self.manifest.name,
            "language": self.manifest.language,
            "framework": self.manifest.framework,
            "repo_path": self.manifest.repo_path,
            "description": self.manifest.description,
            "active_sprint_id": self.active_sprint_id,
        }
        if self.paths:
            d["paths"] = {
                "chain_dir": self.paths.chain_dir,
                "project_root": self.paths.project_root,
                "data_dir": self.paths.data_dir,
                "delta_dir": self.paths.delta_dir,
                "cpg_path": self.paths.cpg_path,
                "ckg_path": self.paths.ckg_path,
                "project_json_path": self.paths.project_json_path,
            }
        return d
