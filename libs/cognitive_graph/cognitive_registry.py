"""features/main/cognitive_registry.py — Schemas de índice por dominio (ADR-045).

Cada dominio define su propio schema de índice CognitiveGraph. El composition
root (container.py) registra los índices llamando a cg.register_index() con
estos schemas. CognitiveGraph (shared/core) no conoce nombres de dominio.

Antes (deuda): CognitiveGraph._register_ckg_index()/_register_delta_index()
hardcodeaban schemas en infraestructura. Ahora cada dominio provee el suyo.

ADR-WALKER-061 (F3): el schema NO lleva 'storage'. El filename se deriva del
nombre del índice ({name}.json.gz); el directorio base lo resuelve la
MemoryConnection (uri file://...) en el composition root.
"""
from __future__ import annotations

import os
from typing import Any


def memory_data_dir(chain_dir: str) -> str:
    """Raíz canónica de memorias (data/) para un chain_dir (ADR-WALKER-054 D4).

    Única fuente de verdad: .cognitive/projects/<pid>/data (resuelto por
    ProjectService.resolve_paths). Si chain_dir ya es la raíz data, se usa
    tal cual; si es el dir del proyecto u otro, se deriva. Fallback defensivo
    (tests/proyectos sin manifest): chain_dir o chain_dir/data.
    """
    if not chain_dir:
        return ""
    try:
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        if paths.data_dir:
            return str(paths.data_dir)
    except Exception:
        pass
    if os.path.isfile(os.path.join(chain_dir, "project.json")):
        return chain_dir
    data_dir = os.path.join(chain_dir, "data")
    return data_dir if os.path.isdir(data_dir) else chain_dir


def ckg_index_config() -> dict[str, Any]:
    """Schema del índice CKG (Knowledge Graph) para CognitiveGraph."""
    return {
        "fields": {
            "type": "type",
            "scope": "scope",
            "label": "statement",
            "task": "metadata.task_id",
            "description": "rationale",
            "tag": "tags",
        },
        "types": {
            "decision": {"description": "Decision de arquitectura"},
            "refactor": {"description": "Refactorizacion aplicada"},
            "convention": {"description": "Convencion de estilo"},
        },
        "description": "CKG: Knowledge Graph - decisiones, convenciones, patrones",
        "search": {
            "type": "fulltext",
            "fields": ["statement", "rationale", "tags"],
        },
    }


def delta_index_config() -> dict[str, Any]:
    """Schema del índice Delta (specs y springs) para CognitiveGraph."""
    return {
        "fields": {
            "task": "spec_id",
            "spec": "spec_id",
            "spring": "spring_id",
            "domain": "domain",
            "type": "type",
        },
        "types": {"spec": {}, "spring": {}},
        "description": "DeltaGraph: specs y springs del proyecto",
        "search": {
            "type": "fulltext",
            "fields": ["objective", "title", "description", "domain", "result_state"],
        },
    }


def cpg_index_config() -> dict[str, Any]:
    """Schema del índice CPG (Code Property Graph) para CognitiveGraph."""
    return {
        "fields": {"function": "name", "file": "path", "type": "type"},
        "types": {"function": {}, "class": {}, "file": {}, "module": {}},
        # FIX FTS sordo: sin este bloque has_fulltext()=False → _token_index
        # None → toda búsqueda textual devolvía 0 hits aunque los docs
        # existieran (el CPG llevaba años mudo para recall_knowledge).
        "search": {
            "type": "fulltext",
            "fields": ["name", "path", "signature", "kind"],
        },
        "description": "CPG: Code Property Graph - estructura del codigo fuente",
    }


def trace_index_config() -> dict[str, Any]:
    """Schema del índice Trace (memoria de ejecucion) para CognitiveGraph.

    ADR-WALKER-055 (arquetipo "índice consultable"): el trace se consulta
    ESTRUCTURADO (type/tool/session/task — índices secundarios O(1)), NO
    fulltext: con 56k docs el TokenIndex serializado (snippets por entrada)
    hace la migración G3 inviable (minutos). El fulltext fue probado y
    revertido por ese motivo. El type index funciona gracias al fix de
    `_index_doc` (resuelve type por el schema → event_type).
    """
    return {
        "fields": {
            "task": "task_id",
            "tool": "tool",
            "type": "event_type",
            "agent": "agent_id",
            "session": "session_id",
            "sprint": "sprint_id",
            "node": "node",
            "sub_state": "sub_state",
            "source": "source",
            "file": "files",
        },
        "types": {
            "workflow_state": {},
            "task_execute": {},
            "agent_message": {},
            "tool_call": {},
            "file_impact": {},
            "kg_query": {},
        },
        "description": "TraceGraph: memoria de ejecucion (workflow + agente + tools)",
    }


def register_cognitive_indexes(cg: Any) -> None:
    """Registra todos los índices de dominio en una instancia de CognitiveGraph.

    ADR-045: CognitiveGraph es infraestructura genérica; los schemas viven
    en el composition root (features/main), no en shared/core.
    """
    cg.register_index("ckg", ckg_index_config())
    cg.register_index("cpg", cpg_index_config())
    cg.register_index("delta", delta_index_config())
    cg.register_index("trace", trace_index_config())
