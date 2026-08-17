"""features/main/cognitive_registry.py — Schemas de índice por dominio (ADR-045).

Cada dominio define su propio schema de índice CognitiveGraph. El composition
root (container.py) registra los índices llamando a cg.register_index() con
estos schemas. CognitiveGraph (shared/core) no conoce nombres de dominio.

Antes (deuda): CognitiveGraph._register_ckg_index()/_register_delta_index()
hardcodeaban schemas en infraestructura. Ahora cada dominio provee el suyo.
"""
from __future__ import annotations

from typing import Any


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
        "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
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
        "storage": {"path": "data/delta.json.gz", "format": "json.gz"},
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
        "storage": {"path": "data/cpg.json.gz", "format": "json.gz"},
        "description": "CPG: Code Property Graph - estructura del codigo fuente",
    }


def trace_index_config() -> dict[str, Any]:
    """Schema del índice Trace (memoria de ejecucion) para CognitiveGraph."""
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
        "storage": {"path": "data/trace.json.gz", "format": "json.gz"},
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
