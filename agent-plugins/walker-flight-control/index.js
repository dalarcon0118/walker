/**
 * walker flight-control (OpenCode V2, global) — binding + estado del workflow.
 *
 * Instalado globalmente: corre en CUALQUIER sesión y resuelve el proyecto por
 * la ubicación de la sesión (`project_for_path` sobre el registro de walker).
 * Antes estaba solo en `.opencode/plugins/` de walker, así que una sesión en
 * otro repo (p. ej. game_reset) no recibía nada.
 *
 * Qué hace (fase 1+2, sin bloquear):
 *   - resuelve {project_id, repo_path} desde `ctx.location.directory`;
 *   - traza `flight_control_active` al cargar (señal de que el hook vive);
 *   - en cada llamada al modelo inyecta `[WALKER] Workflow: …` en las
 *     instrucciones del sistema (`session.hook("context")`);
 *   - al terminar la ejecución traza `workflow_state_injected` /
 *     `workflow_suggestion_ignored` en el trace DEL PROYECTO de la sesión.
 *
 * Escape hatch: WALKER_FLIGHT_CONTROL=0.
 */
import { execFileSync } from "node:child_process"

const WALKER_ROOT = "/Users/davidmartinez/develop/Active-projects/walker"
const CLI = `${WALKER_ROOT}/bin/walker3`
const ENABLED = process.env.WALKER_FLIGHT_CONTROL !== "0"
const REFRESH_MS = 60_000

let projectId = process.env.WALKER_PROJECT_ID || ""
let projectDir = ""

function runTool(tool, payload = {}, project = projectId) {
  try {
    const stdout = execFileSync(
      CLI,
      [`${project || "_"}/${tool}`, JSON.stringify(payload)],
      {
        cwd: WALKER_ROOT,
        encoding: "utf8",
        timeout: 30_000,
        maxBuffer: 8 * 1024 * 1024,
        stdio: ["ignore", "pipe", "ignore"],
      }
    )
    return JSON.parse(stdout)
  } catch {
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

    projectDir = ctx.location?.directory || ""
    if (!projectId && projectDir) {
      const found = runTool("project_for_path", { path: projectDir }, "_")
      if (found?.ok && found.project_id) projectId = found.project_id
    }

    // Señal de vida: sin esto no se distingue "el hook no cargó" de "no había
    // estado de workflow que inyectar".
    runTool("trace_push", {
      event_type: "flight_control_active",
      source: "agent_hook",
      metadata: { project_id: projectId, directory: projectDir },
    })

    if (!projectId) return // sin proyecto registrado no se inyecta nada

    const state = { data: null, fetchedAt: 0 }
    const sessions = new Map()
    const controller = new AbortController()

    const sessionOf = (sessionID) => {
      const key = sessionID || "global"
      if (!sessions.has(key)) {
        sessions.set(key, {
          injections: 0,
          followed: false,
          position: "",
          sessionID: sessionID || "",
        })
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

    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({
          types: [
            "session.execution.succeeded",
            "session.execution.failed",
            "session.execution.interrupted",
          ],
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
