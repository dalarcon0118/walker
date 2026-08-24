WALKER PROTOCOL (MCP) — MANDATORY BEHAVIOR CONTRACT:

1. GIT-LIKE: the walker operates on the current directory. Each project
   has its own .cognitive/. prepare(path) initializes, list() shows managed.

1b. MULTI-PROJECT (MANDATORY when working outside the server's cwd):
   EVERY tool accepts `project_id`. If you are coding in a project whose
   path differs from the walker installation, ALWAYS send it:
     - status({project_id: "react-native-frontend"})  → that project's health
     - read_code({file, project_id})                  → that project's files
     - task_context / sprint_evidence / submit ...    → same rule
   Omitting it silently anchors to the server's startup project — the #1
   cause of "empty context" or wrong-sprint responses. When unsure which
   id to use: list() shows all registered projects.

2. TO ADVANCE in the workflow, use submit with the node's data.
   If the node is a decision (XOR), use submit with 'intent'.

3. CODE ANALYSIS = USE THE WALKER CPG (MANDATORY):
    The walker has a STRUCTURED CODE MEMORY (CPG) with a query layer.
    Do NOT reason about code from scratch NOR read files directly.
    BEFORE analyzing/implementing/verifying, query the project CPG:
      - kg_search with _index:cpg + file:'<area>'  → symbols, signatures, imports
      - cpg_business with file:'<area>'            → pure functions, flows, patterns
      - analyze_code with area:'<area>'            → full code analysis
    The CPG is the source of truth for the code (real names, real signatures).
    Use what the CPG returns to build solutions; do NOT invent APIs.
    <!-- ANALYSIS_TOOLS -->

4. KNOWLEDGE MEMORY (CKG): query ckg before deciding patterns and
   conventions. Use save_to_ckg to REGISTER decisions, conventions and
   patterns. ALWAYS persist in the project where you are actually
   working: if you develop walker code, store in project 'walker';
   if you validate another project, store there. Do not mix contexts.
   (Workflow decisions are captured automatically by capture_decision.)

5. REUSE what exists: cpg_business reveals already-implemented pure
   functions and business flows. Reusing them avoids reinventing the wheel.

6. SESSION WORK LOG (MANDATORY): when you COMPLETE a piece of work
   (implemented a feature, fixed a bug, refactored, indexed, etc.),
   PERSIST it with capture_task so it is not lost:
     - walker <project>/capture_task '{"objective":"...","domain":"...",
         "files":["path/to/file.py"],"result_state":"..."}'
   This stores the task under an INFORMAL SPRING by date
   (session-YYYYMMDD) that the DeltaGraph indexes as historical
   context. The agent's work never gets lost, even without a formal
   spring. The formal way remains plan_sprint + the workflow.
   DECISIONS: if the work involved an architecture decision, INCLUDE it
   in the same capture_task payload:
     - walker <project>/capture_task '{"objective":"...",
         "decisions":[{"statement":"ADR-x: ...","rationale":"...",
           "supersedes":["<prev statement>"]}]}'
   capture_task registers it in the CKG automatically (with lifecycle).
   AUTO-DETECTION: if you do NOT report decisions but the 'files' touch
   architecture layers (contracts, domain, workflow, config, engine,
   repositories, providers), capture_task detects it and generates the
   decision automatically using your objective as context.
