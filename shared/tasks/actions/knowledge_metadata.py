"""task_execution/tasks/kg_metadata.py — Metadata de tasks KG (registro declarativo).

Separa el catálogo de tools/descripciones de la lógica de negocio para que
kg.py sea un thin adapter (< 200 líneas).
"""
from __future__ import annotations

from shared.tasks.actions.knowledge_helpers import (
    kg_agent_context,
    search_memories,
    memory_schema,
    recall_knowledge,
    documenting_knowledge,
)

_KG_HELP = {
    "protocol": (
        "PROTOCOLO: Antes de leer archivos del codigo fuente, consulta las memorias.\n"
        "Cada memoria responde una pregunta distinta. Usa memory_schema para descubrir\n"
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
            "task": "memory_schema",
            "desc": "Descubrir todos los indices disponibles",
            "cmd": "walker <project>/memory_schema",
        },
        {
            "task": "memory_schema",
            "desc": "Ver schema de un indice (campos y tipos)",
            "cmd": 'walker <project>/memory_schema \'{"index":"delta"}\'',
        },
        {
            "task": "recall_knowledge",
            "desc": "Busqueda full-text en ckg (conocimiento)",
            "cmd": 'walker <project>/recall_knowledge \'{"text":"authentication"}\'',
        },
        {
            "task": "recall_knowledge",
            "desc": "Busqueda full-text en un indice especifico (delta)",
            "cmd": 'walker <project>/recall_knowledge \'{"_index":"delta","text":"authentication"}\'',
        },
        {
            "task": "recall_knowledge",
            "desc": "Busqueda estructurada en cpg (codigo por archivo)",
            "cmd": 'walker <project>/recall_knowledge \'{"_index":"cpg","file":"src/auth.py"}\'',
        },
        {
            "task": "recall_knowledge",
            "desc": "Busqueda por tipo en ckg (conocimiento)",
            "cmd": 'walker <project>/recall_knowledge \'{"type":"decision"}\'',
        },
        {
            "task": "search_memories",
            "desc": "Query federada en 3 indices a la vez",
            "cmd": 'walker <project>/search_memories \'{"delta":{"text":"auth"},"cpg":{"file":"src/auth.py"},"ckg":{"scope":"security"}}\'',
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
        "memory_schema",
        memory_schema,
        {
            "description": "Descubre indices disponibles o schema de un indice especifico",
            "usage": "walker <project>/memory_schema",
            "example": 'walker <project>/memory_schema \'{"index":"delta"}\'',
            "query_instructions": (
                "Sin body: lista todos los indices disponibles. "
                'Con body {"index":"delta"}: muestra los campos y tipos de ese indice.'
            ),
            "tags": ["knowledge", "cognitive-graph"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Indice del que se quiere el schema (delta, ckg, cpg, trace). Sin body devuelve lista de indices", "example": "delta"},
                },
                "required": [],
            },
        },
    ),
    (
        "recall_knowledge",
        recall_knowledge,
        {
            "description": "Busca en un indice de CognitiveGraph (delta, ckg, cpg, trace)",
            "usage": 'walker <project>/recall_knowledge \'{"campo":"valor"}\'',
            "example": 'walker <project>/recall_knowledge \'{"_index":"delta","text":"authentication"}\'',
            "query_instructions": (
                'Body es la query JSON. Usa {"text":"..."} para full-text. '
                'Usa {"campo":"valor"} para busqueda estructurada. '
                "Con _index eliges el indice (default ckg). Primero usa memory_schema para ver campos."
            ),
            "tags": ["knowledge", "cognitive-graph"],
        },
    ),
    (
        "documenting_knowledge",
        documenting_knowledge,
        {
            "description": "Almacena conocimiento en CKG (use case SaveToCkgUseCase). Acepta 'nodes' (workflow) o statement/label con ciclo de vida: con 'supersedes' (lista de statements o IDs) degrada a las decisiones anteriores reemplazadas.",
            "usage": 'walker <project>/documenting_knowledge \'{"type":"decision","statement":"...","supersedes":["<statement o id previo>"]}\'',
            "tags": ["knowledge", "cognitive-graph"],
        },
    ),
    (
        "search_memories",
        search_memories,
        {
            "description": "Query federada en multiples indices a la vez",
            "usage": '<walker <project>/search_memories \'{"delta":{},"ckg":{}}\'',
            "example": 'walker <project>/search_memories \'{"delta":{"text":"auth"},"cpg":{"file":"src/auth.py"}}\'',
            "query_instructions": (
                "Cada clave del body es un indice (delta, ckg, cpg, trace) y cada valor "
                "es la query para ese indice. Recibe resultados de todos en una sola respuesta."
            ),
            "tags": ["knowledge", "cognitive-graph"],
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
                "Devuelve un resumen por seccion: sprint/tasks (delta), "
                "ADRs/convenciones (ckg), archivos/simbolos (cpg), "
                "eventos recientes (trace)."
            ),
            "tags": ["knowledge", "context", "agent"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Archivo/area a analizar (cruce de las 4 capas por archivo)."},
                    "task_id": {"type": "string", "description": "Spec/TASK del sprint (cruce delta×trace)."},
                    "text": {"type": "string", "description": "Busqueda full-text de respaldo en CKG."},
                    "delta": {"type": "object", "description": "Filtros sobre el indice delta."},
                    "ckg": {"type": "object", "description": "Filtros sobre el indice CKG."},
                    "cpg": {"type": "object", "description": "Filtros sobre el indice CPG."},
                    "trace": {"type": "object", "description": "Filtros sobre el indice trace."},
                },
                "required": [],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "agent_context"},
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
