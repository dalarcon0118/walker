"""task_execution/tasks/kg_metadata.py — Metadata de tasks KG (registro declarativo).

Separa el catálogo de tools/descripciones de la lógica de negocio para que
kg.py sea un thin adapter (< 200 líneas).
"""
from __future__ import annotations

from features.main.task_execution.tasks.kg_helpers import (
    kg_agent_context,
    kg_multi,
    kg_schema,
    kg_search,
    documenting_knowledge,
)

_KG_HELP = {
    "protocol": (
        "PROTOCOLO: Antes de leer archivos del codigo fuente, consulta las memorias.\n"
        "Cada memoria responde una pregunta distinta. Usa kg_schema para descubrir\n"
        "los campos de cada indice, luego construye queries precisas."
    ),
    "indices": {
        "delta": "Tareas del sprint (campos: task_id, objective, domain, status, files). Busqueda full-text por texto.",
        "ckg": "Conocimiento ingenieril (campos: type, statement, scope, tags). Busqueda estructurada o full-text.",
        "cpg": "Estructura del codigo (campos: name, type, file, line). Busqueda estructurada por file/function/type.",
        "trace": "Historial de ejecucion (campos: event_type, tool, task_id, session_id). Busqueda estructurada o full-text.",
    },
    "ejemplos": [
        {
            "task": "kg_schema",
            "desc": "Descubrir todos los indices disponibles",
            "cmd": "walker <project>/kg_schema",
        },
        {
            "task": "kg_schema",
            "desc": "Ver schema de un indice (campos y tipos)",
            "cmd": 'walker <project>/kg_schema \'{"index":"delta"}\'',
        },
        {
            "task": "kg_search",
            "desc": "Busqueda full-text en ckg (conocimiento)",
            "cmd": 'walker <project>/kg_search \'{"text":"authentication"}\'',
        },
        {
            "task": "kg_search",
            "desc": "Busqueda full-text en un indice especifico (delta)",
            "cmd": 'walker <project>/kg_search \'{"_index":"delta","text":"authentication"}\'',
        },
        {
            "task": "kg_search",
            "desc": "Busqueda estructurada en cpg (codigo por archivo)",
            "cmd": 'walker <project>/kg_search \'{"_index":"cpg","file":"src/auth.py"}\'',
        },
        {
            "task": "kg_search",
            "desc": "Busqueda por tipo en ckg (conocimiento)",
            "cmd": 'walker <project>/kg_search \'{"type":"decision"}\'',
        },
        {
            "task": "kg_multi",
            "desc": "Query federada en 3 indices a la vez",
            "cmd": 'walker <project>/kg_multi \'{"delta":{"text":"auth"},"cpg":{"file":"src/auth.py"},"ckg":{"scope":"security"}}\'',
        },
    ],
}


def _kg_help(topic: str = "") -> dict:
    """Retorna ayuda sobre las memorias de CognitiveGraph."""
    return {
        "type": "kg_help",
        "topic": topic,
        "protocol": _KG_HELP["protocol"],
        "indices": _KG_HELP["indices"],
        "ejemplos": _KG_HELP["ejemplos"],
    }


KG_TASKS = [
    (
        "kg_schema",
        kg_schema,
        {
            "description": "Descubre indices disponibles o schema de un indice especifico",
            "usage": "walker <project>/kg_schema",
            "example": 'walker <project>/kg_schema \'{"index":"delta"}\'',
            "query_instructions": (
                "Sin body: lista todos los indices disponibles. "
                'Con body {"index":"delta"}: muestra los campos y tipos de ese indice.'
            ),
            "tags": ["kg", "cognitive-graph"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Índice del que se quiere el schema (delta, ckg, cpg, trace). Sin body devuelve lista de índices", "example": "delta"},
                },
                "required": [],
            },
        },
    ),
    (
        "kg_search",
        kg_search,
        {
            "description": "Busca en un indice de CognitiveGraph (delta, ckg, cpg, trace)",
            "usage": 'walker <project>/kg_search \'{"campo":"valor"}\'',
            "example": 'walker <project>/kg_search \'{"_index":"delta","text":"authentication"}\'',
            "query_instructions": (
                'Body es la query JSON. Usa {"text":"..."} para full-text. '
                'Usa {"campo":"valor"} para busqueda estructurada. '
                "Con _index eliges el indice (default ckg). Primero usa kg_schema para ver campos."
            ),
            "tags": ["kg", "cognitive-graph"],
        },
    ),
    (
        "documenting_knowledge",
        documenting_knowledge,
        {
            "description": "Almacena conocimiento en CKG (use case SaveToCkgUseCase). Acepta 'nodes' (workflow) o statement/label con ciclo de vida: con 'supersedes' (lista de statements o IDs) degrada a las decisiones anteriores reemplazadas.",
            "usage": 'walker <project>/documenting_knowledge \'{"type":"decision","statement":"...","supersedes":["<statement o id previo>"]}\'',
            "tags": ["kg", "cognitive-graph"],
        },
    ),
    (
        "kg_multi",
        kg_multi,
        {
            "description": "Query federada en multiples indices a la vez",
            "usage": '<walker <project>/kg_multi \'{"delta":{},"ckg":{}}\'',
            "example": 'walker <project>/kg_multi \'{"delta":{"text":"auth"},"cpg":{"file":"src/auth.py"}}\'',
            "query_instructions": (
                "Cada clave del body es un indice (delta, ckg, cpg, trace) y cada valor "
                "es la query para ese indice. Recibe resultados de todos en una sola respuesta."
            ),
            "tags": ["kg", "cognitive-graph"],
            "input_schema": {
                "type": "object",
                "description": "Cada clave del body es un índice (delta, ckg, cpg, trace); cada valor es la query para ese índice.",
                "examples": [{"delta": {"text": "auth"}, "ckg": {}}],
                "additionalProperties": {"type": "object"},
            },
        },
    ),
    (
        "agent_context",
        kg_agent_context,
        {
            "description": "Consulta consolidada delta+ckg+cpg+trace con cruce por archivo/task",
            "usage": 'walker <project>/agent_context \'{"delta":{},"ckg":{},"cpg":{},"trace":{}}\'',
            "example": 'walker <project>/agent_context \'{"delta":{"text":"auth"},"ckg":{"scope":"security"}}\'',
            "query_instructions": (
                "Cada clave del body es una query para ese indice. "
                "Devuelve un resumen por sección: sprint/tasks (delta), "
                "ADRs/convenciones (ckg), archivos/símbolos (cpg), "
                "eventos recientes (trace)."
            ),
            "tags": ["kg", "context", "agent"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Archivo/área a analizar (cruce de las 4 capas por archivo)."},
                    "task_id": {"type": "string", "description": "Spec/TASK del sprint (cruce delta×trace)."},
                    "text": {"type": "string", "description": "Búsqueda full-text de respaldo en CKG."},
                    "delta": {"type": "object", "description": "Filtros sobre el índice delta."},
                    "ckg": {"type": "object", "description": "Filtros sobre el índice CKG."},
                    "cpg": {"type": "object", "description": "Filtros sobre el índice CPG."},
                    "trace": {"type": "object", "description": "Filtros sobre el índice trace."},
                },
                "required": [],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "kg_agent_context"},
                    "project_id": {"type": "string"},
                    "summary": {"type": "object"},
                    "delta": {"type": "object"},
                    "ckg": {"type": "object"},
                    "cpg": {"type": "object"},
                    "trace": {"type": "object"},
                    "insights": {"type": "array"},
                    "next_actions": {"type": "array"},
                },
                "required": [],
            },
        },
    ),
]
