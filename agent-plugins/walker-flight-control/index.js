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
const MAX_AUTO_STEPS = 3
// Negación de escrituras: OPT-IN. Con el flag apagado el plugin solo avisa.
// Se enciende para medir cuánto traba, no como default (una sesión en un
// proyecto registrado no podría editar hasta completar el paso pendiente).
const DENY = process.env.WALKER_FLIGHT_CONTROL_DENY === "1"
// `shell` incluido: sin él el agente escribe con `echo >> archivo` y el gate
// de `edit`/`write` no contiene nada (medido: 1/1 bypass por shell).
const MUTATING_TOOLS = ["edit", "write", "shell"]

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
  const proj = projectId || "<proyecto>"
  if (state.required.length) {
    parts.push(`Campos requeridos: ${state.required.join(", ")}.`)
    const example = state.required.map((f) => `"${f}":"..."`).join(",")
    parts.push(
      `Complete el paso ANTES de escribir código: ${CLI} ${proj}/submit '{${example}}'.`
    )
  } else {
    parts.push(`Complete el paso: ${CLI} ${proj}/submit '{}'.`)
  }
  if (state.gates.length) {
    parts.push(
      `Después, avance con: ${CLI} ${proj}/transition '{"gate":"${state.gates[0]}"}'. Salidas: ${state.gates.join(", ")}.`
    )
  }
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
      data: { project_id: projectId, directory: projectDir },
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

    const readState = () => {
      const data = runTool("workflow_state", {})
      if (!data || data.ok === false) return null
      return {
        position: data.current_position || "",
        execType: data.execution_type || "",
        required: Array.isArray(data.required_fields) ? data.required_fields : [],
        satisfied: data.step_satisfied !== false,
        gates: Array.isArray(data.available_gates) ? data.available_gates : [],
        revision: data.revision ?? 0,
      }
    }

    // Los sub-estados `execution: auto` los corre el MOTOR, no el modelo: no
    // tiene sentido esperar a que el agente se acuerde de submitear un paso
    // que no requiere criterio. Hasta ahora nadie los disparaba, así que todos
    // los proyectos quedaban clavados en `START.LIST` (medido: 7/7).
    const driveAuto = (current) => {
      let cur = current
      for (let i = 0; i < MAX_AUTO_STEPS; i++) {
        if (!cur || (cur.execType !== "auto" && cur.execType !== "bootstrap")) break
        const res = runTool("advance", {})
        if (!res || res.ok === false) break
        cur = readState() || cur
      }
      return cur
    }

    const refresh = () => {
      if (Date.now() - state.fetchedAt < REFRESH_MS) return state.data
      state.data = driveAuto(readState())
      state.fetchedAt = Date.now()
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
            data: {
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
              data: { injections: entry.injections, position: entry.position },
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
        if (!DENY) return
        for (const name of MUTATING_TOOLS) {
          const tool = editor.get(name)
          if (!tool || typeof tool.execute !== "function") continue
          editor.update(name, (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const cur = state.data
              if (!cur || cur.execType !== "llm_task" || cur.satisfied) {
                return original(input, callCtx)
              }
              const fields = cur.required.length
                ? cur.required.map((f) => `"${f}":"..."`).join(",")
                : ""
              runTool("trace_push", {
                event_type: "flight_control_denied",
                source: "agent_hook",
                data: { tool: name, position: cur.position, required: cur.required },
              })
              throw new Error(
                `[WALKER] Paso pendiente: ${cur.position}. Completalo antes de ` +
                  `escribir código: ${CLI} ${projectId}/${"submit"} '{${fields}}'. ` +
                  `Después avanzá con: ${CLI} ${projectId}/transition '{"gate":"${(cur.gates || [])[0] || ""}"}'.`
              )
            }
          })
        }
      })
    } catch {
      // sin transform no hay gate
    }

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
