"""mcp_adapter.py — Input adapter: walker as an MCP server.

CLEAN ARCHITECTURE: input adapter (driver) at the same level as the REST
router. Contains NO business logic: it translates MCP tools to REST paths
and delegates to the Router (SSOT of dispatch).

GIT-LIKE BEHAVIOR: like git, the walker operates PER-DIRECTORY. Each
project has its own .cognitive/ (like .git/). The MCP resolves the project
from the process CWD (os.getcwd()) via ProjectService.resolve_from_registry()
— exactly like git finds .git/ walking upward. project_id is OPTIONAL: if
omitted, the current project is detected.

```
  MCP client (agent)              <- tools in context (push)
       | JSON-RPC stdio
       v
  [mcp_adapter]  <- adapter (translates tool -> REST path; resolves CWD)
       |
       v
  [Router]  <- SSOT of dispatch (same pipeline as the CLI)
       |
       v
  [handlers] -> [use cases] -> [domain]
```
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime


# ── Serializador JSON robusto ──────────────────────────────────────────────
# El workflow devuelve objetos tipados (StepResult, WorkflowState, SubStateData,
# TaskResult, datetimes, etc.) dentro de los dicts de resultado. json.dumps
# falla con 'Object of type X is not JSON serializable'. Este serializer
# convierte recursivamente cualquier objeto con __dict__/to_dict()/dataclasses
# a una representacion JSON-safe, de modo que el MCP NUNCA rompa por
# serializacion (y el estado del workflow ya avanzo en disco de todos modos).


def _json_safe(value):
    """Convierte value en una representacion JSON-serializable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    # Objetos con to_dict() (StepResult, WorkflowState, etc.)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _json_safe(to_dict())
        except Exception:
            pass
    # Dataclasses y objetos con __dict__
    dict_attr = getattr(value, "__dict__", None)
    if isinstance(dict_attr, dict):
        return _json_safe(dict_attr)
    return str(value)


def dumps(obj) -> str:
    """json.dumps con fallback seguro (nunca lanza por serializacion)."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=_json_safe)
    except (TypeError, ValueError):
        return json.dumps(_json_safe(obj), ensure_ascii=False)


# ── Tools catalog (metadata; dispatch goes through the Router) ─────────────

_TOOLS = [
    {
        "name": "prepare",
        "description": "Initialize a project at a path (creates .cognitive/ + registers in data/projects.json). Git init of the walker. If no path given, uses CWD",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Project path (optional; default: CWD)",
                },
            },
        },
    },
    {
        "name": "list",
        "description": "List projects managed by walker (data/projects.json)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "status",
        "description": "Project workflow state: node, sub_state, instructions, gates, GPS progress. Detects project from CWD if no project_id given",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (optional; detected from CWD)",
                },
            },
        },
    },
    {
        "name": "submit",
        "description": "Send data to the current sub-state. ADVANCES the workflow. Body: node fields or intent for XOR",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "body": {"type": "object"},
            },
            "required": ["body"],
        },
    },
    {
        "name": "transition",
        "description": "Transition through a gate (admin/emergency only; the agent advances with submit)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "gate": {"type": "string"},
            },
            "required": ["gate"],
        },
    },
    {
        "name": "kg_search",
        "description": "Search project memory (CPG code, CKG knowledge, Delta tasks, Trace events). QUERY before reading code files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID (auto-detected from CWD if omitted)"},
                "query": {
                    "type": "object",
                    "description": "Query filters. Supports structured + fulltext combined.",
                    "properties": {
                        "text": {"type": "string", "description": "Fulltext search across all fulltext fields. Use this for keyword search. Can be string or {\"query\":\"...\",\"fields\":[\"rationale\"]} for field-scoped"},
                        "_index": {"type": "string", "description": "Which index to search: ckg (knowledge), cpg (code), delta (tasks/sprints), trace (execution history). Default: ckg"},
                        "type": {"type": "string", "description": "Filter by type. CKG: decision|convention|refactor|pattern|rule. CPG: file|class|function|method. Delta: spring|spec"},
                        "file": {"type": "string", "description": "Filter by file path (substring match). CPG: 'features/delta_tracking'"},
                        "scope": {"type": "string", "description": "CKG scope filter: architecture|security|infrastructure|testing"},
                        "status": {"type": "string", "description": "Delta spec status: draft|specified|in_progress|completed"},
                        "size": {"type": "integer", "description": "Max results to return (default 50)"},
                        "_from": {"type": "integer", "description": "Pagination offset (0-based)"},
                        "text_fields": {"type": "array", "items": {"type": "string"}, "description": "Limit fulltext to specific fields (E1: field-scoped)"},
                    },
                },
            },
            "required": ["query"],
        },
        "outputSchema": {
            "description": "Search result with hits and schema metadata. hits.hits[] contains {_id, _index, _source: {field values}}",
            "type": "object",
            "properties": {
                "took": {"type": "integer", "description": "Query time in ms"},
                "hits": {
                    "type": "object",
                    "properties": {
                        "total": {"type": "integer", "description": "Total matching docs"},
                        "hits": {"type": "array", "description": "Result documents"},
                        "from": {"type": "integer", "description": "Pagination offset used"},
                        "size": {"type": "integer", "description": "Page size used"},
                    },
                },
                "schema": {"type": "object", "description": "Index schema (fields, types, total_docs)"},
            },
        },
    },
    {
        "name": "kg_multi",
        "description": "Federated query across multiple indices at once. Query CPG+CKG+Delta in one call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "layers": {
                    "type": "object",
                    "description": '{"ckg": {"text":"auth"}, "cpg": {"file":"src/auth.py"}, "delta": {"text":"login"}} — one query per index',
                },
            },
            "required": ["layers"],
        },
    },
    {
        "name": "cpg_business",
        "description": "CPG business layer: reusable pure functions, business flows, architecture patterns",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "file": {"type": "string"},
            },
        },
    },
    {
        "name": "cpg_unused_functions",
        "description": "Detect unused functions via CPG (dead code). Public task — works on the project CPG, no workflow needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
        },
    },
    {
        "name": "project_metrics",
        "description": "Compute project quality metrics from CPG (dead_code, cyclo, security, per-file). Public task — works on the project CPG, no workflow needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
        },
    },
    {
        "name": "analyze_code",
        "description": "CODE ANALYSIS (mandatory before touching code): query the project CPG for an area/file — symbols with signatures, imports, and business layer (pure functions, flows, patterns). Source of truth for the code. Do NOT read files directly.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "area": {
                    "type": "string",
                    "description": "File/area substring (e.g. 'win-win', 'domain.ts')",
                },
            },
            "required": ["area"],
        },
    },
    {
        "name": "kg_store",
        "description": "Register a knowledge/decision/convention node in the project CKG. Use after making architecture decisions or discovering patterns.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID (auto-detected)"},
                "body": {
                    "type": "object",
                    "description": "Knowledge node to register",
                    "required": ["type", "statement"],
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Node type: decision (architecture decision), convention (code style/pattern), refactor (applied refactoring), pattern (design pattern), rule (technical rule)",
                            "enum": ["decision", "convention", "refactor", "pattern", "rule", "component"],
                        },
                        "statement": {"type": "string", "description": "The knowledge statement. For decisions: 'Decision: <what>'. For conventions: 'Convention: <rule>'. REQUIRED"},
                        "rationale": {"type": "string", "description": "Why this decision/convention was made (context, tradeoffs)"},
                        "scope": {"type": "string", "description": "Domain scope: architecture, security, infrastructure, testing, code_analysis, delta_tracking, cognitive_knowledge"},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for filtering: ['performance', 'cache', 'redis']"},
                        "metadata": {
                            "type": "object",
                            "description": "Extra metadata: {task_id: 'TASK-042', severity: 'high', status: 'active'}",
                        },
                        "supersedes": {"type": "array", "items": {"type": "string"}, "description": "IDs or statements of previous decisions this replaces"},
                    },
                },
            },
            "required": ["body"],
        },
    },
    {
        "name": "help",
        "description": "Walker protocol and available tasks",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _public_task_tools() -> list[dict]:
    """Genera tools MCP para las tasks PUBLICAS con input schemas ricos.

    Las tasks frecuentes (kg_schema, kg_extract, etc.) reciben schemas
    enriquecidos para que el agente sepa exactamente que enviar y recibir.
    """
    # Schemas enriquecidos por task (evita que el agente adivine formatos)
    _RICH_SCHEMAS: dict[str, dict] = {
        "kg_schema": {
            "description": "Discover available memory indices and their fields/types. Use BEFORE kg_search to know what to query. No body needed.",
            "body_desc": 'Optional: {"index":"ckg"} to see one index. Omit for all indices.',
        },
        "kg_extract": {
            "description": "Extract knowledge from the CKG using a query. Returns relevant facts, decisions, and patterns.",
            "body_desc": '{"query":{"text":"authentication","type":"decision"},"max_results":20}',
        },
        "kg_research": {
            "description": "Deep research across multiple sources (CPG + CKG). Combines code structure with knowledge facts.",
            "body_desc": '{"topic":"authentication flow","depth":2,"max_results":10}',
        },
        "kg_index_repo": {
            "description": "Index a code repository into the CPG. Builds code property graph.",
            "body_desc": '{"repo_path":"/path/to/repo"} or omit for current project.',
        },
        "cpg_business": {
            "description": "Analyze business layer: pure functions, architecture patterns, flows, metrics from CPG.",
            "body_desc": '{"file":"features/auth"} to scope analysis to a directory.',
        },
        "cpg_unused_functions": {
            "description": "Find unused functions in the CPG (dead code detection).",
            "body_desc": 'Omit for all files, or {"file":"features/auth"} for directory.',
        },
        "cpg_dfg_scan": {
            "description": "Scan for data flow issues: hardcoded secrets, taint paths, uninitialized variables.",
            "body_desc": 'Omit for full scan, or {"paths":["src/auth.py"]} for specific files.',
        },
        "project_metrics": {
            "description": "Get project metrics: code size, complexity, test coverage, quality gates.",
            "body_desc": "No body needed.",
        },
        "build_initial_ckg": {
            "description": "Build the initial CKG from the project codebase. Run once when starting a new project.",
            "body_desc": "No body needed.",
        },
        "import_legacy_tasks": {
            "description": "Import legacy sprint tasks into the DeltaGraph.",
            "body_desc": '{"path":"data/delta/sprint-id"} — path to sprint directory.',
        },
        "import_legacy_history": {
            "description": "Import legacy sprint history (completed sprints) into the DeltaGraph.",
            "body_desc": '{"path":"data/delta"} — path to delta directory.',
        },
        "detect_legacy": {
            "description": "Detect legacy sprints that have not been imported into the DeltaGraph.",
            "body_desc": "No body needed — auto-detects from project.",
        },
        "map_legacy_workflow": {
            "description": "Map legacy workflow state to new format.",
            "body_desc": '{"sprint_id":"session-20260731"}',
        },
        "capture_task": {
            "description": "Capture a task from context and add it as a spec to the active sprint.",
            "body_desc": '{"objective":"Add login endpoint","domain":"authentication","files":["src/auth.py"]}',
        },
        "trace_push": {
            "description": "Push a trace event to the execution history (for debugging/monitoring).",
            "body_desc": '{"event_type":"tool_call","tool":"Write","files":["src/auth.py"]}',
        },
        "scan_code_and_build_cpg": {
            "description": "Full rebuild of the CPG from source code. Use after major code changes or first setup.",
            "body_desc": "No body needed.",
        },
        "update_project": {
            "description": "Update project metadata (name, code_paths, etc).",
            "body_desc": '{"name":"My Project","code_paths":["src"]}',
        },
        "resolve_project_sprint": {
            "description": "Resolve the current project and sprint ID from context.",
            "body_desc": "No body needed — auto-detects from CWD or chain_dir.",
        },
        "list_projects": {
            "description": "List all registered projects in the walker.",
            "body_desc": "No body needed.",
        },
        "cpg_index_ref": {
            "description": "Index a reference library (e.g., framework docs) as a CPG reference index.",
            "body_desc": '{"name":"expressjs","path":"/path/to/docs"}',
        },
        "analyze_code": {
            "description": "Analyze code using the CPG for deep insights.",
            "body_desc": '{"query":"auth","depth":"deep"}',
        },
        "query": {
            "description": "Generic query across memories.",
            "body_desc": '{"text":"authentication","indices":["ckg","cpg"]}',
        },
    }

    try:
        from features.engine.application.workflow_api import bootstrap as _bs
        from features.engine.infrastructure.task_registry import InMemoryTaskRegistry
        from features.engine.infrastructure.trace_log import TraceLogger

        registry, _se = _bs(InMemoryTaskRegistry(tracer=TraceLogger()))
        public_tasks = registry.list_tasks_with_meta(scope="public") or []
    except Exception:
        return []

    tools = []
    for t in public_tasks:
        name = t.get("name", "")
        if not name:
            continue
        rich = _RICH_SCHEMAS.get(name, {})
        desc = rich.get("description") or t.get("description") or f"Public walker task: {name}"
        body_desc = rich.get("body_desc", "Optional JSON body for the task")
        tools.append({
            "name": name,
            "description": f"{desc} (public task — works on the project, no workflow needed)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project ID (auto-detected from CWD if omitted)"},
                    "body": {
                        "type": "object",
                        "description": body_desc,
                    },
                },
            },
        })
    return tools


# Catalog final = tools fijas (workflow) + tasks publicas (registro).
# Se deduplican por nombre: si una task publica coincide con una tool fija
# (ej: cpg_business, kg_search), gana la definicion fija (mas completa).
_TOOLS_BASE = _TOOLS
_TOOLS = _TOOLS_BASE + [
    t for t in _public_task_tools() if t["name"] not in {x["name"] for x in _TOOLS_BASE}
]

_RESOURCES = [
    {
        "uri": "walker://status/{project_id}",
        "name": "Sprint Status",
        "description": "Workflow snapshot: where you are, instructions, gates, GPS progress",
        "mimeType": "application/json",
    },
    {
        "uri": "walker://memory/{project_id}",
        "name": "Project Memory",
        "description": "CKG + business layer: decisions, conventions, reusable functions",
        "mimeType": "application/json",
    },
]

_PROMPTS = [
    {
        "name": "workflow-protocol",
        "description": "Walker REST protocol: how to advance, what to query, when submit vs transition",
    },
]

_PROTOCOL_FALLBACK = (
    "WALKER PROTOCOL (MCP) — MANDATORY BEHAVIOR CONTRACT:\n"
    "\n"
    "1. GIT-LIKE: the walker operates on the current directory. Each project\n"
    "   has its own .cognitive/. prepare(path) initializes, list() shows managed.\n"
    "\n"
    "2. TO ADVANCE in the workflow, use submit with the node's data.\n"
    "   If the node is a decision (XOR), use submit with 'intent'.\n"
    "\n"
    "3. CODE ANALYSIS = USE THE WALKER CPG (MANDATORY):\n"
    "   The walker has a STRUCTURED CODE MEMORY (CPG) with a query layer.\n"
    "   Do NOT reason about code from scratch NOR read files directly.\n"
    "   BEFORE analyzing/implementing/verifying, query the project CPG:\n"
    "     - kg_search with _index:cpg + file:'<area>'  → symbols, signatures, imports\n"
    "     - cpg_business with file:'<area>'            → pure functions, flows, patterns\n"
    "     - analyze_code with area:'<area>'            → full code analysis\n"
    "   The CPG is the source of truth for the code (real names, real signatures).\n"
    "   Use what the CPG returns to build solutions; do NOT invent APIs.\n"
    "\n"
    "4. KNOWLEDGE MEMORY (CKG): query ckg before deciding patterns and\n"
    "   conventions. Use kg_store to REGISTER decisions, conventions and\n"
    "   patterns. ALWAYS persist in the project where you are actually\n"
    "   working: if you develop walker code, store in project 'walker';\n"
    "   if you validate another project, store there. Do not mix contexts.\n"
    "   (Workflow decisions are captured automatically by capture_decision.)\n"
    "\n"
    "5. REUSE what exists: cpg_business reveals already-implemented pure\n"
    "   functions and business flows. Reusing them avoids reinventing the wheel.\n"
    "\n"
    "6. SESSION WORK LOG (MANDATORY): when you COMPLETE a piece of work\n"
    "   (implemented a feature, fixed a bug, refactored, indexed, etc.),\n"
    "   PERSIST it with capture_task so it is not lost:\n"
    '     - walker <project>/capture_task \'{"objective":"...","domain":"...",\n'
    '         "files":["path/to/file.py"],"result_state":"..."}\'\n'
    "   This stores the task under an INFORMAL SPRING by date\n"
    "   (session-YYYYMMDD) that the DeltaGraph indexes as historical\n"
    "   context. The agent's work never gets lost, even without a formal\n"
    "   spring. The formal way remains plan_sprint + the workflow.\n"
    "   DECISIONS: if the work involved an architecture decision, INCLUDE it\n"
    "   in the same capture_task payload:\n"
    '     - walker <project>/capture_task \'{"objective":"...",\n'
    '         "decisions":[{"statement":"ADR-x: ...","rationale":"...",\n'
    '           "supersedes":["<prev statement>"]}]}\'\n'
    "   capture_task registers it in the CKG automatically (with lifecycle).\n"
    "   AUTO-DETECTION: if you do NOT report decisions but the 'files' touch\n"
    "   architecture layers (contracts, domain, workflow, config, engine,\n"
    "   repositories, providers), capture_task detects it and generates the\n"
    "   decision automatically using your objective as context.\n"
)


def _load_protocol_text() -> str:
    """Carga el contrato MCP desde el template canónico (SSOT).

    SSOT: features/prompts/templates/protocol/mcp.md (mismo path único de
    templates que el resto de prompts). Fallback: constante del código.
    """
    try:
        from features.prompts.loader import load_static
        text = load_static("protocol/mcp")
        return text or _PROTOCOL_FALLBACK
    except Exception:
        return _PROTOCOL_FALLBACK


_PROTOCOL_TEXT = _load_protocol_text()


# ── Project resolution (git-like: from CWD) ───────────────────────────────────


def _resolve_project_id(args: dict) -> str:
    """Resolves the project_id: uses args if provided, otherwise detects it
    from the CWD (like git detects .git/)."""
    pid = (args.get("project_id") or "").strip()
    if pid:
        return pid
    try:
        from features.project import ProjectService

        resolved = ProjectService.resolve_from_registry()
        if resolved:
            return resolved.get("project_id", "")
    except Exception:
        pass
    return ""


# ── Tool MCP → REST path translation (SSOT: the Router dispatches) ───────────

_TOOL_ROUTES = {
    "prepare": ("prepare", lambda a: {"path": a.get("path", os.getcwd())}),
    "list": ("list", lambda a: {}),
    "status": ("{project_id}/status", lambda a: {}),
    "submit": ("{project_id}/submit", lambda a: {"_payload": a.get("body", {})}),
    "transition": (
        "{project_id}/transition",
        lambda a: {"gate": a.get("gate", ""), "positional": [a.get("gate", "")]},
    ),
    "kg_search": ("{project_id}/kg_search", lambda a: {"_payload": a.get("query", {})}),
    "kg_multi": ("{project_id}/kg_multi", lambda a: {"_payload": a.get("layers", {})}),
    "kg_store": ("{project_id}/kg_store", lambda a: {"_payload": a.get("body", {})}),
    "cpg_business": (
        "{project_id}/cpg_business",
        lambda a: {"_payload": {"file": a.get("file", "")}},
    ),
    "cpg_unused_functions": (
        "{project_id}/cpg_unused_functions",
        lambda a: {},
    ),
    "project_metrics": (
        "{project_id}/project_metrics",
        lambda a: {},
    ),
    "analyze_code": (
        "{project_id}/analyze_code",
        lambda a: {"_payload": {"area": a.get("area", "")}},
    ),
    "help": ("help", lambda a: {}),
}


def _call_tool(name: str, args: dict) -> dict:
    """Translates an MCP tool to a REST path and delegates to the Router (SSOT).

    'analyze_code' is a COMPOSITE tool: it has no REST handler of its own;
    it combines CPG queries (kg_search + cpg_business) to deliver a full
    code analysis.
    """
    if name == "analyze_code":
        return _analyze_code(args)

    # Dispatch estandar: tool -> ruta REST mapeada en _TOOL_ROUTES.
    # FALLBACK (fix descubrimiento de tasks publicas): si la tool no esta
    # en el mapa (ej: cpg_unused_functions, project_metrics, kg_extract),
    # se despacha como task publica del proyecto via {project_id}/<nombre>.
    # Asi el MCP expone todas las tasks publicas del registro sin hardcodear.
    if name not in _TOOL_ROUTES:
        project_id = _resolve_project_id(args)
        path = f"{project_id}/{name}"
        flags = {"_payload": args.get("body", {})}
        from . import _ROUTER

        result = _ROUTER.dispatch(path, flags, method="POST")
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return {"type": "ok", "data": {"result": result}}

    route_tpl, flags_builder = _TOOL_ROUTES[name]
    project_id = _resolve_project_id(args)
    path = (
        route_tpl.format(project_id=project_id)
        if "{project_id}" in route_tpl
        else route_tpl
    )
    flags = flags_builder(args)

    from . import _ROUTER

    method = None
    if path.endswith(("submit", "transition")):
        method = "POST"
    elif path == "prepare":
        method = "POST"
    result = _ROUTER.dispatch(path, flags, method=method)
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result
    return {"type": "ok", "data": {"result": result}}


def _analyze_code(args: dict) -> dict:
    """Analyzes code by querying the project CPG (query layer).

    Combines:
      1. kg_search cpg by area  -> symbols, signatures, imports
      2. cpg_business by area   -> pure functions, flows, patterns
    """
    project_id = _resolve_project_id(args)
    area = (args.get("area") or "").strip()
    if not area:
        return {
            "type": "error",
            "status_code": 400,
            "message": "analyze_code requiere 'area' (substring del archivo)",
        }

    # 1. CPG symbols
    sym_resp = _call_tool(
        "kg_search",
        {
            "project_id": project_id,
            "query": {"_index": "cpg", "file": area},
        },
    )
    symbols = sym_resp.get("data", {}).get("result", {}) if sym_resp.get("data") else {}
    hits = symbols.get("hits", {}).get("hits", []) if isinstance(symbols, dict) else []

    # 2. Business layer
    biz_resp = _call_tool(
        "cpg_business",
        {
            "project_id": project_id,
            "file": area,
        },
    )
    biz = biz_resp.get("data", {}).get("result", {}) if biz_resp.get("data") else {}

    # Summarize symbols (avoid huge responses)
    files = {}
    functions = []
    imports = []
    for h in hits[:80]:
        src = h.get("_source", {})
        name = src.get("name", "")
        ftype = src.get("type", "")
        sig = src.get("signature", "")
        fpath = src.get("file", "")
        if ftype == "file":
            files[fpath] = fpath
        elif ftype == "module":
            imports.append({"name": name, "file": fpath, "signature": sig})
        elif ftype in (
            "function",
            "constant",
            "method",
            "interface",
            "struct",
            "class",
        ):
            functions.append(
                {
                    "name": name,
                    "type": ftype,
                    "file": fpath,
                    "line": src.get("line", 0),
                    "signature": sig,
                }
            )

    return {
        "type": "analyze_code",
        "status_code": 200,
        "message": f"Code analysis for '{area}' — {len(functions)} symbols, {len(imports)} imports",
        "data": {
            "area": area,
            "project_id": project_id,
            "files": sorted(files.keys()),
            "symbols": functions[:60],
            "imports": imports[:20],
            "business_layer": {
                "pure_functions": (biz.get("pure_functions") or [])[:15],
                "business_flows": (biz.get("business_flows") or [])[:8],
                "architecture_patterns": (biz.get("architecture_patterns") or [])[:8],
            },
        },
        "next_actions": [
            {
                "command": f"walker {project_id}/kg_search",
                "reason": "more specific CPG query",
            },
            {
                "command": f"walker {project_id}/cpg_business",
                "reason": "reuse functions/patterns of the area",
            },
        ],
    }


def _read_resource(uri: str) -> dict:
    """Reads a walker resource (context injectable to the agent)."""
    if uri.startswith("walker://status/"):
        project_id = uri.split("/")[-1]
        result = _call_tool("status", {"project_id": project_id})
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": dumps(result),
                }
            ]
        }
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": dumps({"error": "resource no encontrado"}),
            }
        ]
    }


# ── JSON-RPC 2.0 handler ────────────────────────────────────────────────────


def _handle_request(msg: dict) -> dict:
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False},
                    "prompts": {},
                },
                "serverInfo": {"name": "walker", "version": "3.1"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _TOOLS}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        try:
            result = _call_tool(name, args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": dumps(result)}
                    ],
                    "isError": result.get("status_code", 200) >= 400,
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": str(e)}],
                    "isError": True,
                },
            }
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": _RESOURCES}}
    if method == "resources/read":
        uri = params.get("uri", "")
        try:
            return {"jsonrpc": "2.0", "id": msg_id, "result": _read_resource(uri)}
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "contents": [{"uri": uri, "mimeType": "text/plain", "text": str(e)}]
                },
            }
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": _PROMPTS}}
    if method == "prompts/get":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "description": "Workflow protocol",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": _PROTOCOL_TEXT},
                    }
                ],
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Metodo '{method}' no soportado"},
    }


def _is_notification(msg: dict) -> bool:
    """JSON-RPC 2.0: un mensaje SIN 'id' es una notificación y NO debe
    recibir respuesta (p. ej. 'notifications/initialized'). Responderla
    rompe el handshake MCP (opencode desincroniza y tools/list queda vacío)."""
    return "id" not in msg or msg.get("id") is None


# ── Recarga de codigo (fix estructural 'server stale') ──────────────────────
# El server MCP cargaba los modulos de features/ una vez al arrancar. Cualquier
# fix en disco (engine, handlers) quedaba inactivo hasta reiniciar el proceso,
# perdiendo la conexion MCP de la sesion. Este mecanismo recarga los modulos
# cuando su source cambia, sin reiniciar.

import importlib

_FEATURES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "features"
)
_reload_cache: dict[str, tuple[float, int]] = {}


def _reload_features_if_changed() -> None:
    """Recarga modulos de features/ cuyo source cambio en disco.

    Escanea recursivamente features/ buscando .py, compara (mtime, size)
    contra la cache, y si cambio recarga el modulo via importlib.reload.
    Best-effort: no rompe si un modulo no es recargable.
    """
    if not os.path.isdir(_FEATURES_DIR):
        return
    changed_files: list[str] = []
    for root, dirs, files in os.walk(_FEATURES_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            try:
                st = os.stat(path)
                sig = (st.st_mtime, st.st_size)
            except OSError:
                continue
            if _reload_cache.get(path) == sig:
                continue
            _reload_cache[path] = sig
            changed_files.append(path)

    if not changed_files:
        return

    # Recargar los modulos de features/ que esten importados
    import sys

    changed_real = {os.path.realpath(p) for p in changed_files}
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith("features."):
            continue
        mod_file = getattr(mod, "__file__", None)
        if not mod_file:
            continue
        mod_path = mod_file.replace(".pyc", ".py")
        try:
            mod_path = os.path.realpath(mod_path)
        except OSError:
            continue
        if mod_path in changed_real:
            try:
                importlib.reload(mod)
            except Exception:
                pass  # best-effort: si no recarga, sigue con la version actual


def serve_stdio() -> int:
    """stdio JSON-RPC loop: reads requests line by line, responds.

    RECARGA DE CODIGO (fix estructural 'server stale'): entre request y
    request se verifica si los modulos de features/ cambiaron en disco.
    Si cambian, se recargan via importlib.reload para que el server tome
    los fixes SIN reiniciar el proceso ni perder la conexion MCP.

    Las NOTIFICACIONES (sin id) se procesan pero NO se responden — el
    protocolo MCP las usa como señales one-way (initialized, cancelled).
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            _reload_features_if_changed()
        except Exception as _reload_err:
            # Recarga best-effort: si falla, se loggea y se continua con
            # los modulos actuales (no romper el loop por un reload).
            import logging

            logging.getLogger(__name__).warning(
                "Recarga de codigo fallo (best-effort): %s", _reload_err
            )
        try:
            msg = json.loads(line)
            resp = _handle_request(msg)
        except json.JSONDecodeError:
            resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        if _is_notification(msg):
            continue
        sys.stdout.write(dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    """MCP adapter entry point (driver). No business logic."""
    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
