"""
domain/errors.py — Domain errors for the Project aggregate.

Pure domain errors — no IO, no imports from other features.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base error for Project domain."""
    pass


class InvalidProjectIdError(DomainError):
    """ProjectId fails format validation."""

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(f"Invalid project_id: '{value}'. Must match [a-zA-Z0-9_-]+")


class ProjectNotFoundError(DomainError):
    """Project does not exist in the repository."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project not found: '{project_id}'")


class ProjectAlreadyExistsError(DomainError):
    """Project already registered."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(f"Project already exists: '{project_id}'")


class InvalidManifestError(DomainError):
    """Project manifest (project.json) fails validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Invalid manifest: {'; '.join(errors)}")


class PathResolutionError(DomainError):
    """Cannot resolve project paths from a chain_dir."""

    def __init__(self, chain_dir: str, reason: str = "") -> None:
        self.chain_dir = chain_dir
        super().__init__(
            f"Cannot resolve paths from '{chain_dir}'"
            + (f": {reason}" if reason else "")
        )
