from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from features.cognitive_knowledge.domain.errors import SchemaValidationError


def validate_ckg_json(data: dict[str, Any]) -> bool:
    schema_path = Path(__file__).parent.parent.parent / "schema" / "ckg.schema.json"
    if not schema_path.exists():
        return True
    try:
        schema = json.loads(schema_path.read_text())
    except (OSError, json.JSONDecodeError):
        return True
    try:
        import jsonschema
        jsonschema.validate(instance=data, schema=schema)
        return True
    except ImportError:
        return _basic_validate(data, schema)
    except Exception:
        raise SchemaValidationError("Validación de schema fallida")


def _basic_validate(data: dict[str, Any], schema: dict[str, Any]) -> bool:
    required = schema.get("required", [])
    for field in required:
        if field not in data:
            return False
    return True
