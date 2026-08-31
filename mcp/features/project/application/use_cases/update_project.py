"""use_cases/update_project.py — UpdateProjectUseCase.

Actualiza project.json con validación de schema.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


class UpdateProjectUseCase:
    """Update project.json with schema validation.

    Validates all fields against PROJECT_SCHEMA before saving.
    """

    @staticmethod
    def execute(chain_dir: str, data: dict) -> dict:
        from ...domain.schema import ProjectSchemaValidator
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        project_json_path = paths.project_json_path
        if not project_json_path or not os.path.exists(project_json_path):
            raise FileNotFoundError(f"project.json not found at {project_json_path}")

        with open(project_json_path) as f:
            current = json.load(f)

        # Merge: update only provided fields
        project_data = current.get("project", current)
        for section, values in data.items():
            if isinstance(values, dict) and section in project_data:
                project_data[section].update(values)
            elif isinstance(values, dict):
                project_data[section] = values

        current["project"] = project_data

        # Validate
        errors = ProjectSchemaValidator.validate(current)
        if errors:
            raise ValueError(f"Project schema validation failed: {'; '.join(errors)}")

        current["project"]["meta"]["updated_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        with open(project_json_path, "w") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)

        return {
            "updated": True,
            "project_id": project_data.get("meta", {}).get("project_id", ""),
            "path": project_json_path,
        }
