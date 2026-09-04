"""fast_mcp.phases.phase2_memories — Fase 2: memorias que leen datos y escriben en prepare.

Cubre las tools de MEMORIA que el agente usa para leer el estado del proyecto
(CKG/CPG/Delta) y escribir conocimiento resultante durante el flujo de prepare:
indexado, kg_*, code_context, extract_knowledge, etc.

Tools del grupo (verificado contra mcp/ task actions):
  - indexación:  build_code_index, index_repo, update_incremental_code
  - conocimiento: extract_knowledge, knowledge_query, knowledge_metadata,
                  knowledge_agent_context, session_knowledge
  - análisis de código: cpg_* / code_context, inspect_codebase, read_code, code_search
  - memorias: memory_schema, recall_knowledge, documenting_knowledge,
              search_memories, agent_context

Los runners llegan YA CABLEADOS desde composition (regla de coherencia §3bis 2/3).
Esta fase declara nombres/schemas y registra — sin tocar features.
Sin imports de `mcp/` (ADR-WALKER-071 §0 #4/#5).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from fast_mcp import composition
from fast_mcp.registration import register_domain_tool

logger = logging.getLogger(__name__)

# Names of the memory tools (Phase 2).
_FASE2_TOOLS = (
    # Indexación CPG
    "build_code_index",
    "index_repo",
    "update_incremental_code",
    # Conocimiento CKG
    "extract_knowledge",
    "knowledge_query",
    "memory_schema",
    "recall_knowledge",
    "documenting_knowledge",
    "search_memories",
    "agent_context",
    # Análisis de código
    "code_context",
    "inspect_codebase",
    "read_code",
    "code_search",
)

# SSOT local del inputSchema (contrato legacy, declarativo — no inferido).
_INPUT_SCHEMAS: dict[str, dict] = {
    "build_code_index": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID override."},
        },
    },
    "index_repo": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL of the remote repo to index (required)."},
            "name": {"type": "string", "description": "Repo name (default: last segment of url)."},
            "branch": {"type": "string", "description": "Branch to index (default main)."},
        },
        "required": ["url"],
    },
    "update_incremental_code": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID override (optional)."},
        },
    },
    "extract_knowledge": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (alternative to chain_dir)."},
            "scope": {"type": "string", "description": "Analysis scope: all | functions | classes | files (default all)."},
        },
    },
    "knowledge_query": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "action": {"type": "string", "description": "Sub-command: search, save, active, supersede, context (default search)."},
            "query": {"type": "string", "description": "Full-text search (action=search)."},
            "text": {"type": "string", "description": "Alias of query (action=search)."},
            "max_results": {"type": "integer", "description": "Search result limit (default 20)."},
            "nodes": {"type": "array", "description": "Knowledge nodes to save (action=save)."},
            "type": {"type": "string", "description": "Type filter (action=active)."},
            "files": {"type": "array", "description": "Files to extract context (action=context)."},
            "old_id": {"type": "string", "description": "Decision ID to replace (action=supersede)."},
            "new_id": {"type": "string", "description": "New decision ID (action=supersede)."},
            "statement": {"type": "string", "description": "Decision text/reason (action=supersede)."},
        },
    },
    "memory_schema": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "index": {"type": "string", "description": "Index to get schema from (delta, ckg, cpg, trace). Without body returns list of indexes."},
        },
    },
    "recall_knowledge": {
        "type": "object",
        "description": "Each key is an index query (delta, ckg, cpg, trace). Requires project_id.",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "_index": {"type": "string", "description": "Index to search (default ckg)."},
            "text": {"type": "string", "description": "Full-text search query."},
        },
    },
    "documenting_knowledge": {
        "type": "object",
        "description": "Knowledge to save: nodes (workflow) or statement/label with lifecycle. Requires project_id.",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "nodes": {"type": "array", "description": "Knowledge nodes to save."},
            "statement": {"type": "string", "description": "Knowledge statement."},
            "type": {"type": "string", "description": "Knowledge type."},
            "supersedes": {"type": "array", "description": "Statements or IDs this replaces."},
        },
    },
    "search_memories": {
        "type": "object",
        "description": "Federated query across multiple indexes. Requires project_id.",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "delta": {"type": "object", "description": "Delta index query."},
            "ckg": {"type": "object", "description": "CKG index query."},
            "cpg": {"type": "object", "description": "CPG index query."},
            "trace": {"type": "object", "description": "Trace index query."},
        },
    },
    "agent_context": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "file": {"type": "string", "description": "File/area to analyze (cross-layer by file)."},
            "task_id": {"type": "string", "description": "Spec/TASK of the sprint (delta×trace cross)."},
            "text": {"type": "string", "description": "Full-text fallback search in CKG."},
            "delta": {"type": "object", "description": "Filters on the delta index."},
            "ckg": {"type": "object", "description": "Filters on the CKG index."},
            "cpg": {"type": "object", "description": "Filters on the CPG index."},
            "trace": {"type": "object", "description": "Filters on the trace index."},
        },
    },
    "code_context": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "file": {"type": "string", "description": "File path to contextualize (relative to project_root)."},
            "query": {"type": "string", "description": "Structured search by symbol, function or class name."},
            "depth": {"type": "string", "description": "Depth: shallow | normal | deep (default normal)."},
        },
    },
    "inspect_codebase": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "scope": {"type": "string", "description": "Path or scope to inspect."},
            "action": {"type": "string", "description": "Action: business, dead_code, security, review, metrics, build, update."},
            "detect": {"type": "string", "description": "Detection mode: dead_code, security."},
            "depth": {"type": "string", "description": "Analysis depth: business."},
            "review": {"type": "boolean", "description": "Enable code review."},
            "metrics": {"type": "boolean", "description": "Enable metrics collection."},
            "build": {"type": "boolean", "description": "Build CPG index."},
        },
    },
    "read_code": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "File path to read (relative to project_root or absolute)."},
            "project_id": {"type": "string", "description": "Project ID (optional if context resolves it)."},
        },
        "required": ["file"],
    },
    "code_search": {
        "type": "object",
        "properties": {
            "project_id": {"type": "string", "description": "Project ID (REQUIRED — fail-fast, no se infiere del CWD)."},
            "query": {"type": "string", "description": "Full-text search query (required)."},
            "max_results": {"type": "integer", "description": "Result limit (default 20)."},
        },
        "required": ["query"],
    },
}

_DESCRIPTIONS = {
    "build_code_index": "Full CPG rebuild from scratch. Use for initial setup or when index is corrupted.",
    "index_repo": "Index an external repo as reference in CKG.",
    "update_incremental_code": "Incremental CPG update via watcher: re-indexes only changed files.",
    "extract_knowledge": "Extract knowledge from code via CPG to populate CKG (no sprint needed).",
    "knowledge_query": "Query project knowledge: search, save, activate, supersede or extract context.",
    "memory_schema": "Discover available indexes or schema of a specific index.",
    "recall_knowledge": "Search in a CognitiveGraph index (delta, ckg, cpg, trace).",
    "documenting_knowledge": "Store knowledge in CKG (with supersedes lifecycle).",
    "search_memories": "Federated query across multiple indexes at once.",
    "agent_context": "Consolidated delta+ckg+cpg+trace query with cross-references.",
    "code_context": "Structured code context: symbols, calls, imports and relations by file or search.",
    "inspect_codebase": "Inspect codebase: architecture, dead code, security, metrics or CPG index.",
    "read_code": "Read a project file's content by path.",
    "code_search": "Full-text search in CKG over indexed code and knowledge.",
}


def install(server: FastMCP) -> None:
    """Register the Phase 2 memory tools.

    Runners arrive ALREADY WIRED from composition (coherence rule §3bis 2/3):
    this phase declares names/schemas and registers — without touching features.
    """
    for name in _FASE2_TOOLS:
        register_domain_tool(
            server,
            name=name,
            runner=composition.memory_tool_runner(name),
            description=_DESCRIPTIONS[name],
            input_schema=_INPUT_SCHEMAS[name],
        )
    logger.info("fast_mcp fase2: %s registradas (memorias)", ", ".join(_FASE2_TOOLS))
