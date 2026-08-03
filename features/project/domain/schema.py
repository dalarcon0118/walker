"""
domain/schema.py — Project.json schema definition and validator.

SSOT: All valid fields, types, and required fields for project.json.
"""

from __future__ import annotations


PROJECT_SCHEMA = {
    "meta": {
        "required": ["project_id", "version"],
        "fields": {
            "project_id": {
                "type": "string",
                "description": "Unique project identifier",
            },
            "version": {"type": "string", "description": "Schema version"},
            "created_at": {"type": "string", "description": "ISO timestamp"},
            "updated_at": {"type": "string", "description": "ISO timestamp"},
        },
    },
    "identity": {
        "required": ["name"],
        "fields": {
            "name": {"type": "string", "description": "Project display name"},
            "description": {"type": "string", "description": "Project description"},
            "language": {"type": "string", "description": "Primary language"},
            "framework": {"type": "string", "description": "Primary framework"},
            "tags": {"type": "array", "description": "Project tags"},
        },
    },
    "location": {
        "required": ["repo_path"],
        "fields": {
            "repo_path": {
                "type": "string",
                "description": "Absolute path to repo root",
            },
            "cognitive_subdir": {
                "type": "string",
                "description": "Subdir for .cognitive",
            },
            "code_paths": {"type": "array", "description": "Subdirs with source code"},
        },
    },
    "design": {
        "fields": {
            "patterns": {"type": "array"},
            "layer_rules": {"type": "object"},
            "bounded_contexts": {"type": "array"},
            "forbidden_dependencies": {"type": "array"},
            "decisions": {"type": "array"},
        },
    },
    "coding": {
        "fields": {
            "naming_convention": {"type": "string"},
            "formatter": {"type": "string"},
            "linter": {"type": "string"},
            "test_framework": {"type": "string"},
            "max_line_length": {"type": "integer"},
        },
    },
}


class ProjectSchemaValidator:
    """Validates project.json data against the schema.

    PURE: No IO. Receives data, returns errors.
    """

    @staticmethod
    def validate(data: dict) -> list[str]:
        errors: list[str] = []
        project = data.get("project", data)

        for section_name, section_schema in PROJECT_SCHEMA.items():
            section_data = project.get(section_name, {})
            required = section_schema.get("required", [])

            # Check required fields
            for field in required:
                if field not in section_data or section_data.get(field) in (None, ""):
                    errors.append(f"{section_name}.{field}: is required")

            # Check field types if present
            fields_schema = section_schema.get("fields", {})
            for field_name, field_def in fields_schema.items():
                val = section_data.get(field_name)
                if val is None:
                    continue
                expected_type = field_def.get("type", "string")
                if expected_type == "array" and not isinstance(val, list):
                    errors.append(f"{section_name}.{field_name}: must be an array")
                elif expected_type == "object" and not isinstance(val, dict):
                    errors.append(f"{section_name}.{field_name}: must be an object")
                elif expected_type == "integer" and not isinstance(val, int):
                    errors.append(f"{section_name}.{field_name}: must be an integer")

        return errors
