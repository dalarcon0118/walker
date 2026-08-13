"""engine/context/contracts.py — DTOs de contexto de entrada de los 9 providers (F2 SPEC-043).

ADR-043 §4.1: DTOs de contrato (no ViewModels). El contexto que el agente
recibe en cada step atraviesa 9 providers; cada uno produce una CAPA con una
forma estructurada. Estos DTOs son el SSOT de ESA forma:

  - `to_schema()` → JSON Schema de la capa que el agente recibe (el prompt
    render incluye el esquema de contexto — RF-B1.2 del SPEC-043).
  - `from_dict()` → valida/coerce datos de la capa si se necesita estructurar.

NO se reemplaza el content en texto de los prompts: los providers siguen
entregando su texto formateado (compatible con los 47 templates); los DTOs
declaran la FORMA tipada de la capa (context_schema del workflow), que el
agente usa para planear y para validar su lectura.
"""

from __future__ import annotations

from typing import Any


# ── DTOs por provider ───────────────────────────────────────────────────────
# Cada clase describe la forma de la capa que produce el provider homónimo.
# Los campos reflejan el content real formateado en cada provider.

TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa task_spec/state — spec actual, posición en el workflow, gates.",
    "properties": {
        "task_id": {"type": "string"},
        "spec": {"type": "object"},
        "status": {"type": "string"},
        "node": {"type": "string"},
        "sub_state": {"type": "string"},
        "required_tools": {"type": "array", "items": {"type": "string"}},
    },
}

DIFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa diff — cambios git (archivos, líneas, funciones).",
    "properties": {
        "base_ref": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}},
        "functions_changed": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
}

TEST_RESULTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa test_results — salida del run de tests (pytest).",
    "properties": {
        "exit_code": {"type": "integer"},
        "total": {"type": "integer"},
        "passed": {"type": "integer"},
        "failed": {"type": "integer"},
        "errors": {"type": "integer"},
        "failures": {"type": "array", "items": {"type": "string"}},
    },
}

TRACE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa trace — historial de eventos de la sesión por tool/task.",
    "properties": {
        "max_events": {"type": "integer"},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "event": {"type": "string"},
                    "task_id": {"type": "string"},
                    "timestamp": {"type": "string"},
                },
            },
        },
    },
}

REFLECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa reflection — paquete de evidencia para REFINE (hechos, no opiniones).",
    "properties": {
        "source_node": {"type": "string"},
        "attempt": {"type": "integer"},
        "past_attempts": {"type": "array"},
        "failed_gates": {"type": "array"},
        "hint": {"type": "string"},
        "last_error": {"type": "string"},
        "last_error_strategy": {"type": "string"},
        "last_reason": {"type": "string"},
    },
}

CKG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa ckg — conocimiento del proyecto (convenciones, decisiones, patrones).",
    "properties": {
        "total": {"type": "integer"},
        "max_nodes": {"type": "integer"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "statement": {"type": "string"},
                    "scope": {"type": "string"},
                },
            },
        },
    },
}

CPG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa cpg — código estructurado (funciones, clases, calls, imports).",
    "properties": {
        "max_results": {"type": "integer"},
        "files": {"type": "array", "items": {"type": "string"}},
        "symbols": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                },
            },
        },
    },
}

BUSINESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa business — capa de negocio derivada del CPG (flujos y patrones).",
    "properties": {
        "pure_functions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "file": {"type": "string"},
                    "signature": {"type": "string"},
                },
            },
        },
        "business_flows": {
            "type": "array",
            "items": {"type": "object", "properties": {"entrypoint": {"type": "string"}, "chain": {"type": "array"}}},
        },
        "architecture_patterns": {
            "type": "array",
            "items": {"type": "object", "properties": {"pattern": {"type": "string"}, "file": {"type": "string"}}},
        },
    },
}

DELTA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa delta — specs del sprint desde el DeltaGraph (SSOT de backlog).",
    "properties": {
        "max_results": {"type": "integer"},
        "specs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "objective": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
        },
    },
}

PROJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Capa project — metadata del proyecto (lenguajes, convenciones, arquitectura).",
    "properties": {
        "project_id": {"type": "string"},
        "language": {"type": "string"},
        "framework": {"type": "string"},
        "naming_convention": {"type": "string"},
        "code_extensions": {"type": "array", "items": {"type": "string"}},
    },
}


# ── Registro SSOT: provider → JSON Schema de su capa ────────────────────────
#
# Clave = nombre con el que el nodo declara el provider en context.sources.
# Este mapa es la fuente que el workflow expone como `context_schema`
# (el agente ve la forma de cada capa antes de que el engine la renderice).

CONTEXT_SCHEMAS: dict[str, dict[str, Any]] = {
    "business": BUSINESS_SCHEMA,
    "ckg": CKG_SCHEMA,
    "cpg": CPG_SCHEMA,
    "delta": DELTA_SCHEMA,
    "diff": DIFF_SCHEMA,
    "project": PROJECT_SCHEMA,
    "reflection": REFLECTION_SCHEMA,
    "test_results": TEST_RESULTS_SCHEMA,
    "trace": TRACE_SCHEMA,
}


def context_schema(provider_name: str) -> dict[str, Any]:
    """Devuelve el JSON Schema del contexto de un provider (SSOT).

    Fail-open: si el provider no está registrado, devuelve {} (igual que
    ToolDefinition.output_schema default).
    """
    return CONTEXT_SCHEMAS.get(provider_name, {})


def all_context_schemas() -> dict[str, dict[str, Any]]:
    """Mapa provider → schema (para paneles/help/tauler coherentes)."""
    return dict(CONTEXT_SCHEMAS)