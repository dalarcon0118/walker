#!/usr/bin/env python3
"""
deep_analysis.py — Deterministic 6-layer analysis protocol for sprint goal design.

Migrado desde ``to_clean_next/_internal/deep_analysis.py`` (era el subcomando
``analyze`` de spring.py). Cada capa tiene un schema JSON estricto. El script
controla la secuencia — el LLM debe proporcionar datos estructurados válidos
en cada capa antes de pasar a la siguiente.

6 capas:
  1. Observación   — Qué existe? Hechos, datos, síntomas
  2. Estructura    — Cómo está organizado? Componentes, dependencias, interfaces
  3. Comportamiento — Cómo interactúa? Flujos, estados, secuencias, contratos
  4. Causalidad    — Por qué ocurre? Restricciones, invariantes, causas raíz
  5. Finalidad     — Para qué es? Objetivos, criterios de éxito, trade-offs
  6. Principios    — Qué leyes generales gobiernan el sistema? Patrones, heurísticas

Correcciones aplicadas durante la migración (vs legacy):
  - ``compile_analysis`` usaba ``obs``/``struct``/``beh``/``caus`` sin definirlos
    (NameError en finalize) → ahora se extraen de ``layers``.
  - ``write_json`` dependía de ``_internal.progress_checksum`` → checksum local.
  - ``_run_schema_validation`` lanzaba subprocess ``validate-schema.py`` →
    ``jsonschema`` contra ``schema/plan.schema.json`` y ``schema/progress.schema.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

_ANALYSIS_DIR = ".analysis"

# ── Layer definitions ──────────────────────────────────────────
# Cada capa tiene: id, name, description, prompt_questions, required_fields
# El schema se define inline para validación.

_LAYERS = {
    "layer-01": {
        "id": "layer-01",
        "name": "Observación",
        "description": "¿Qué existe? — Hechos, datos, síntomas del sistema.",
        "prompt_questions": [
            "¿Qué archivos, clases, funciones y módulos existen en el área de interés?",
            "¿Qué configuraciones, tests y datos están presentes?",
            "¿Qué síntomas observables hay (errores, warnings, deuda técnica)?",
            "¿Qué APIs y dependencias externas se utilizan?",
        ],
        "required_fields": ["layer", "facts", "files_scanned"],
        "schema": {
            "type": "object",
            "required": ["layer", "facts", "files_scanned"],
            "properties": {
                "layer": {
                    "const": "observation",
                    "description": "Identificador de la capa",
                },
                "facts": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Hechos observables del sistema",
                    "items": {
                        "type": "object",
                        "required": ["type", "name"],
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "file",
                                    "class",
                                    "function",
                                    "config",
                                    "test",
                                    "data",
                                    "api",
                                    "dependency",
                                    "module",
                                ],
                                "description": "Tipo del hecho observado",
                            },
                            "name": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Nombre del hecho",
                            },
                            "path": {
                                "type": "string",
                                "description": "Ruta relativa al proyecto",
                            },
                            "detail": {
                                "type": "string",
                                "description": "Detalle adicional",
                            },
                        },
                    },
                },
                "symptoms": {
                    "type": "array",
                    "description": "Síntomas observables (errores, warnings, deuda técnica)",
                    "items": {"type": "string", "minLength": 5},
                },
                "files_scanned": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Archivos examinados durante la observación",
                    "items": {"type": "string"},
                },
            },
        },
        "output_summary_fields": ["facts", "symptoms", "files_scanned"],
    },
    "layer-02": {
        "id": "layer-02",
        "name": "Estructura",
        "description": "¿Cómo está organizado? — Componentes, dependencias, interfaces, boundaries.",
        "prompt_questions": [
            "¿Cuáles son los componentes principales (módulos, paquetes, clases)?",
            "¿Cómo se relacionan? (dependencias, imports, herencia, composición)",
            "¿Cuáles son las interfaces y contratos entre componentes?",
            "¿Qué boundaries o bounded contexts existen?",
            "¿Qué componentes faltan o están incompletos?",
        ],
        "required_fields": ["layer", "components"],
        "schema": {
            "type": "object",
            "required": ["layer", "components"],
            "properties": {
                "layer": {
                    "const": "structure",
                    "description": "Identificador de la capa",
                },
                "components": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Componentes del sistema",
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "type": {
                                "type": "string",
                                "enum": [
                                    "module",
                                    "package",
                                    "class",
                                    "interface",
                                    "function",
                                    "config",
                                    "migration",
                                ],
                            },
                            "path": {"type": "string"},
                            "responsibility": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "existing",
                                    "missing",
                                    "incomplete",
                                    "deprecated",
                                ],
                            },
                        },
                    },
                },
                "dependencies": {
                    "type": "array",
                    "description": "Dependencias entre componentes",
                    "items": {
                        "type": "object",
                        "required": ["from", "to"],
                        "properties": {
                            "from": {"type": "string"},
                            "to": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": [
                                    "import",
                                    "call",
                                    "data",
                                    "event",
                                    "inheritance",
                                    "composition",
                                ],
                            },
                        },
                    },
                },
                "interfaces": {
                    "type": "array",
                    "description": "Interfaces y contratos",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "provided_by": {"type": "string"},
                            "consumed_by": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "contract": {"type": "string"},
                        },
                    },
                },
                "boundaries": {
                    "type": "array",
                    "description": "Bounded contexts detectados",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "components": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "files": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        },
        "output_summary_fields": [
            "components",
            "dependencies",
            "interfaces",
            "boundaries",
        ],
    },
    "layer-03": {
        "id": "layer-03",
        "name": "Comportamiento",
        "description": "¿Cómo interactúa? — Flujos, estados, secuencias, contratos.",
        "prompt_questions": [
            "¿Cuáles son los flujos principales del sistema? (request→response, ciclo de vida)",
            "¿Qué estados puede tener cada entidad y cómo transicionan?",
            "¿Cuáles son las secuencias de llamadas entre componentes?",
            "¿Qué contratos (precondiciones, postcondiciones, invariantes) existen?",
            "¿Qué comportamientos están rotos o faltan?",
        ],
        "required_fields": ["layer", "flows"],
        "schema": {
            "type": "object",
            "required": ["layer", "flows"],
            "properties": {
                "layer": {
                    "const": "behavior",
                    "description": "Identificador de la capa",
                },
                "flows": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Flujos del sistema",
                    "items": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "trigger": {"type": "string"},
                            "steps": {"type": "array", "items": {"type": "string"}},
                            "end_state": {"type": "string"},
                        },
                    },
                },
                "states": {
                    "type": "array",
                    "description": "Máquinas de estado de entidades",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity": {"type": "string"},
                            "states": {"type": "array", "items": {"type": "string"}},
                            "transitions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "from": {"type": "string"},
                                        "to": {"type": "string"},
                                        "trigger": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
                "contracts": {
                    "type": "array",
                    "description": "Contratos entre componentes",
                    "items": {
                        "type": "object",
                        "properties": {
                            "interface": {"type": "string"},
                            "preconditions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "postconditions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "invariants": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        "output_summary_fields": ["flows", "states", "contracts"],
    },
    "layer-04": {
        "id": "layer-04",
        "name": "Causalidad",
        "description": "¿Por qué ocurre? — Restricciones, invariantes, causas raíz, puntos de dolor.",
        "prompt_questions": [
            "¿Qué restricciones (business, técnicas, regulatorias) gobiernan el sistema?",
            "¿Qué invariantes deben mantenerse siempre? ¿Alguna está violada?",
            "¿Cuáles son las causas raíz de los síntomas observados en la Capa 1?",
            "¿Qué puntos de dolor existen y con qué impacto?",
            "¿Qué decisiones pasadas causaron la situación actual?",
        ],
        "required_fields": ["layer"],
        "schema": {
            "type": "object",
            "required": ["layer"],
            "properties": {
                "layer": {
                    "const": "causality",
                    "description": "Identificador de la capa",
                },
                "constraints": {
                    "type": "array",
                    "description": "Restricciones del sistema",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "business",
                                    "technical",
                                    "regulatory",
                                    "performance",
                                ],
                            },
                            "description": {"type": "string"},
                            "enforced_by": {"type": "string"},
                        },
                    },
                },
                "invariants": {
                    "type": "array",
                    "description": "Invariantes del sistema",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rule": {"type": "string"},
                            "verified_by": {"type": "string"},
                            "current_status": {
                                "type": "string",
                                "enum": ["satisfied", "violated", "untested"],
                            },
                        },
                    },
                },
                "root_causes": {
                    "type": "array",
                    "description": "Causas raíz de síntomas",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symptom": {"type": "string"},
                            "cause": {"type": "string"},
                            "evidence": {"type": "string"},
                            "fix_proposal": {"type": "string"},
                        },
                    },
                },
                "pain_points": {
                    "type": "array",
                    "description": "Puntos de dolor",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "impact": {
                                "type": "string",
                                "enum": ["low", "medium", "high", "critical"],
                            },
                            "frequency": {"type": "string"},
                        },
                    },
                },
            },
        },
        "output_summary_fields": [
            "constraints",
            "invariants",
            "root_causes",
            "pain_points",
        ],
    },
    "layer-05": {
        "id": "layer-05",
        "name": "Finalidad",
        "description": "¿Para qué existe? — Objetivos, criterios de éxito, trade-offs.",
        "prompt_questions": [
            "¿Cuáles son los objetivos de negocio o técnicos del sprint?",
            "¿Cómo se mide el éxito? (criterios cuantificables)",
            "¿Qué trade-offs se han hecho o se deben hacer?",
            "¿Qué está explícitamente fuera del alcance del sprint?",
            "¿Qué stakeholder se beneficia de cada objetivo?",
        ],
        "required_fields": ["layer", "objectives"],
        "schema": {
            "type": "object",
            "required": ["layer", "objectives"],
            "properties": {
                "layer": {
                    "const": "purpose",
                    "description": "Identificador de la capa",
                },
                "objectives": {
                    "type": "array",
                    "minItems": 1,
                    "description": "Objetivos del sprint",
                    "items": {
                        "type": "object",
                        "required": ["goal"],
                        "properties": {
                            "stakeholder": {"type": "string"},
                            "goal": {"type": "string", "minLength": 5},
                            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                            "success_criteria": {"type": "string"},
                        },
                    },
                },
                "success_criteria": {
                    "type": "array",
                    "description": "Criterios de éxito globales",
                    "items": {"type": "string", "minLength": 5},
                },
                "tradeoffs": {
                    "type": "array",
                    "description": "Trade-offs identificados",
                    "items": {
                        "type": "object",
                        "properties": {
                            "decision": {"type": "string"},
                            "chosen": {"type": "string"},
                            "rejected": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                    },
                },
                "non_goals": {
                    "type": "array",
                    "description": "Explícitamente fuera de alcance",
                    "items": {"type": "string"},
                },
            },
        },
        "output_summary_fields": [
            "objectives",
            "success_criteria",
            "tradeoffs",
            "non_goals",
        ],
    },
    "layer-06": {
        "id": "layer-06",
        "name": "Principios",
        "description": "¿Qué leyes generales gobiernan el sistema? — Patrones, heurísticas, arquitectura.",
        "prompt_questions": [
            "¿Qué patrones arquitectónicos y de diseño se usan en el proyecto?",
            "¿Qué heurísticas o reglas no escritas sigue el equipo?",
            "¿Qué estilo arquitectónico predomina? (hexagonal, DDD, MVC, etc.)",
            "¿Qué principles generales deben mantenerse durante el sprint?",
            "¿Qué convenciones de código, testing y naming existen?",
        ],
        "required_fields": ["layer"],
        "schema": {
            "type": "object",
            "required": ["layer"],
            "properties": {
                "layer": {
                    "const": "principles",
                    "description": "Identificador de la capa",
                },
                "patterns": {
                    "type": "array",
                    "description": "Patrones identificados",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": [
                                    "architectural",
                                    "design",
                                    "coding",
                                    "testing",
                                    "data",
                                ],
                            },
                            "location": {"type": "string"},
                            "variant": {"type": "string"},
                        },
                    },
                },
                "heuristics": {
                    "type": "array",
                    "description": "Heurísticas y reglas del equipo",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rule": {"type": "string"},
                            "applies_to": {"type": "string"},
                            "source": {
                                "type": "string",
                                "enum": [
                                    "team_convention",
                                    "industry",
                                    "legacy",
                                    "architecture",
                                ],
                            },
                        },
                    },
                },
                "architecture_style": {
                    "type": "string",
                    "description": "Estilo arquitectónico predominante",
                },
                "governing_principles": {
                    "type": "array",
                    "description": "Principios rectores para el sprint",
                    "items": {"type": "string"},
                },
            },
        },
        "output_summary_fields": [
            "patterns",
            "heuristics",
            "architecture_style",
            "governing_principles",
        ],
    },
}

_LAYER_ORDER = ["layer-01", "layer-02", "layer-03", "layer-04", "layer-05", "layer-06"]

_LAYER_FILE_MAP = {
    "layer-01": "layer-01-observation.json",
    "layer-02": "layer-02-structure.json",
    "layer-03": "layer-03-behavior.json",
    "layer-04": "layer-04-causality.json",
    "layer-05": "layer-05-purpose.json",
    "layer-06": "layer-06-principles.json",
}


# ── Helpers ────────────────────────────────────────────────────


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


def load_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _serialize_deterministic(data: dict) -> str:
    """Serialize dict to JSON with sorted keys for a deterministic hash."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def compute_checksum(data: dict) -> str:
    """SHA-256 del contenido excluyendo el campo ``_checksum``."""
    data_copy = {k: v for k, v in data.items() if k != "_checksum"}
    raw = _serialize_deterministic(data_copy)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def add_checksum(data: dict) -> dict:
    """Añade ``_checksum`` al dict (in-place y retorna)."""
    data["_checksum"] = compute_checksum(data)
    return data


def verify_checksum(data: dict) -> bool:
    """Verifica que ``_checksum`` coincida con el contenido."""
    if "_checksum" not in data:
        return True  # backward compat: archivos legacy sin checksum se aceptan
    stored = data["_checksum"]
    computed = compute_checksum(data)
    return stored == computed


def write_json(path: str, data: dict) -> None:
    # Integridad para progress.json (checksum SHA-256)
    if os.path.basename(path) == "progress.json":
        add_checksum(data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Validation ─────────────────────────────────────────────────


def validate_layer_data(data: dict, layer_id: str) -> list[str]:
    """Valida los datos de una capa contra su schema. Retorna lista de errores."""
    layer_def = _LAYERS.get(layer_id)
    if not layer_def:
        return [f"Capa desconocida: {layer_id}"]

    errors: list[str] = []
    schema = layer_def["schema"]

    # Top-level debe ser dict
    if not isinstance(data, dict):
        return [f"El layer debe ser un objeto JSON, recibido {type(data).__name__}"]

    # Campos requeridos
    for req in schema.get("required", []):
        if req not in data:
            errors.append(f'Campo requerido faltante: "{req}"')
        elif data[req] is None:
            errors.append(f'Campo "{req}" es None, debe tener valor')

    # Const del layer
    const_val = schema["properties"].get("layer", {}).get("const")
    if const_val and data.get("layer") != const_val:
        errors.append(f'layer debe ser "{const_val}", recibido "{data.get("layer")}"')

    # Arrays recursivamente
    errors.extend(_validate_arrays(data, schema, ""))

    return errors


def _validate_arrays(data: dict, schema: dict, prefix: str) -> list[str]:
    """Valida arrays y sus items recursivamente."""
    errors: list[str] = []
    for field, field_schema in schema.get("properties", {}).items():
        if field not in data:
            continue
        val = data[field]

        if field_schema.get("type") == "array":
            if not isinstance(val, list):
                errors.append(
                    f'"{prefix}{field}" debe ser un array, recibido {type(val).__name__}'
                )
                continue
            min_items = field_schema.get("minItems", 0)
            if len(val) < min_items:
                errors.append(
                    f'"{prefix}{field}" debe tener al menos {min_items} elemento(s), '
                    f"tiene {len(val)}"
                )

            item_schema = field_schema.get("items", {})
            item_props = item_schema.get("properties", {})
            item_required = item_schema.get("required", [])
            for i, item in enumerate(val):
                if not isinstance(item, dict):
                    continue
                item_prefix = f"{prefix}{field}[{i}]."
                # Sub-campos requeridos
                for sub_req in item_required:
                    if sub_req not in item:
                        errors.append(f'"{item_prefix}{sub_req}" es requerido')
                    elif item[sub_req] is None:
                        errors.append(f'"{item_prefix}{sub_req}" es None')
                    elif isinstance(item[sub_req], str) and len(item[sub_req]) == 0:
                        errors.append(f'"{item_prefix}{sub_req}" no debe estar vacío')

                # Enums
                for sub_field, sub_schema in item_props.items():
                    if sub_field not in item:
                        continue
                    enum_vals = sub_schema.get("enum")
                    if enum_vals and item[sub_field] not in enum_vals:
                        errors.append(
                            f'"{item_prefix}{sub_field}" debe ser uno de '
                            f'{enum_vals}, recibido "{item[sub_field]}"'
                        )

            # Arrays anidados dentro de items
            if item_schema.get("type") == "object":
                item_props_nested = item_schema.get("properties", {})
                for sub_field in item_props_nested:
                    sub_schema = item_props_nested[sub_field]
                    if sub_schema.get("type") == "array":
                        for i, item in enumerate(val):
                            if isinstance(item, dict) and sub_field in item:
                                sub_prefix = f"{prefix}{field}[{i}]."
                                sub_schema_wrapper = {"properties": {sub_field: sub_schema}}
                                errors.extend(_validate_arrays(item, sub_schema_wrapper, sub_prefix))

    return errors


def _get_layer_index(layer_id: str) -> int | None:
    try:
        return _LAYER_ORDER.index(layer_id)
    except ValueError:
        return None


def _base_task_fields() -> dict:
    """Campos base de una tarea (cumple schema plan.schema.json vigente — formato delta)."""
    return {
        "role": "core",
        "scrum_role": "dev",
        "importer_files": 0,
        "depends_on": [],
        "preconditions": [],
        "quality_gates": [],
        "loop_verification": [],
        "loop_acceptance": "all",
        "decisions": [],
        "refinement": {"attempts": 0, "dead_ends": []},
    }


# ── Paths ──────────────────────────────────────────────────────


def _sanitize_chain_id(raw: str) -> str:
    """Normaliza un chain_id para cumplir el schema ``^[a-z0-9-]+$``.

    El chain_id se deriva del basename del chain_dir; en macOS los
    directorios temporales pueden contener ``_`` (ej: ``tmpzno1o_4s``),
    lo que rompía la validación de plan/progress schema.
    """
    sanitized = re.sub(r"[^a-z0-9-]", "-", raw.lower())
    return sanitized.strip("-") or "chain"


def _get_analysis_dir(chain_dir: str) -> str:
    return os.path.join(chain_dir, _ANALYSIS_DIR)


def _get_meta_path(chain_dir: str) -> str:
    return os.path.join(_get_analysis_dir(chain_dir), "meta.json")


def _get_layer_path(chain_dir: str, layer_id: str) -> str:
    fname = _LAYER_FILE_MAP.get(layer_id, f"{layer_id}.json")
    return os.path.join(_get_analysis_dir(chain_dir), fname)


def _get_compiled_path(chain_dir: str) -> str:
    return os.path.join(_get_analysis_dir(chain_dir), "compiled-analysis.json")


def _repo_root() -> str:
    """Raíz del repo walker (donde vive ``schema/``)."""
    # features/code_analysis/application/deep_analysis.py → 3 niveles hacia arriba
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _run_schema_validation(json_path: str, schema_name: str) -> bool:
    """Valida un archivo JSON contra el schema en ``schema/`` con jsonschema."""
    schema_path = os.path.join(_repo_root(), "schema", schema_name)
    if not os.path.exists(schema_path):
        eprint(f"  ❌ schema no encontrado en {schema_path}")
        return False

    try:
        schema = json.loads(open(schema_path).read())
        data = json.loads(open(json_path).read())
        import jsonschema

        validator = jsonschema.Draft7Validator(schema)
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if errors:
            eprint("  ❌ Schema validation failed:")
            for err in errors[:10]:
                loc = ".".join(str(p) for p in err.path) or "<root>"
                eprint(f"    • {loc}: {err.message}")
            return False
        return True
    except ImportError:
        # Fallback sin jsonschema: validación básica de required
        data = json.loads(open(json_path).read())
        required = schema.get("required", [])
        missing = [r for r in required if r not in data]
        if missing:
            eprint(f"  ❌ Faltan campos requeridos: {missing}")
            return False
        return True
    except Exception as e:
        eprint(f"  ❌ Error validando contra {schema_name}: {e}")
        return False


# ── Layer prompt generation ────────────────────────────────────


def build_layer_description(layer_id: str, previous_summary: str = "") -> str:
    """Construye el prompt legible de una capa, incluyendo contexto previo."""
    layer_def = _LAYERS.get(layer_id)
    if not layer_def:
        return f"Capa desconocida: {layer_id}"

    idx = _get_layer_index(layer_id) or 0
    total = len(_LAYER_ORDER)
    progress_bar = "▓" * idx + "░" * (total - idx)

    lines = []
    lines.append("=" * 70)
    lines.append(f"  📋 CAPA {idx + 1} DE {total}: {layer_def['name']}")
    lines.append(f"  {progress_bar}  ({idx + 1}/{total})")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  {layer_def['description']}")
    lines.append("")
    lines.append("  🎯 Preguntas guía:")
    for q in layer_def["prompt_questions"]:
        lines.append(f"    • {q}")

    lines.append("")
    lines.append("  📐 Schema JSON esperado:")
    lines.append(f"  {json.dumps(layer_def['schema'], indent=2)}")

    if previous_summary:
        lines.append("")
        lines.append("  📊 Contexto de capas anteriores:")
        lines.append(f"  {previous_summary}")

    lines.append("")
    lines.append("=" * 70)
    lines.append("📋 INSTRUCCIONES:")
    lines.append("")
    lines.append(f'  1. Ejecuta el análisis de la capa "{layer_def["name"]}"')
    lines.append("     sobre el código real del proyecto.")
    lines.append("  2. Proporciona el resultado como --data JSON válido contra")
    lines.append("     el schema arriba.")
    lines.append("  3. Ejecuta:")
    lines.append(f'     python3 scripts/spring.py analyze {layer_id} <chain_dir> \\')
    lines.append('       --data \'{"layer": "<layer_tag>", ...}\'')
    lines.append("")
    lines.append("⚠️  El script validará el JSON contra el schema.")
    lines.append("   Si es inválido, deberás corregirlo y reintentar.")
    lines.append("=" * 70)

    return "\n".join(lines)


# ── INIT ───────────────────────────────────────────────────────


def init_analysis(chain_dir: str, project_id: str, goal: str) -> int:
    """Inicializa una sesión de análisis profundo. Retorna código de salida."""
    analysis_dir = _get_analysis_dir(chain_dir)

    # Ya existe un análisis
    meta_path = _get_meta_path(chain_dir)
    existing = load_json(meta_path)
    if existing:
        eprint(f"❌ Ya existe un análisis en {chain_dir}/")
        eprint("   Para reiniciar, elimina el directorio .analysis/ manualmente.")
        return 2

    os.makedirs(analysis_dir, exist_ok=True)

    meta = {
        "chain_dir": chain_dir,
        "project_id": project_id,
        "goal": goal,
        "created_at": _now_iso(),
        "status": "in_progress",
        "completed_layers": [],
        "current_layer": "layer-01",
    }
    write_json(meta_path, meta)

    print(build_layer_description("layer-01"))
    print()
    print("📝 Comando para enviar la Capa 1:")
    print(
        f"  python3 scripts/spring.py analyze layer-01 {chain_dir} \\\n"
        f"    --data '<JSON según el schema>'"
    )

    return 0


# ── LAYER N ────────────────────────────────────────────────────


def submit_layer(chain_dir: str, layer_id: str, data: dict) -> int:
    """Valida y registra los datos de una capa. Retorna código de salida."""
    if layer_id not in _LAYER_ORDER:
        eprint(f"❌ Capa inválida: {layer_id}")
        eprint(f"   Capas válidas: {', '.join(_LAYER_ORDER)}")
        return 2

    meta = load_json(_get_meta_path(chain_dir))
    if not meta:
        eprint("❌ No hay análisis iniciado. Ejecuta primero init_analysis().")
        return 2

    # Secuencia forzada
    current_idx = _get_layer_index(layer_id)
    completed = meta.get("completed_layers", [])

    if current_idx is not None and current_idx > 0:
        prev_layer = _LAYER_ORDER[current_idx - 1]
        if prev_layer not in completed:
            eprint(
                f"❌ Secuencia forzada: la capa {prev_layer} debe completarse antes de {layer_id}"
            )
            eprint(f"   Capas completadas: {completed}")
            eprint(f"   Próxima capa requerida: {prev_layer}")
            return 2

    if layer_id in completed:
        eprint(f"❌ La capa {layer_id} ya fue completada.")
        eprint(f"   Capas completadas: {completed}")
        if current_idx is not None and current_idx + 1 < len(_LAYER_ORDER):
            next_layer = _LAYER_ORDER[current_idx + 1]
            eprint(f"   Siguiente capa: {next_layer}")
        return 2

    if not isinstance(data, dict):
        eprint("❌ Los datos deben ser un objeto JSON (dict)")
        return 2

    # Validación contra schema
    errors = validate_layer_data(data, layer_id)
    if errors:
        eprint("=" * 70)
        eprint("❌ VALIDATION FAILED — la capa no cumple el schema")
        eprint("=" * 70)
        for err in errors:
            eprint(f"  • {err}")
        eprint()
        eprint("📋 Acción requerida:")
        eprint("  Corrige el JSON según los errores arriba y reintenta.")
        eprint("=" * 70)
        return 2

    # Guardar datos de la capa
    layer_path = _get_layer_path(chain_dir, layer_id)
    write_json(layer_path, data)

    # Actualizar meta
    completed = meta.get("completed_layers", [])
    completed.append(layer_id)
    meta["completed_layers"] = completed

    current_idx = _get_layer_index(layer_id)
    next_layer_id = None
    if current_idx is not None and current_idx + 1 < len(_LAYER_ORDER):
        next_layer_id = _LAYER_ORDER[current_idx + 1]
        meta["current_layer"] = next_layer_id
    else:
        meta["current_layer"] = "finalize"
        meta["ready_to_finalize"] = True

    write_json(_get_meta_path(chain_dir), meta)

    previous_summary = _build_summary(chain_dir, completed)

    layer_def = _LAYERS.get(layer_id, {})
    item_count = 0
    for field in layer_def.get("output_summary_fields", []):
        items = data.get(field, [])
        if isinstance(items, list):
            item_count += len(items)

    print("=" * 70)
    print(f"✅ CAPA {layer_id} ({layer_def.get('name', layer_id)}) COMPLETADA")
    print("=" * 70)
    print(f"  Items registrados: {item_count}")
    print(f"  Capas completadas: {len(completed)}/{len(_LAYER_ORDER)}")
    print()

    for field in _LAYERS[layer_id].get("output_summary_fields", []):
        items = data.get(field, [])
        if items:
            print(f"  📊 {field}: {len(items)} items")

    print()

    if next_layer_id:
        print(build_layer_description(next_layer_id, previous_summary))
        print()
        print("📝 Comando para enviar la siguiente capa:")
        print(
            f"  python3 scripts/spring.py analyze {next_layer_id} {chain_dir} \\\n"
            f"    --data '<JSON según el schema>'"
        )
        print()
        print("📋 DECISION PROTOCOL:")
        print("  ▶️  Analiza la siguiente capa siguiendo las preguntas guía")
        print(f"     y proporciona el JSON para {next_layer_id}")
    else:
        print("🎯 TODAS LAS CAPAS COMPLETADAS")
        print()
        print("📋 DECISION PROTOCOL:")
        print("  ▶️  Finaliza el análisis y genera el sprint:")

    return 0


# ── FINALIZE ───────────────────────────────────────────────────


def _build_summary(chain_dir: str, completed_layers: list[str]) -> str:
    """Resumen textual de las capas completadas para inyección de contexto."""
    parts: list[str] = []
    for layer_id in _LAYER_ORDER:
        if layer_id not in completed_layers:
            continue
        layer_data = load_json(_get_layer_path(chain_dir, layer_id))
        if not layer_data:
            continue
        layer_def = _LAYERS.get(layer_id, {})
        name = layer_def.get("name", layer_id)

        summary_items: list[str] = []
        for field in layer_def.get("output_summary_fields", []):
            items = layer_data.get(field, [])
            if items:
                count = len(items)
                examples = []
                for item in items[:3]:
                    if isinstance(item, dict):
                        examples.append(
                            item.get(
                                "name",
                                item.get(
                                    "goal",
                                    item.get("rule", item.get("description", "")),
                                ),
                            )
                        )
                    else:
                        examples.append(str(item))
                examples_str = ", ".join(examples[:2])
                trim = examples_str[:100]
                summary_items.append(
                    f"    {field}: {count} items [{trim}...]"
                    if trim
                    else f"    {field}: {count} items"
                )

        if summary_items:
            parts.append(f"  [{name}]:")
            parts.extend(summary_items)

    return "\n".join(parts)


# ── Layer extractors ───────────────────────────────────────────


def _extract_layer_1_observations(
    data: dict, must_exist: list, must_not_exist: list, invariants: list
) -> None:
    """Layer 1: Observation facts."""
    for fact in data.get("facts", []):
        ftype = fact.get("type", "")
        fname = fact.get("name", "")
        if ftype in ("function", "class", "api", "module", "config"):
            must_exist.append(f"{ftype} {fname}")


def _extract_layer_2_structure(
    data: dict, must_exist: list, must_not_exist: list, invariants: list
) -> None:
    """Layer 2: Structure components."""
    for comp in data.get("components", []):
        if comp.get("status") == "missing":
            must_exist.append(f"{comp.get('type', 'component')} {comp['name']}")
        elif comp.get("status") == "deprecated":
            must_not_exist.append(
                f"{comp.get('type', 'component')} {comp['name']} (deprecated)"
            )


def _extract_layer_3_behavior(
    data: dict, must_exist: list, must_not_exist: list, invariants: list
) -> None:
    """Layer 3: Behavior contracts + invariants."""
    for ct in data.get("contracts", []):
        for inv in ct.get("invariants", []):
            if inv not in invariants:
                invariants.append(inv)


def _extract_layer_4_causality(
    data: dict, must_exist: list, must_not_exist: list, invariants: list
) -> None:
    """Layer 4: Root causes, invariants, pain points."""
    for rc in data.get("root_causes", []):
        fix = rc.get("fix_proposal", "")
        if fix and len(fix) > 5:
            must_exist.append(fix)
    for inv_item in data.get("invariants", []):
        rule = inv_item.get("rule", "")
        status = inv_item.get("current_status", "")
        if status == "violated":
            must_not_exist.append(rule)
        elif rule and rule not in invariants:
            invariants.append(rule)
    for pain in data.get("pain_points", []):
        desc = pain.get("description", "")
        impact = pain.get("impact", "medium")
        if impact in ("high", "critical") and desc:
            must_exist.append(f"fix: {desc}")


def _extract_layer_5_purpose(
    data: dict, must_exist: list, must_not_exist: list, invariants: list
) -> None:
    """Layer 5: Non-goals and objectives."""
    for ng in data.get("non_goals", []):
        if ng:
            must_not_exist.append(ng)
    for obj in data.get("objectives", []):
        g = obj.get("goal", "")
        if g and g not in must_exist:
            must_exist.append(f"objective: {g}")


def _extract_layer_6_principles(
    data: dict, must_exist: list, must_not_exist: list, invariants: list
) -> None:
    """Layer 6: Heuristics and governing principles."""
    for hp in data.get("heuristics", []):
        rule = hp.get("rule", "")
        if rule and rule not in invariants:
            invariants.append(rule)
    for gp in data.get("governing_principles", []):
        if gp and gp not in invariants:
            invariants.append(gp)


def _extract_all_predicates(
    layers: dict[str, dict],
) -> tuple[list[str], list[str], list[str]]:
    """Extrae y deduplica predicates de las 6 capas."""
    must_exist: list[str] = []
    must_not_exist: list[str] = []
    invariants: list[str] = []

    for layer_id, handler in [
        ("layer-01", _extract_layer_1_observations),
        ("layer-02", _extract_layer_2_structure),
        ("layer-03", _extract_layer_3_behavior),
        ("layer-04", _extract_layer_4_causality),
        ("layer-05", _extract_layer_5_purpose),
        ("layer-06", _extract_layer_6_principles),
    ]:
        data = layers.get(layer_id, {})
        handler(data, must_exist, must_not_exist, invariants)

    return (
        list(dict.fromkeys(must_exist)),
        list(dict.fromkeys(must_not_exist)),
        list(dict.fromkeys(invariants)),
    )


def _collect_known_files(obs: dict, struct: dict) -> list[str]:
    """Recolecta archivos conocidos de las capas de observacion y estructura."""
    known: list[str] = []
    for fact in obs.get("facts", []):
        p = fact.get("path", "")
        if p and p not in known:
            known.append(p)
    for comp in struct.get("components", []):
        p = comp.get("path", "")
        if p and p not in known:
            known.append(p)
    for f in obs.get("files_scanned", []):
        if f not in known:
            known.append(f)
    return known


def _match_files_for(predicate: str, known_files: list[str]) -> list[str]:
    """Matchea un predicate contra los archivos conocidos (heuristica)."""
    matched: list[str] = []
    pred_lower = predicate.lower()
    for f in known_files:
        parts = (
            f.replace("/", " ")
            .replace("\\", " ")
            .replace("_", " ")
            .replace("-", " ")
            .lower()
            .split()
        )
        if any(p in pred_lower for p in parts if len(p) > 2):
            matched.append(f)
        if len(matched) >= 3:
            break
    if not matched and known_files:
        matched = [known_files[0]]
    elif not matched:
        matched = ["src/main.py"]
    return matched


def _build_tasks_from_predicates(
    must_exist: list[str],
    must_not_exist: list[str],
    invariants: list[str],
    domains: list[str],
    project_id: str,
    match_files,
) -> tuple[list[dict], int]:
    """Genera tareas (feature/bugfix/test) desde los predicates."""
    tasks: list[dict] = []
    task_num = 1

    for p in must_exist:
        p_domain = domains[0] if domains else project_id
        for d in domains:
            if d.lower() in p.lower():
                p_domain = d
                break

        p_type = "feature"
        p_work_type = "refactor"
        if "fix:" in p:
            p_type = "bugfix"
            p_work_type = "bugfix"

        tasks.append(
            {
                **_base_task_fields(),
                "task_id": f"TASK-{task_num:03d}",
                "type": p_type,
                "work_type": p_work_type,
                "objective": f"Implementar {p}",
                "result_state": f"{p} implementado y verificado",
                "task_description": f"Implementar: {p}",
                "domain": p_domain,
                "validation": {"predicates": [p]},
                "impacted_artifacts": {"files": match_files(p)},
                "risks": {"curvature": 0.15},
                "acceptance_criteria": [f"{p} implementado y verificado"],
            }
        )
        task_num += 1

    for p in must_not_exist:
        p_domain = domains[0] if domains else project_id
        tasks.append(
            {
                **_base_task_fields(),
                "task_id": f"TASK-{task_num:03d}",
                "type": "bugfix",
                "work_type": "bugfix",
                "objective": f"Eliminar o migrar {p}",
                "result_state": f"{p} eliminado",
                "task_description": f"Eliminar o migrar: {p}",
                "domain": p_domain,
                "validation": {"predicates": [f"NO {p}"]},
                "impacted_artifacts": {"files": match_files(p)},
                "risks": {"curvature": 0.20},
                "acceptance_criteria": [f"{p} eliminado"],
            }
        )
        task_num += 1

    for inv in invariants:
        p_domain = domains[0] if domains else project_id
        tasks.append(
            {
                **_base_task_fields(),
                "task_id": f"TASK-{task_num:03d}",
                "type": "test",
                "work_type": "refactor",
                "objective": f"Verificar: {inv}",
                "result_state": f"{inv} verificado",
                "task_description": f"Verificar: {inv}",
                "domain": p_domain,
                "validation": {"predicates": [f"invariant: {inv}"]},
                "impacted_artifacts": {"files": match_files(inv)},
                "risks": {"curvature": 0.10},
                "acceptance_criteria": [f"{inv} verificado"],
            }
        )
        task_num += 1

    return tasks, task_num


def _append_fallback_task(
    tasks: list[dict], goal: str, project_id: str, known_files: list[str]
) -> None:
    """Agrega una tarea fallback cuando no hay predicates."""
    fallback_files = known_files[:1] if known_files else ["src/main.py"]
    tasks.append(
        {
            **_base_task_fields(),
            "task_id": "TASK-001",
            "type": "feature",
            "work_type": "refactor",
            "objective": goal[:200],
            "result_state": f"{goal[:100]} completado",
            "task_description": goal[:100],
            "domain": project_id,
            "validation": {"predicates": [goal[:200]]},
            "impacted_artifacts": {"files": fallback_files},
            "risks": {"curvature": 0.20},
            "acceptance_criteria": [f"{goal[:100]} completado"],
        }
    )


def _fill_task_contexts(tasks: list[dict], goal: str, project_id: str) -> None:
    """Completa task_context de cada tarea (requerido por schema)."""
    for t in tasks:
        obj = t.get("objective", goal)[:100]
        files = t.get("impacted_artifacts", {}).get("files", [])
        file_list = ", ".join(files) if files else "por determinar"
        domain = t.get("domain", project_id)
        t["task_context"] = {
            "observation": f"Objetivo: {obj}. Archivos: {file_list}.",
            "structure": f"Archivos {file_list} en dominio {domain}.",
            "behavior": f"Implementar: {obj}.",
            "causality": "Restricciones del analisis.",
            "purpose": f"Cumplir goal: {goal[:80]}.",
            "principles": f"Convenciones del proyecto en {domain}.",
        }
        if not t.get("task_description"):
            t["task_description"] = obj
        if not t.get("acceptance_criteria"):
            t["acceptance_criteria"] = [t.get("result_state", obj)]


_TASK_REQUIRED_FIELDS_LOCAL = [
    "task_id",
    "type",
    "objective",
    "result_state",
    "task_description",
    "domain",
    "impacted_artifacts",
    "risks",
    "task_context",
    "validation",
    "role",
    "scrum_role",
    "work_type",
    "importer_files",
    "depends_on",
    "preconditions",
    "acceptance_criteria",
    "quality_gates",
    "loop_verification",
    "loop_acceptance",
    "decisions",
    "refinement",
]


def _validate_task_schema(tasks: list[dict]) -> list[str]:
    """Valida que cada tarea tenga todos los campos requeridos del schema."""
    errors: list[str] = []
    for t in tasks:
        tid = t.get("task_id", "?")
        for fld in _TASK_REQUIRED_FIELDS_LOCAL:
            if fld not in t:
                errors.append(f"  ❌ Tarea {tid}: falta campo requerido '{fld}'")
            elif t[fld] is None:
                errors.append(f"  ❌ Tarea {tid}: campo '{fld}' es null")
    return errors


def _compute_total_risk(
    must_exist: list[str], must_not_exist: list[str], struct: dict, caus: dict
) -> float:
    """Calcula el riesgo arquitectónico compuesto del análisis."""
    base_curvature = min(0.05 + (len(must_exist) + len(must_not_exist)) * 0.02, 0.40)
    struct_complexity = min(len(struct.get("components", [])) * 0.01, 0.10)
    pain_risk = min(len(caus.get("pain_points", [])) * 0.02, 0.15)
    return min(base_curvature + struct_complexity + pain_risk, 0.50)


def _build_plan_payload(
    chain_id: str,
    meta: dict,
    project_id: str,
    goal: str,
    domains: list[str],
    obs: dict,
    struct: dict,
    beh: dict,
    must_exist: list[str],
    must_not_exist: list[str],
    invariants: list[str],
    tasks: list[dict],
) -> dict:
    """Construye el payload de plan.json."""
    now = _now_iso()
    meta_project_type = meta.get("project_type", "backend")
    meta_tags = meta.get("tags", [])
    if not meta_tags:
        meta_tags = ["python"]

    return {
        "plan": {
            "meta": {
                "chain_id": chain_id,
                "generated_at": now,
                "version": "1.0",
                "source": "delta-skill",
                "type": "plan",
            },
            "project": {
                "project_id": project_id,
                "project_type": meta_project_type,
                "domains": domains,
                "tags": meta_tags,
            },
            "goal": {
                "description": goal,
                "path_summary": f"Generado desde análisis profundo de {len(obs.get('files_scanned', []))} archivos, "
                f"{len(struct.get('components', []))} componentes, "
                f"{len(beh.get('flows', []))} flujos",
                "predicates": {
                    "must_exist": must_exist,
                    "must_not_exist": must_not_exist,
                    "invariants": invariants,
                },
                "signatures": [],
            },
            "tasks": tasks,
            "parameters": {},
        }
    }


def _build_progress_payload(
    chain_id: str,
    project_id: str,
    goal: str,
    tasks: list[dict],
    must_exist: list[str],
    must_not_exist: list[str],
    invariants: list[str],
    total_risk: float,
) -> dict:
    """Construye el payload de progress.json."""
    now = _now_iso()
    task_states: dict[str, str] = {}
    next_action = None
    for t in tasks:
        tid = t["task_id"]
        task_states[tid] = "pending"
        if next_action is None:
            next_action = {
                "task_id": tid,
                "type": t.get("type", "feature"),
                "work_type": t.get("work_type", "refactor"),
                "target": "",
                "delta": 1.0,
                "confidence": 0.70,
                "risk": t.get("risks", {}).get("curvature", 0.20),
                "score": 0.0,
                "candidate": t.get("objective", "")[:100],
                "rationale": f"Primera tarea del análisis profundo: {t.get('objective', '')[:60]}...",
            }

    return {
        "progress": {
            "meta": {
                "chain_id": chain_id,
                "generated_at": now,
                "version": "1.0",
                "source": "delta-skill",
                "type": "progress",
                "status": "active",
            },
            "plan_ref": chain_id,
            "trail": [
                {
                    "at": now,
                    "distance": 1.0,
                    "event": (
                        f"🧠 Sprint creado desde análisis profundo (6 capas). "
                        f"{len(tasks)} tareas generadas de {len(must_exist)} must_exist, "
                        f"{len(must_not_exist)} must_not_exist, {len(invariants)} invariants."
                    ),
                }
            ],
            "current": {
                "task_states": task_states,
                "next_action": next_action,
                "alternatives": [],
                "risks": [
                    {
                        "description": "Riesgo arquitectónico compuesto del análisis profundo",
                        "severity": "medium" if total_risk > 0.25 else "low",
                        "curvature": round(total_risk, 2),
                    }
                ],
                "delta_metrics": {},
            },
        }
    }


def compile_analysis(chain_dir: str) -> tuple[dict, dict, dict | None]:
    """Compila las 6 capas en plan.json + progress.json.

    Returns (plan, progress, error_dict).
    error_dict es None en éxito, o dict con mensaje de error.
    """
    meta = load_json(_get_meta_path(chain_dir))
    if not meta:
        return {}, {}, {"error": "No meta.json found"}

    project_id = meta.get("project_id", "")
    goal = meta.get("goal", "")
    chain_id = _sanitize_chain_id(os.path.basename(os.path.normpath(chain_dir)))

    # Cargar datos de todas las capas
    layers: dict[str, dict] = {}
    for layer_id in _LAYER_ORDER:
        data = load_json(_get_layer_path(chain_dir, layer_id))
        if data:
            layers[layer_id] = data

    # (FIX legacy) obs/struct/beh/caus se referenciaban sin definir → NameError
    obs = layers.get("layer-01", {})
    struct = layers.get("layer-02", {})
    beh = layers.get("layer-03", {})
    caus = layers.get("layer-04", {})

    # ── Extraer predicates de todas las capas ─────────────────
    must_exist, must_not_exist, invariants = _extract_all_predicates(layers)

    # ── Archivos conocidos de las capas ───────────────────────
    _known_files = _collect_known_files(obs, struct)

    def _match_files(predicate: str) -> list[str]:
        return _match_files_for(predicate, _known_files)

    # ── Generar tareas desde predicates ───────────────────────
    domains = [b.get("name", project_id) for b in struct.get("boundaries", [])]
    if not domains:
        domains = [project_id]

    tasks, task_num = _build_tasks_from_predicates(
        must_exist, must_not_exist, invariants, domains, project_id, _match_files
    )

    if not tasks:
        _append_fallback_task(tasks, goal, project_id, _known_files)

    # ── task_context (requerido por schema) ───────────────────
    _fill_task_contexts(tasks, goal, project_id)

    # ── PRE-WRITE VALIDATION ──────────────────────────────────
    vw_errors = _validate_task_schema(tasks)
    if vw_errors:
        eprint()
        eprint("=" * 70)
        eprint("❌ VALIDACIÓN PRE-WRITE: Faltan campos requeridos en tareas del análisis")
        eprint("=" * 70)
        for e in vw_errors:
            eprint(e)
        eprint()
        eprint("  Referencia: schema/plan.schema.json → $defs/task → required")
        eprint("  Corrige las capas de análisis y reintenta.")
        eprint("=" * 70)
        return {}, {}, {"error": "Tareas no cumplen schema"}

    # ── Curvatura desde complejidad del análisis ──────────────
    total_risk = _compute_total_risk(must_exist, must_not_exist, struct, caus)

    # ── plan.json ─────────────────────────────────────────────
    plan = _build_plan_payload(
        chain_id, meta, project_id, goal, domains, obs, struct, beh,
        must_exist, must_not_exist, invariants, tasks,
    )

    # ── progress.json ─────────────────────────────────────────
    progress = _build_progress_payload(
        chain_id, project_id, goal, tasks,
        must_exist, must_not_exist, invariants, total_risk,
    )

    return plan, progress, None


def finalize_analysis(chain_dir: str) -> int:
    """Compila las 6 capas en plan.json + progress.json. Retorna código de salida."""
    meta = load_json(_get_meta_path(chain_dir))
    if not meta:
        eprint("❌ No hay análisis iniciado. Ejecuta primero init_analysis().")
        return 2

    completed = meta.get("completed_layers", [])
    missing = [l for l in _LAYER_ORDER if l not in completed]
    if missing:
        eprint(f"❌ No todas las capas están completadas. Faltan: {', '.join(missing)}")
        eprint("   Completa las capas faltantes antes de finalizar.")
        return 2

    plan, progress, error = compile_analysis(chain_dir)
    if error:
        eprint(f"❌ Error al compilar análisis: {error['error']}")
        return 2

    # Guardar análisis compilado como referencia
    compiled_path = _get_compiled_path(chain_dir)
    write_json(
        compiled_path,
        {
            "compiled_at": _now_iso(),
            "project_id": meta.get("project_id", ""),
            "goal": meta.get("goal", ""),
            "metadata": {
                "layers_completed": len(_LAYER_ORDER),
                "predicates": {
                    "must_exist": len(plan["plan"]["goal"]["predicates"]["must_exist"]),
                    "must_not_exist": len(plan["plan"]["goal"]["predicates"]["must_not_exist"]),
                    "invariants": len(plan["plan"]["goal"]["predicates"]["invariants"]),
                },
                "tasks": len(plan["plan"]["tasks"]),
            },
        },
    )

    # Escribir plan.json + progress.json
    plan_path = os.path.join(chain_dir, "plan.json")
    progress_path = os.path.join(chain_dir, "progress.json")
    write_json(plan_path, plan)
    write_json(progress_path, progress)

    # ── ENFORCEMENT: validar schemas post-write ───────────────
    plan_valid = _run_schema_validation(plan_path, "plan.schema.json")
    prog_valid = _run_schema_validation(progress_path, "progress.schema.json")

    if not plan_valid or not prog_valid:
        eprint()
        eprint("=" * 70)
        eprint("❌ ENFORCEMENT BLOCKED: Schema validation failed")
        eprint("=" * 70)
        eprint("  Los archivos generados tienen datos inválidos contra el schema.")
        eprint("  El análisis profundo produjo predicates o tareas que no cumplen")
        eprint("  las reglas de plan.schema.json o progress.schema.json.")
        eprint()
        eprint("  Acción: Revisa las capas de análisis. Puede que falten campos")
        eprint("  requeridos (objective >= 10 chars, result_state >= 10 chars,")
        eprint("  domain, impacted_artifacts.files, risks.curvature, etc.)")
        eprint()
        try:
            if os.path.exists(plan_path):
                os.remove(plan_path)
            if os.path.exists(progress_path):
                os.remove(progress_path)
        except OSError:
            pass
        return 2

    print("  ✅ plan.json + progress.json válidos contra schema")

    meta["status"] = "completed"
    meta["completed_at"] = _now_iso()
    write_json(_get_meta_path(chain_dir), meta)

    must_exist_count = len(plan["plan"]["goal"]["predicates"]["must_exist"])
    must_not_exist_count = len(plan["plan"]["goal"]["predicates"]["must_not_exist"])
    invariants_count = len(plan["plan"]["goal"]["predicates"]["invariants"])
    tasks_count = len(plan["plan"]["tasks"])

    print("=" * 70)
    print("✅ ANÁLISIS PROFUNDO COMPLETADO — SPRINT GENERADO")
    print("=" * 70)
    print(f"  Proyecto:    {meta.get('project_id', '?')}")
    print(f"  Goal:        {meta.get('goal', '')[:80]}")
    print(f"  Chain dir:   {chain_dir}")
    print()
    print("📊 PREDICATES GENERADOS:")
    print(f"  ➕ must_exist:      {must_exist_count}")
    print(f"  ➖ must_not_exist:  {must_not_exist_count}")
    print(f"  🔒 invariants:     {invariants_count}")
    print(f"  📦 Tareas:         {tasks_count}")
    print()
    print("📁 Archivos generados:")
    print(f"  📝 {plan_path}")
    print(f"  📝 {progress_path}")
    print(f"  📊 {compiled_path}")
    print()
    print("💡 Las 6 capas de análisis están registradas en .analysis/")
    print("   como referencia durante la implementación.")
    print("=" * 70)

    return 0


# ── STATUS ─────────────────────────────────────────────────────


def analysis_status(chain_dir: str) -> int:
    """Muestra el progreso del análisis. Retorna código de salida."""
    meta = load_json(_get_meta_path(chain_dir))
    if not meta:
        eprint(f"❌ No hay análisis en {chain_dir}")
        eprint("   Inicia uno con init_analysis(chain_dir, project_id, goal).")
        return 1

    completed = meta.get("completed_layers", [])
    current_layer = meta.get("current_layer", "layer-01")
    ready_finalize = meta.get("ready_to_finalize", False)

    print("=" * 70)
    print(f"📊 ANÁLISIS PROFUNDO — {meta.get('project_id', '?')}")
    print("=" * 70)
    print(f"  Goal:  {meta.get('goal', '')[:80]}")
    print(
        f"  Status: {'✅ COMPLETADO' if meta.get('status') == 'completed' else '🔄 EN PROGRESO'}"
    )
    print()

    total = len(_LAYER_ORDER)
    for i, layer_id in enumerate(_LAYER_ORDER):
        layer_def = _LAYERS.get(layer_id, {})
        name = layer_def.get("name", layer_id)
        completed_flag = "✅" if layer_id in completed else "⬜"
        active_flag = "▶️" if layer_id == current_layer and completed_flag == "⬜" else "  "
        print(f"  {active_flag}{completed_flag} Capa {i + 1}: {name}")

    print()
    print(f"  Capas completadas: {len(completed)}/{total}")

    for layer_id in completed:
        layer_data = load_json(_get_layer_path(chain_dir, layer_id))
        if layer_data:
            layer_def = _LAYERS.get(layer_id, {})
            name = layer_def.get("name", layer_id)
            counts = []
            for field in layer_def.get("output_summary_fields", []):
                items = layer_data.get(field, [])
                if items:
                    counts.append(f"{field}: {len(items)}")
            if counts:
                print(f"    [{name}] {' | '.join(counts)}")

    print()

    if ready_finalize:
        print("🎯 ACCIÓN:")
        print("  finalize_analysis(chain_dir)")
    elif meta.get("status") != "completed":
        print("▶️ PRÓXIMA ACCIÓN:")
        next_layer_id = current_layer
        print(
            f"  submit_layer(chain_dir, '{next_layer_id}', data)"
        )

    print("=" * 70)
    return 0


# ═══════════════════════════════════════════════════════════════
#  API pública
# ═══════════════════════════════════════════════════════════════

LAYERS = _LAYERS
LAYER_ORDER = _LAYER_ORDER

__all__ = [
    "LAYERS",
    "LAYER_ORDER",
    "init_analysis",
    "submit_layer",
    "compile_analysis",
    "finalize_analysis",
    "analysis_status",
    "validate_layer_data",
    "build_layer_description",
    "compute_checksum",
    "add_checksum",
    "verify_checksum",
]
