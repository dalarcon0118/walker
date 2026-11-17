/**
 * walker flight-control (OpenCode V2) — binding + estado del workflow inyectado.
 *
 * Por qué V2: el plugin anterior (`legacy/walker-session.v1.js`) exportaba
 * `WalkerHooks` como named export; OpenCode V2 exige `export default { id,
 * setup }` ("Plugin must export a default definition with an id and an effect or
 * setup function") y lo descartaba al cargar. Todas las llamadas del plugin a
 * walker eran, en la práctica, código muerto.
 *
 * Qué hace (fase 1+2, sin bloquear):
 *   - al iniciar, resuelve el estado del workflow con el CLI de walker;
 *   - en cada llamada al modelo inyecta un bloque `[WALKER] Workflow: …` en las
 *     instrucciones del sistema (`session.hook("context")`);
 *   - al terminar la sesión traza cuántas inyecciones hubo y si hubo
 *     `submit`/`transition` (para medir cumplimiento antes de gatear).
 *
 * Escape hatch: WALKER_FLIGHT_CONTROL=0 desactiva todo.
 */
import { execFileSync } from "node:child_process"

const WALKER_ROOT = "/Users/davidmartinez/develop/Active-projects/walker"
const CLI = `${WALKER_ROOT}/bin/walker3`
const PROJECT_ID = process.env.WALKER_PROJECT_ID || "walker"
const ENABLED = process.env.WALKER_FLIGHT_CONTROL !== "0"
const REFRESH_MS = 60_000

function runTool(tool, payload = {}) {
  try {
    const stdout = execFileSync(CLI, [`${PROJECT_ID}/${tool}`, JSON.stringify(payload)], {
      cwd: WALKER_ROOT,
      encoding: "utf8",
      timeout: 30_000,
      maxBuffer: 8 * 1024 * 1024,
      stdio: ["ignore", "pipe", "ignore"],
    })
    return JSON.parse(stdout)
  } catch (e) {
    return null // fail-open: el agente sigue aunque walker no responda
  }
}

function workflowBlock(state) {
  if (!state?.position) return ""
  const parts = [`[WALKER] Workflow: ${state.position} (${state.execType}).`]
  if (state.required.length) parts.push(`Campos requeridos: ${state.required.join(", ")}.`)
  if (state.gates.length) parts.push(`Salidas disponibles: ${state.gates.join(", ")}.`)
  parts.push(
    "Antes de avanzar, confirmá el estado con `workflow_state`; completá el paso actual con `submit` (o `transition` si corresponde)."
  )
  return parts.join(" ")
}

export default {
  id: "walker-flight-control",

  async setup(ctx) {
    if (!ENABLED) return

    const state = { data: null, fetchedAt: 0 }
    const sessions = new Map() // sessionID -> {injections, followed, position}
    const controller = new AbortController()

    const sessionOf = (sessionID) => {
      const key = sessionID || "global"
      if (!sessions.has(key)) {
        sessions.set(key, { injections: 0, followed: false, position: "", sessionID: sessionID || "" })
      }
      return sessions.get(key)
    }

    const refresh = () => {
      if (Date.now() - state.fetchedAt < REFRESH_MS) return state.data
      const data = runTool("workflow_state", {})
      if (data && data.ok !== false) {
        state.data = {
          position: data.current_position || "",
          execType: data.execution_type || "",
          required: Array.isArray(data.required_fields) ? data.required_fields : [],
          gates: Array.isArray(data.available_gates) ? data.available_gates : [],
          revision: data.revision ?? 0,
        }
        state.fetchedAt = Date.now()
      }
      return state.data
    }

    // Inyección de estado en las instrucciones del sistema, por llamada al modelo.
    try {
      await ctx.session.hook("context", (event) => {
        const block = workflowBlock(refresh())
        if (!block) return
        const sid = event.sessionID || event.sessionId || ""
        event.system.push({ type: "text", text: block })
        const entry = sessionOf(sid)
        entry.injections += 1
        entry.position = state.data.position
      })
    } catch {
      // Sin el hook tipado no se inyecta; el plugin no debe romper la sesión.
    }

    // Al terminar la ejecución, trazar el resultado de la sesión (métrica).
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({
          types: ["session.execution.succeeded", "session.execution.failed"],
          signal: controller.signal,
        })) {
          const sid =
            event.sessionID ||
            event.sessionId ||
            event.data?.sessionID ||
            event.data?.sessionId ||
            event.data?.info?.sessionID ||
            ""
          const entry = sessionOf(sid)
          if (entry.injections === 0) continue
          runTool("trace_push", {
            event_type: "workflow_state_injected",
            source: "agent_hook",
            session_id: entry.sessionID,
            metadata: {
              injections: entry.injections,
              position: entry.position,
              followed: entry.followed,
              outcome: event.type,
            },
          })
          if (!entry.followed) {
            runTool("trace_push", {
              event_type: "workflow_suggestion_ignored",
              source: "agent_hook",
              session_id: entry.sessionID,
              metadata: { injections: entry.injections, position: entry.position },
            })
          }
          sessions.delete(sid || "global")
        }
      } catch {
        // stream cerrado al descargar el plugin
      }
    })()

    // `submit`/`transition` marcan la sugerencia como seguida. En V2 se envuelve
    // el execute de la tool MCP con un transform.
    try {
      await ctx.tool.transform((editor) => {
        for (const name of ["walker_submit", "walker_transition", "submit", "transition"]) {
          const tool = editor.get(name)
          if (!tool || typeof tool.execute !== "function") continue
          const original = tool.execute
          editor.update(name, (draft) => {
            draft.execute = async (input, callCtx) => {
              sessionOf(callCtx?.sessionID || "").followed = true
              return original(input, callCtx)
            }
          })
        }
      })
    } catch {
      // Si el transform no está disponible, se mide solo la inyección.
    }

    return () => controller.abort()
  },
}
