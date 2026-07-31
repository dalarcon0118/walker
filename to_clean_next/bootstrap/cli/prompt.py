"""cli/prompt.py — Agent help text for walker3.

Single source of _AGENT_PROMPT shown in cmd_help.
Separated so help text changes don't require touching command logic.

GUIDING PRINCIPLE: The help does NOT hardcode workflows or tasks.
These are discovered dynamically via (workflow, tasks) commands.
If nodes, gates, or tasks change tomorrow, the help remains valid
without modifications.
"""
from __future__ import annotations

_AGENT_PROMPT = """
╔══════════════════════════════════════════════════════════════╗
║              WALKER3 — AGENT GUIDE                         ║
║  Meta-guide: teaches how to discover workflows dynamically ║
╚══════════════════════════════════════════════════════════════╝

Walker3 is a generic workflow engine. NOT limited to software —
any workflow works. The engine injects context (CPG, CKG,
DeltaGraph, Trace) automatically at each node. You do not need
to run manual queries.


═══ STEP 1: IDENTIFY YOURSELF AS AN AGENT ═══

  walker3 agents/<your-id>/status

  This is the FIRST command. Without it, walker3 doesn't know who you are.
  Replace <your-id> with your identifier (codebuff, opencode, etc.).


═══ STEP 2: CHOOSE PROJECT AND SPRINT ═══

  walker3 agents/<id>/list              ← View available projects
  walker3 agents/<id>/<project>/list    ← View sprints of a project
  walker3 agents/<id>/switch <project>[/<sprint>]  ← Activate session

  GENERAL SYNTAX (always use agents/<id>/ prefix):

  walker3 agents/<id>/<project>/<sprint>/status    ← Sprint status
  walker3 agents/<id>/<project>/<sprint>/submit    ← Send data
  walker3 agents/<id>/<project>/<sprint>/transition ← Change node
  walker3 agents/<id>/<project>/workflow           ← Full workflow
  walker3 agents/<id>/<project>/task/<name>        ← Run task


═══ STEP 3: DISCOVER THE WORKFLOW (ALWAYS) ═══

  walker3 agents/<id>/<project>/workflow

  This returns the COMPLETE and CURRENT workflow map:
  • Available nodes and sub-states
  • Descriptions for each node
  • Required fields (input_schema) for submit
  • Gates (transitions) available from each node
  • Event handlers on:{} that run automatically

  ⚠️  ALWAYS use workflow before deciding what to do.
  Workflows vary by project — never assume.


═══ STEP 4: KNOW WHAT TO DO AT THE CURRENT NODE ═══

  walker3 agents/<id>/<project>/<sprint>/status

  The status output tells you EVERYTHING you need:
  • current_node / current_sub_state — where you are
  • execution_type — how it runs:
      auto        → runs by itself, no input needed
      llm_task    → you must send data via submit
      user_input  → you pick an option via submit
  • required_fields — what fields submit expects
  • available_gates — where you can go next
  • prompt — instructions for the current node

  submit vs transition:
  • submit  → sends data to the CURRENT node (fills required_fields)
  • transition → moves to the NEXT node (uses a gate)


═══ STEP 5: DISCOVER AVAILABLE TASKS ═══

  walker3 agents/<id>/tasks                    ← List all
  walker3 agents/<id>/tasks/<name>             ← Detailed help

  Each task has its own documentation:
  • Description of what it does
  • Input schema (required / optional fields)
  • Output schema (what it returns)
  • Usage example

  ⚠️  Prefer navigating the workflow over running tasks directly.
  Direct tasks skip validations (gates, contracts, side-effects).
  Use task/ ONLY for:
  • Context queries (query)
  • Auxiliary operations (trace_push)
  • Debugging


═══ STEP 6: QUERY CONTEXT LAYERS ═══

  Available layers are self-discoverable:
  walker3 agents/<id>/tasks/query

  The tasks/query output lists all layers registered in the
  query engine (cpg, ckg, delta, trace, state, and future ones).
  Never assume which layers exist — discover them always.

  Example queries (once you know the layers):
  walker3 agents/<id>/<project>/task/query --query '{"trace":{"type":"TRANSITION"}}'
  walker3 agents/<id>/<project>/task/query --query '{"cpg":{"function":"my_func"}}'
  walker3 agents/<id>/<project>/task/query --query '{"ckg":{"type":"decision"}}'


═══ ERROR RECOVERY ═══

  When something fails (auto-advance error, validation, etc.):

  1. Read the error in the command result
  2. Use status to see which node you're on
  3. Check available_gates in the status output
  4. Pick the gate that makes sense for recovery:
     • Gates with "return to sprint dashboard" or "back to active"
     • Gates with "edit plan" or "re-planify"
     • Gates with "cancel task" or "close"
  5. Run: transition <selected_gate>


═══ QUICK COMMAND REFERENCE ═══

  agents/<id>/status                          Dashboard
  agents/<id>/<project>/status                Project status
  agents/<id>/<project>/<sprint>/status       Sprint status
  agents/<id>/<project>/<sprint>/submit --k v  Send data
  agents/<id>/<project>/<sprint>/transition g  Change node
  agents/<id>/<project>/workflow              Workflow map
  agents/<id>/<project>/task/<name> --k v     Run task
  agents/<id>/tasks                           List tasks
  agents/<id>/tasks/<name>                    Task help
  agents/<id>/list                            Projects
  agents/<id>/switch <proj>[/<sp>]            Change session
  agents/<id>/session                         Current session
  agents/<id>/config                          Configuration
  help                                        This guide
"""
