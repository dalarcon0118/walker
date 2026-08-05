/**
 * Walker hooks — protection, auto-save, trace, compaction.
 *
 * Project-level plugin (.opencode/plugins/).
 * Auto-loaded on opencode start for this project.
 *
 * Message template: [WALKER] blocked <action> on <target>
 *                    Reason: <reason>
 *                    Allowed: <list>
 */

export const WalkerHooks = async ({ project, $, directory }) => {
  const WALKER_ROOT = "/Users/davidmartinez/develop/Active-projects/walker"

  // ── Message template ──

  const WALKER_LABEL = "[WALKER]"

  const TEMPLATE = (action, target, reason, allowed) =>
    `${WALKER_LABEL} blocked ${action} on ${target}\n` +
    `Reason: ${reason}\n` +
    `Allowed: ${allowed}`

  const ALLOWED_TOOLS =
    "walker_kg_store, walker_kg_search, walker_kg_multi, " +
    "walker_cpg_business, walker_analyze_code, " +
    "walker_submit, walker_transition, walker_capture_task"

  // ── Helpers ──

  const runWalker = async (code) => {
    try {
      const result = await $`python3 -c ${code}`.cwd(WALKER_ROOT).quiet()
      return result.text().trim()
    } catch {
      return null
    }
  }

  const ckgSave = async (type, statement, tags = []) => {
    const imp = "from features.cli_interface.application.handlers import _run_public_task"
    const body = JSON.stringify({ type, statement, tags })
    const code = `${imp}; r=_run_public_task("kg_store",${body}); print("ok="+str(r.get('data',{}).get('ok',False)))`
    return await runWalker(code)
  }

  const isProtected = (fp) => {
    if (!fp) return false
    return String(fp).includes(".cognitive/")
  }

  // ── Hooks ──

  return {
    // ═══════════════════════════════════════════════════════════
    // PROTECTION: block direct filesystem edits on .cognitive/
    // ═══════════════════════════════════════════════════════════

    "tool.execute.before": async (input, output) => {
      const tool = input.tool

      // Block edit / write on .cognitive/
      if (tool === "edit" || tool === "write") {
        const fp = output?.args?.filePath || input?.args?.filePath || ""
        if (isProtected(fp)) {
          throw new Error(
            TEMPLATE(
              tool,
              `path "${fp}"`,
              "cognitive data must be modified through walker MCP tools only",
              ALLOWED_TOOLS
            )
          )
        }
      }

      // Block destructive bash on .cognitive/
      if (tool === "bash") {
        const cmd = String(output?.args?.command || input?.args?.command || "")

        // Block inline Python — force walker MCP tool usage
        if (/python3?\s+-c\b|python3?\s+-\s*<<|python3?\s+<<|python3?\s+<<</.test(cmd)) {
          throw new Error(
            TEMPLATE(
              "python3",
              "inline execution",
              "all scripting must go through walker MCP tools",
              ALLOWED_TOOLS + ", python3 -m pytest (testing only)"
            )
          )
        }

        // Block destructive commands on .cognitive/ (allow read-only)
        if (isProtected(cmd)) {
          const readOnly = /^\s*(cat|head|grep|find|ls|echo|wc|stat|file)\s/.test(cmd)
          if (!readOnly) {
            throw new Error(
              TEMPLATE(
                "bash",
                `.cognitive/ path`,
                "cognitive data must be modified through walker MCP tools only",
                "cat, grep, find, ls (read-only) | " + ALLOWED_TOOLS
              )
            )
          }
        }
      }
    },

    // ═══════════════════════════════════════════════════════════
    // AUTO-SAVE: persist session end to CKG
    // ═══════════════════════════════════════════════════════════

    "session.idle": async () => {
      const now = new Date().toISOString().slice(0, 19)
      await ckgSave(
        "session_end",
        `Session completed — ${now}. OpenCode agent session in walker project.`,
        ["session", "auto-save"]
      )
    },

    // ═══════════════════════════════════════════════════════════
    // TRACE: log modification tool calls to CKG
    // ═══════════════════════════════════════════════════════════

    "tool.execute.after": async (input) => {
      const tool = input?.tool
      if (!tool) return
      if (!["edit", "write", "bash"].includes(tool)) return

      const now = new Date().toISOString()
      await ckgSave(
        "tool_trace",
        `Tool executed: ${tool} — ${now}`,
        ["trace", "tool", tool]
      )
    },

    // ═══════════════════════════════════════════════════════════
    // COMPACTION: inject walker context into prompt
    // ═══════════════════════════════════════════════════════════

    "experimental.session.compacting": async (input, output) => {
      let ckgInfo = ""
      try {
        const code = `from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph; import os; c=CodeKnowledgeGraph(os.getcwd()); n=len(c.query({})); print(n)`
        const nodes = await runWalker(code)
        ckgInfo = `\n- CKG nodes: ${nodes || "?"}`
      } catch { /* ignore */ }

      output.context.push(
        `## Walker Protocol (auto-injected)\n` +
        `- Project: walker (features/project DDD Clean Architecture)${ckgInfo}\n` +
        `- ADR-WALKER-013: DeltaGraph as single source of truth for spring state\n` +
        `- ADR-WALKER-014: plan.json / progress.json eliminated\n` +
        `- ADR-WALKER-015: features/project/ migrated to DDD Clean Architecture\n` +
        `\n` +
        `### QUERY PROTOCOL (MANDATORY)\n` +
        `Before using grep or read to search source code, you MUST:\n` +
        `1. walker_kg_schema → discover available indices (ckg, cpg, delta, trace)\n` +
        `2. walker_kg_search → query CKG (knowledge, ADRs, decisions, patterns)\n` +
        `3. walker_kg_multi → federated query across CPG + CKG + Delta\n` +
        `4. walker_cpg_business → business layer: reusable functions, flows, patterns\n` +
        `5. walker_analyze_code → full code analysis via CPG\n` +
        `\n` +
        `Only after exhausting walker queries, use grep/read as fallback.\n` +
        `\n` +
        `### STRICT RULES\n` +
        `- FORBIDDEN: python3 -c / python3 << → use walker MCP tools\n` +
        `- FORBIDDEN: direct edits to .cognitive/ → use walker MCP tools\n` +
        `- walker_kg_store → register ADRs after architectural decisions\n` +
        `- walker_submit / walker_transition → navigate spring workflow\n` +
        `- walker_capture_task → capture specs into active sprint\n`
      )
    },
  }
}
