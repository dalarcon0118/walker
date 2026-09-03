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

  // ── Proyecto (decisión PO ADR-WALKER-018) ──
  // El project_id viene de la config/entorno del agente (WALKER_PROJECT_ID),
  // NO se auto-detecta. Fallback: "walker" (el proyecto del propio hook). Las
  // llamadas `runWalker` usan la ruta `{PROJECT_ID}/<task>`.
  const PROJECT_ID = process.env.WALKER_PROJECT_ID || "walker"

  // ── Guard .cognitive/ (ADR-WALKER-018) ─────────────────────────
  // Booleano que habilita/deshabilita la PROTECCIÓN de .cognitive/.
  //   WALKER_GUARD_ENABLED=0|false → no bloquea edit/write/read/bash
  //   sobre .cognitive/ (debug: inspeccionar/limpiar el estado cognitivo).
  //   Default: habilitado (protección activa).
  const WALKER_GUARD_ENABLED =
    process.env.WALKER_GUARD_ENABLED !== "0" &&
    process.env.WALKER_GUARD_ENABLED !== "false"

  // ── Message template ──

  const WALKER_LABEL = "[WALKER]"

  const TEMPLATE = (action, target, reason, allowed) =>
    `${WALKER_LABEL} blocked ${action} on ${target}\n` +
    `Reason: ${reason}\n` +
    `Allowed: ${allowed}`

  const ALLOWED_TOOLS =
    "walker_documenting_knowledge, walker_recall_knowledge, walker_search_memories, " +
    "walker_code_context, walker_inspect_codebase, " +
    "walker_submit, walker_transition, walker_capture"

  // ── Helpers ──

  // El hook usa el CLI walker (bin/walker3) — NO python inline.
  // Esto respeta la misma regla que impone al agente (ADR-016 / hook).
  // args se pasan como array: Bun shell escapa cada elemento como un solo
  // argumento (el JSON con comillas internas llega intacto al CLI).
  // Si el primer arg es una task pública (sin prefijo de proyecto), se le
  // antepone `{PROJECT_ID}/` para apuntar al proyecto configurado del agente.
  const runWalker = async (env = {}, ...args) => {
    const first = String(args[0] || "")
    const full = first.includes("/") ? args : [`${PROJECT_ID}/${first}`, ...args.slice(1)]
    try {
      const result = await $`bin/walker3 ${full}`.cwd(WALKER_ROOT).env(env).quiet()
      return result.text().trim()
    } catch {
      return null
    }
  }

  // Extrae el primer objeto JSON de la salida del CLI (viene con texto envolvente).
  const extractJson = (txt) => {
    if (!txt) return null
    const start = txt.indexOf("{")
    const end = txt.lastIndexOf("}")
    if (start < 0 || end <= start) return null
    try {
      return JSON.parse(txt.slice(start, end + 1))
    } catch {
      return null
    }
  }

  // Extensiones que el CPG indexa (multi-lenguaje). Files like .md/.json no
  // tienen nodos útiles (son datos, no código) → se salta la consulta.
  const CODE_EXT = /\.(py|js|jsx|cjs|mjs|ts|tsx|go|rs|java|rb|kt|kts|sh|php|c|cpp|h|hpp|swift)$/

  // ── Context gates (ADR-WALKER-018) ─────────────────────────────────────
  // Cada tool de acción tiene un checklist de gates que se ejecuta ANTES de la
  // tool (annotate, nunca bloquea) y cuyo resultado se anexa al output.
  // Cada gate: { key, task, buildArgs(ctx), why } donde buildArgs recibe el
  // contexto del call (filepath, pattern) y devuelve el JSON args de la task.

  const relPath = (filepath) =>
    String(filepath || "").replace(WALKER_ROOT + "/", "")

  // Scope CKG: subir al nivel de feature (features/<feature>/...) donde vive
  // el conocimiento (el CKG se indexa por feature), igual que en read.
  const ckgScopeFor = (filepath) => {
    const rel = relPath(filepath)
    const parts = rel.split("/")
    const featureIdx = parts.indexOf("features")
    return featureIdx >= 0 && parts.length >= featureIdx + 2
      ? parts.slice(0, featureIdx + 2).join("/")
      : parts.slice(0, -1).join("/") || rel
  }

  const GATES = {
    grep: {
      why: "before searching on disk, check the indexed memories (CPG/CKG) for the same symbols",
      items: [
        {
          key: "grep-multi",
          task: "search_memories",
          buildArgs: (ctx) => {
            const scope = ckgScopeFor(ctx.filePath || ctx.directory)
            return JSON.stringify({
              cpg: ctx.pattern ? { name: ctx.pattern } : {},
              ckg: { scope },
            })
          },
          why: "hits indexados en CPG/CKG antes de escanear disco",
        },
        {
          key: "grep-schema",
          task: "memory_schema",
          buildArgs: () => JSON.stringify({}),
          why: "descubrir índices disponibles",
        },
      ],
    },
    glob: {
      why: "the CPG already indexes project files; check it before globbing disk",
      items: [
        {
          key: "glob-multi",
          task: "search_memories",
          buildArgs: (ctx) =>
            JSON.stringify({
              cpg: ctx.pattern ? { name: ctx.pattern } : { file: relPath(ctx.filePath) },
            }),
          why: "el CPG ya indexa archivos del proyecto",
        },
      ],
    },
    // read NO está en GATES a propósito: ya inyecta contexto rico vía
    // queryCpgForFile (IMPORTS/SYMBOLS/CONOCIMIENTO) en tool.execute.after.
    // Correr también gates duplicaría la consulta search_memories por archivo.
    edit: {
      why: "decisions/conventions of the scope apply before modifying code",
      items: [
        {
          key: "edit-ckg",
          task: "search_memories",
          buildArgs: (ctx) => {
            const scope = ckgScopeFor(ctx.filePath)
            return JSON.stringify({ ckg: { scope } })
          },
          why: "decisiones/convenciones que aplican al archivo",
        },
      ],
    },
    write: {
      why: "decisions/conventions of the scope apply before writing code",
      items: [
        {
          key: "write-ckg",
          task: "search_memories",
          buildArgs: (ctx) => {
            const scope = ckgScopeFor(ctx.filePath)
            return JSON.stringify({ ckg: { scope } })
          },
          why: "decisiones/convenciones que aplican al archivo",
        },
      ],
    },
    bash: {
      why: "check recent trace of bash commands before executing a new one",
      items: [
        {
          key: "bash-trace",
          task: "recall_knowledge",
          buildArgs: () => JSON.stringify({ index: "trace", query: { tool: "bash" } }),
          why: "historial reciente de comandos (trace)",
        },
      ],
    },
  }

  // Cache callID -> resumen de gates: recolectado en tool.execute.before,
  // anexado al output en tool.execute.after (misma callID).
  const pendingGatesByCall = new Map()
  // Cache por sesión: gates ya ejecutadas (no se re-consultan en la sesión).
  const gatesDoneBySession = new Map()

  // Ejecuta las gates de una tool y formatea el resultado en líneas compactas.
  const runGatesForTool = async (tool, ctx, callID, sessionID) => {
    const def = GATES[tool]
    if (!def) return
    const lines = []
    lines.push(`[WALKER] Context gates (ADR-WALKER-018) — Before ${tool}, execute this checklist:`)
    const done = gatesDoneBySession.get(sessionID) || new Set()
    for (const gate of def.items) {
      const doneKey = `${tool}:${gate.key}`
      let raw = done.has(doneKey) ? null : await runWalker(gate.task, gate.buildArgs(ctx))
      if (raw) done.add(doneKey)
      const data = raw ? extractJson(raw) : null
      if (data) {
        // Compactar: resumen de hits por índice.
        const summary = summarizeHits(data, gate)
        lines.push(`  [${done.has(doneKey) ? "x" : " "}] ${gate.task} (${gate.why}): ${summary}`)
      } else {
        lines.push(`  [${done.has(doneKey) ? "x" : " "}] ${gate.task} (${gate.why}): sin datos indexados`)
      }
    }
    gatesDoneBySession.set(sessionID, done)
    if (lines.length > 1) {
      // Combina con gates previas del mismo call (ej: bash que hace grep).
      const prev = pendingGatesByCall.get(callID)
      pendingGatesByCall.set(callID, prev ? `${prev}\n${lines.join("\n")}` : lines.join("\n"))
    }
  }

  // Convierte la respuesta de search_memories/recall_knowledge/memory_schema en un resumen corto.
  const summarizeHits = (data, gate) => {
    const parts = []
    const resp = data.responses || data
    for (const [idx, r] of Object.entries(resp)) {
      if (r && typeof r === "object" && r.hits && r.hits.total !== undefined) {
        parts.push(`${idx}=${r.hits.total}`)
      }
    }
    if (parts.length) return `hits: ${parts.join(" ")}`
    if (data.hits && data.hits.total !== undefined) return `hits: ${data.hits.total}`
    if (data.total_docs !== undefined) return `índices: ${Object.keys(data.types || {}).join(",") || data.total_docs}`
    if (data.error) return `error: ${data.error}`
    return "ok"
  }


  // Cache callID -> contexto CPG: recolectado en tool.execute.before (read),
  // anexado al output en tool.execute.after (misma callID).
  const pendingCpgByCall = new Map()

  // Consulta federada search_memories ANTES de que el agente lea un archivo:
  //   - cpg:  estructura indexada del archivo (imports, functions, classes,
  //           methods) — qué existe ya, para que el modelo no re-descubra.
  //   - ckg:  decisiones/convenciones con scope relacionado al archivo.
  // Resultado compacto (top N por sección) para no inflar el contexto.
  const queryCpgForFile = async (callID, filepath) => {
    const rel = String(filepath).replace(WALKER_ROOT + "/", "")
    if (!rel || !CODE_EXT.test(rel)) return
    const base = rel.split("/").pop() || rel
    // Scope CKG: subir al nivel de feature (features/<feature>/...) donde vive
    // el conocimiento, no al subdir del archivo (el CKG se indexa por feature).
    const parts = rel.split("/")
    const featureIdx = parts.indexOf("features")
    const scope =
      featureIdx >= 0 && parts.length >= featureIdx + 2
        ? parts.slice(0, featureIdx + 2).join("/")
        : parts.slice(0, -1).join("/") || rel
    try {
      const raw = await runWalker(
        "search_memories",
        JSON.stringify({ cpg: { file: base }, ckg: { scope } })
      )
      const data = extractJson(raw)
      if (!data || !data.responses) return

      const cpgHits = data.responses.cpg?.hits?.hits || []
      const ckgHits = data.responses.ckg?.hits?.hits || []

      // Solo nodos del archivo exacto que se va a leer (no de otros con
      // nombre similar en el path).
      const exact = cpgHits.filter(
        (h) => h._source?.file && h._source.file.endsWith(rel)
      )
      if (!exact.length && !ckgHits.length) return

      const lines = []
      const modules = exact.filter((h) => h._source?.type === "module")
      if (modules.length) {
        lines.push("IMPORTS (CPG):")
        for (const m of modules.slice(0, 12)) {
          lines.push(`- ${m._source.signature || m._source.name}`)
        }
      }
      const symbols = exact.filter((h) =>
        ["function", "class", "method"].includes(h._source?.type)
      )
      if (symbols.length) {
        lines.push("SYMBOLS (CPG):")
        for (const s of symbols.slice(0, 15)) {
          const src = s._source
          const sig = src.signature || `${src.type} ${src.name}`
          lines.push(`- L${src.line} ${sig}`)
        }
      }
      if (ckgHits.length) {
        lines.push("CONOCIMIENTO (CKG, scope relacionado):")
        for (const k of ckgHits.slice(0, 4)) {
          const src = k._source || {}
          lines.push(`- [${src.type}] ${src.subject || src.statement || src.name}`)
        }
      }
      if (lines.length) {
        pendingCpgByCall.set(callID, lines.join("\n"))
      }
      console.log(`[WALKER][debug] search_memories ${base}: cpg=${cpgHits.length} exact=${exact.length} ckg=${ckgHits.length} lines=${lines.length}`)
    } catch (e) { console.log(`[WALKER][debug] search_memories ${base} ERROR: ${e}`) /* silencioso a proposito: el read nunca se bloquea por el contexto */ }
  }

  const ckgSave = async (type, statement, tags = []) => {
    // Via MCP tool documenting_knowledge (walker_documenting_knowledge) — nunca python directo.
    const payload = JSON.stringify({ type, statement, tags })
    return await runWalker("documenting_knowledge", payload)
  }

  const isProtected = (fp) => {
    if (!fp) return false
    const p = String(fp).replace(/\\/g, "/")
    // Normalizar: .cognitive/ (con/sin punto inicial), doble slash, ../
    if (p.includes(".cognitive/") || p.includes("cognitive/") || p.includes("cognitive\\")) return true
    // Paths cognitivos del root (sin .cognitive/): data/projects.json, data/*.json.gz
    if (/\/data\/(projects\.json|[a-z_]+\.json\.gz)/.test(p)) return true
    if (/^data\/(projects\.json|[a-z_]+\.json\.gz)/.test(p)) return true
    return false
  }

  const isReadOnlyCmd = (cmd) => {
    // Solo lectura: cat/head/grep/find/ls/wc/stat/file — SIN redirección
    // ni operadores de escritura (> >> tee touch mkdir rm mv cp truncate)
    if (/(>|>>|tee|touch|mkdir|rm\b|mv\b|cp\b|truncate)/.test(cmd)) return false
    return /^\s*(cat|head|grep|find|ls|echo|wc|stat|file)\s/.test(cmd)
  }

  // ── Trace state (ADR-WALKER-018: eventos estructurados en índice trace) ──
  const sessionStartTimes = new Map()
  const sessionEnded = new Set()
  const processedMessageIds = new Set()
  const sessionToolStarts = new Map()

  // ── Trace helpers ──

  const tracePush = async (eventData) => {
    const sid = eventData.session_id || ""
    const aid = eventData.agent_id || ""
    const env = {}
    if (sid) env.WALKER_SESSION_ID = sid
    if (aid) env.WALKER_AGENT_ID = aid
    const payload = JSON.stringify(eventData)
    return await runWalker(env, "trace_push", payload)
  }

  const ensureSessionStart = async (sessionID, agentID = "") => {
    if (!sessionID || sessionStartTimes.has(sessionID)) return
    sessionStartTimes.set(sessionID, Date.now())
    await tracePush({
      event_type: "session_start",
      source: "agent_hook",
      session_id: sessionID,
      agent_id: agentID,
      timestamp: new Date().toISOString(),
    })
  }

  const pushSessionEnd = async (sessionID) => {
    if (!sessionID || sessionEnded.has(sessionID)) return
    sessionEnded.add(sessionID)
    const start = sessionStartTimes.get(sessionID) || Date.now()
    const durationMs = Date.now() - start
    await tracePush({
      event_type: "session_end",
      source: "agent_hook",
      session_id: sessionID,
      started_at: new Date(start).toISOString(),
      finished_at: new Date().toISOString(),
      duration_ms: durationMs,
    })
  }

  // ── Hooks ──

  return {
    // ═══════════════════════════════════════════════════════════
    // TRACE: agent messages (structured agent_message events)
    // ═══════════════════════════════════════════════════════════

    "experimental.chat.messages.transform": async (input, output) => {
      for (const msg of output.messages || []) {
        const info = msg.info || {}
        const sid = info.sessionID || ""
        const mid = info.id || ""
        if (!sid || !mid || processedMessageIds.has(mid)) continue
        processedMessageIds.add(mid)

        await ensureSessionStart(sid, info.agent || "")

        const role = info.role || "unknown"
        const timestamp = info.time?.created ? new Date(info.time.created).toISOString() : new Date().toISOString()
        let tokenCount = 0
        if (info.tokens) {
          tokenCount = info.tokens.total ?? info.tokens.input + info.tokens.output ?? 0
        }
        let contentExtract = ""
        for (const part of msg.parts || []) {
          if (part.type === "text" && typeof part.text === "string" && part.text.trim()) {
            contentExtract = part.text.trim().slice(0, 200)
            break
          }
        }

        await tracePush({
          event_type: "agent_message",
          source: "agent_hook",
          session_id: sid,
          agent_id: info.agent || "",
          timestamp,
          metadata: {
            role,
            token_count: tokenCount,
            content_extract: contentExtract,
          },
        })
      }
    },

    // ═══════════════════════════════════════════════════════════
    // PROTECTION: block direct filesystem edits on .cognitive/
    // ═══════════════════════════════════════════════════════════

    "tool.execute.before": async (input, output) => {
      const tool = input.tool
      const sid = input.sessionID || ""

      // TRACE: ensure session_start on first tool call of a session
      if (sid) await ensureSessionStart(sid, "")

      // Track tool start time for structured tool_call events
      if (sid && input.callID) {
        if (!sessionToolStarts.has(sid)) sessionToolStarts.set(sid, new Map())
        sessionToolStarts.get(sid).set(input.callID, {
          startTime: Date.now(),
          tool,
          input: output?.args || input?.args || {},
        })
      }

      // CPG CONTEXT: antes de leer código, consulta el CPG por ese archivo y
      // deja el resumen listo para anexar en tool.execute.after (misma callID).
      // El read no se bloquea ni se retrasa si el CPG no está indexado.
      if (tool === "read" && !output?.args?.filePath?.includes("cognitive/")) {
        const fp = output?.args?.filePath || input?.args?.filePath || ""
        if (fp) await queryCpgForFile(input.callID, fp)
      }

      // CONTEXT GATES (ADR-WALKER-018): para tools de acción/símbolo, ejecuta
      // las gates de memorias (annotate — nunca bloquea) y deja el resumen para
      // anexar al output. Cache por sesión para no re-consultar gates ya pasadas.
      if (GATES[tool]) {
        const ctx = {
          filePath: output?.args?.filePath || input?.args?.filePath || "",
          directory: output?.args?.directory || input?.args?.directory || "",
          pattern: output?.args?.pattern || input?.args?.pattern || input?.args?.query || input?.args?.cmd || "",
        }
        await runGatesForTool(tool, ctx, input.callID, input.sessionID)
      }

      // Si el grep se ejecuta dentro de bash (no como tool grep), aplica las
      // gates de grep también — el patrón se extrae del comando.
      if (tool === "bash") {
        const cmd = String(output?.args?.command || input?.args?.command || "")
        if (/^\s*(grep|rg|rgrep)\b/.test(cmd)) {
          const m = cmd.match(/^\s*(?:grep|rg|rgrep)\b[^'"|]*['"]([^'"]+)['"]/)
          await runGatesForTool(
            "grep",
            { pattern: m ? m[1] : cmd.replace(/^\s*(?:grep|rg|rgrep)\b\s*/, "").slice(0, 40), filePath: "", directory: "" },
            input.callID,
            input.sessionID
          )
        }
      }

      // Block edit / write / read on .cognitive/ (read solo via walker recall_knowledge)
      if (WALKER_GUARD_ENABLED && (tool === "edit" || tool === "write" || tool === "read")) {
        const fp = output?.args?.filePath || input?.args?.filePath || ""
        if (isProtected(fp)) {
          throw new Error(
            TEMPLATE(
              tool,
              `path "${fp}"`,
              "cognitive data must be accessed through walker MCP tools only",
              ALLOWED_TOOLS
            )
          )
        }
      }

      // Block destructive bash on .cognitive/
      if (WALKER_GUARD_ENABLED && tool === "bash") {
        const cmd = String(output?.args?.command || input?.args?.command || "")

        // Block inline Python — force walker MCP tool usage
        // Cubre: python3 -c, heredocs (<<, <<<, - <<), Y python3 <script.py>
        if (
          /python3?\s+-c\b|python3?\s+-\s*<<|python3?\s+<<|python3?\s+<<</.test(cmd) ||
          /python3?\s+\/[^\s]+\.py\b/.test(cmd)  // python3 /path/script.py
        ) {
          throw new Error(
            TEMPLATE(
              "python3",
              "inline execution",
              "all scripting must go through walker MCP tools",
              ALLOWED_TOOLS + ", python3 -m pytest (testing only)"
            )
          )
        }

        // Block destructive commands on .cognitive/ (allow read-only sin redirección)
        if (isProtected(cmd)) {
          if (!isReadOnlyCmd(cmd)) {
            throw new Error(
              TEMPLATE(
                "bash",
                `.cognitive/ path`,
                "cognitive data must be modified through walker MCP tools only",
                "cat, grep, find, ls, stat (read-only, sin > ni tee) | " + ALLOWED_TOOLS
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
      // Push structured session_end para cada sesión activa
      for (const sid of sessionStartTimes.keys()) {
        await pushSessionEnd(sid)
      }
      sessionStartTimes.clear()
      sessionToolStarts.clear()
      processedMessageIds.clear()

      // Keep CKG session_end for backward compatibility
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

    "tool.execute.after": async (input, output) => {
      const tool = input?.tool
      if (!tool) return

      // TRACE: push structured tool_call event
      const sid = input.sessionID || ""
      const cid = input.callID || ""
      const toolStart = sid && cid ? sessionToolStarts.get(sid)?.get(cid) : null
      if (toolStart) {
        const durationMs = Date.now() - toolStart.startTime
        await tracePush({
          event_type: "tool_call",
          source: "agent_hook",
          session_id: sid,
          tool,
          input: toolStart.input,
          output: output?.output || "",
          started_at: new Date(toolStart.startTime).toISOString(),
          finished_at: new Date().toISOString(),
          duration_ms: durationMs,
        })
        sessionToolStarts.get(sid)?.delete(cid)
      }

      // CPG CONTEXT: anexa el resumen del CPG al output del read para que el
      // modelo vea qué funciones/flows del archivo ya existen (sin re-leer).
      if (tool === "read") {
        const ctx = pendingCpgByCall.get(input.callID)
        if (ctx && output && typeof output.output === "string") {
          output.output = `${output.output}\n\n[WALKER] CPG context (estructura indexada):\n${ctx}`
        }
        pendingCpgByCall.delete(input.callID)
        pendingGatesByCall.delete(input.callID)
        return
      }

      // CONTEXT GATES (ADR-WALKER-018): anexa el resumen de las gates de
      // memorias al output de cualquier tool de acción (grep/glob/edit/write/bash).
      const gatesCtx = pendingGatesByCall.get(input.callID)
      if (gatesCtx && output && typeof output.output === "string") {
        output.output = `${output.output}\n\n${gatesCtx}`
      }
      pendingGatesByCall.delete(input.callID)

      if (!["edit", "write", "bash"].includes(tool)) return

      // CKG tool_trace eliminado (ADR-WALKER-018): ahora el trace estructurado
      // vive en el índice trace via trace_push. No spammear CKG con tool_trace.
    },

    // ═══════════════════════════════════════════════════════════
    // COMPACTION: inject walker context into prompt
    // ═══════════════════════════════════════════════════════════

    "experimental.session.compacting": async (input, output) => {
      let ckgInfo = ""
      try {
        // Contar nodos via walker recall_knowledge (no python directo) — args array.
        const res = await runWalker("recall_knowledge", JSON.stringify({ index: "ckg", query: { text: "" } }))
        ckgInfo = `\n- CKG: consultado via recall_knowledge (ver walker memory_schema para conteo)\n`
      } catch { /* ignore */ }

      output.context.push(
`## Walker Protocol (auto-injected)\n` +
        `- Project: ${PROJECT_ID} (features/project DDD Clean Architecture)${ckgInfo}\n` +
        `- ADR-WALKER-013: DeltaGraph as single source of truth for spring state\n` +
        `- ADR-WALKER-014: plan.json / progress.json eliminated\n` +
        `- ADR-WALKER-015: features/project/ migrated to DDD Clean Architecture\n` +
        `- ADR-WALKER-016: cli_interface → Bounded Context 'Protocol' (DIP: libs no importa features)\n` +
        `- ADR-WALKER-018: Context Gates — cada tool de acción (grep/glob/read/edit/write/bash) ejecuta un checklist de memorias (annotate).\n` +
        `\n` +
        `### CONTEXT GATES (impuesto por el hook — ADR-WALKER-018)\n` +
        `The hook auto-injects, before each action tool, a checklist like:\n` +
        `  Before <tool>, execute this checklist gates:\n` +
        `    [x] search_memories  → hits indexados (CPG/CKG/Delta)\n` +
        `    [x] memory_schema → índices disponibles\n` +
        `Use that injected gate result as your PRIMARY context. Only if the gates\n` +
        `return no hits should you fall back to grep/read on disk.\n` +
        `\n` +
        `### MEMORY QUERIES (available on demand)\n` +
        `1. walker_memory_schema → discover available indices (ckg, cpg, delta, trace)\n` +
        `2. walker_recall_knowledge → query CKG (knowledge, ADRs, decisions, patterns, conventions)\n` +
        `3. walker_search_memories → federated query across CPG + CKG + Delta + Trace\n` +
        `4. walker_code_context → structured code context by file/query (symbols, calls, imports)\n` +
        `5. walker_inspect_codebase → architecture, dead code, security review via CPG\n` +
        `6. walker_agent_context → consolidated session context (delta+ckg+cpg+trace)\n` +
        `7. walker_run_tests → execute the test suite (fail-loud, pytest)\n` +
        `8. walker_verify_predicates → check task DoD predicates (VERIFY phase)\n` +
        `9. walker_sprint_planning → sprint ceremonies and evidence (SPRINT.PLANNING phase)\n` +
        `10. walker_capture → capture evidence and artifacts into the active sprint\n` +
        `\n` +
        `### PHASE TOOLS (MANDATORY per workflow phase)\n` +
        `Use these structured tools at each phase:\n` +
        `- IMPLEMENT: walker_code_context before editing\n` +
        `- REFINE: walker_agent_context when task fails (consolidated context for diagnosis)\n` +
        `- VERIFY: walker_run_tests + walker_verify_predicates to check coverage\n` +
        `- SPRINT.PLANNING: walker_sprint_planning to gather evidence\n` +
        `\n` +
        `### STRICT RULES\n` +
        `- FORBIDDEN: python3 -c / python3 << / python3 script.py → use walker MCP tools\n` +
        `- FORBIDDEN: direct reads/edits of .cognitive/ → use walker MCP tools\n` +
        `- FORBIDDEN: echo/cat con > >> tee hacia .cognitive/ o data/*.json.gz\n` +
        `- walker_documenting_knowledge → register ADRs and conventions after architectural decisions\n` +
        `- walker_submit / walker_transition → navigate spring workflow\n` +
        `- walker_capture → capture specs into active sprint\n` +
        `- CONVENTIONS: chequear walker_recall_knowledge type=convention y cumplirlas\n`
      )
    },
  }
}
