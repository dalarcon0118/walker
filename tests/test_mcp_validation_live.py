#!/usr/bin/env python3
"""MCP Protocol Validation — Agent-as-Validator (ADR-WALKER-073).

Normative method.
-------------
The Walker MCP protocol is validated by the **agent calling MCP tools directly**
(`walker_*` on the Walker server, `memora_*` on the memora server), interpreting
each response semantically. The agent is the native validator; it does **not**
reimplement an MCP client.

This file supersedes an earlier draft that shipped a hand-rolled
`MCPClient` (subprocess `Popen` + raw JSON-RPC over stdin/stdout). That approach
was **abandoned as an anti-pattern**: fragile pipe buffering, it duplicated the
protocol, and it could not run under `pytest` (it had a `main()` entrypoint).
Decision recorded in memory id 11842.

What this placeholder provides.
------------------------------
A set of **agent-executable prompts** per phase. An agent executes these directly
against the live MCP surface and writes its observed results to
`docs/MCP_VALIDATION_REPORT.md`. This file intentionally contains **no executable
MCP client** — that is the point. It exists so a future agent/reviewer knows *how*
the validation is performed and *what* to exercise.

Phases and the agent-driven surface exercised:
- Phase 0 — Connection & Discovery: `walker_list`, `walker_status`, `walker_prepare`,
  `memora_memory_stats`, `memora_memory_tags`.
- Phase 1 — Agent tool calls: interpret each response (idempotency, health, counts).
- Phase 2 — Workflow E2E: submit/transition path is covered at the BDD/application
  layer (`tests/test_fast_mcp_workflow_application_bdd.py`) and via the official
  stdio client (`tests/test_fast_mcp_stdio_validation_bdd.py`), not by a re-written
  client.
- Phase 3 — Error handling: idempotent prepare, graceful status without sprint,
  tool-gate enforcement.
- Phase 4 — Production readiness: server stability, structured responses, full suite.

The consolidated, honest-coverage report lives at `docs/MCP_VALIDATION_REPORT.md`.

Agent prompts
-------------
Phase 1 — call each tool and record the observed result:

    walker_list({})                       # -> 7 projects, registry data/projects.json
    walker_status({"project_id": "walker"})  # -> health, language python, sprint: null
    walker_prepare({"project_id": "walker"}) # -> idempotent already_exists
    memora_memory_stats({})               # -> total_memories, tag_counts
    memora_memory_tags({})                # -> {"allowed": []}

Then write the consolidated results to docs/MCP_VALIDATION_REPORT.md with an honest
coverage statement (which tools were actually exercised by the agent vs. covered at
the BDD/application layer).
"""

from __future__ import annotations

__all__: list[str] = []
