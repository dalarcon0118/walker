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
// Tarea en curso (la declara `implement_task`). El gate solo aplica acá: sin
// tarea declarada, una sesión ad-hoc no se bloquea (medido: bloquear todo
// costaba ~7x tokens en un cambio de una línea).
let activeTask = null
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

function bootstrapBlock() {
  // Se manda UNA vez por sesión y se apaga. Sin esto el plugin queda mudo para
  // siempre (la inyección normal exige tarea declarada) y nadie declara nada.
  // Da permiso explícito a ignorarlo: si no, el aviso empuja al agente a
  // caminar el workflow en una consulta o un cambio ad-hoc.
  const proj = projectId || "<proyecto>"
  return (
    `[WALKER] Si esta sesión es una IMPLEMENTACIÓN de código, declarala una vez: ` +
    `${CLI} ${proj}/implement_task '{"task_id":"...","objective":"...","files":["..."]}'. ` +
    `Si es una consulta, una revisión o un cambio ad-hoc, ignorá este mensaje y seguí normal.`
  )
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
          bootstrapped: false,
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
        // El motor se conduce igual (los `auto` corren sin tokens), pero el
        // texto solo se inyecta cuando hay una TAREA declarada. Medido: la
        // inyección en sesiones ad-hoc hacía que el agente caminara el
        // workflow por su cuenta (4 → 15 pasos sin que nada lo exigiera).
        refresh()
        const sid = event.sessionID || event.sessionId || ""
        const entry = sessionOf(sid)
        let block = ""
        if (activeTask) {
          block = workflowBlock(state.data)
        } else if (!entry.bootstrapped) {
          block = bootstrapBlock()
          entry.bootstrapped = true
        }
        if (!block) return
        event.system.push({ type: "text", text: block })
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
              if (!cur || cur.execType !== "llm_task" || cur.satisfied || !activeTask) {
                return original(input, callCtx)
              }
              // Si la tarea declaró archivos, el gate solo cubre esos archivos.
              const path = String(input?.path || input?.filePath || input?.file || "")
              const files = activeTask.files || []
              if (path && files.length && !files.some((f) => path.endsWith(f))) {
                return original(input, callCtx)
              }
              const fields = cur.required.length
                ? cur.required.map((f) => `"${f}":"..."`).join(",")
                : ""
              const gate = (cur.gates || [])[0] || ""
              runTool("trace_push", {
                event_type: "flight_control_denied",
                source: "agent_hook",
                data: { tool: name, position: cur.position, required: cur.required },
              })
              // El mensaje tiene que ser AUTOSUFICIENTE: en el probe anterior el
              // agente gastó 19 de 24 pasos leyendo el fuente del plugin para
              // entender el bloqueo. Todo lo que necesita va acá.
              throw new Error(
                `[WALKER] Escritura bloqueada. El workflow del proyecto está en ` +
                  `${cur.position} y falta registrar: ${cur.required.join(", ") || "(sin campos)"}.\n` +
                  `Es una regla del proyecto, no un bug: no hace falta leer el código del gate.\n` +
                  `Para desbloquear, en DOS llamadas:\n` +
                  `  1) ${CLI} ${projectId}/submit '{${fields}}'\n` +
                  `  2) ${CLI} ${projectId}/transition '{"gate":"${gate}"}'\n` +
                  `Qué poner en cada campo: el contenido real que exige el paso ` +
                  `(para \`preconditions\`, premisas verificables: comando => resultado; ` +
                  `para \`loop_verification\`, comando => salida).\n` +
                  `Después repetí exactamente la herramienta que querías usar y seguirá normal.`
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
        const taskTool = editor.get("implement_task")
        if (taskTool && typeof taskTool.execute === "function") {
          editor.update("implement_task", (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const result = await original(input, callCtx)
              activeTask = {
                taskId: String(input?.task_id || ""),
                files: Array.isArray(input?.files) ? input.files.map(String) : [],
              }
              return result
            }
          })
        }
      })
    } catch {
      // sin transform, el gate no se acota por tarea
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
              const result = await original(input, callCtx)
              // El estado cambió: sin invalidar, el gate sigue negando con el
              // `satisfied: false` cacheado hasta REFRESH_MS y el agente reintenta
              // a ciegas (medido: 4 denegaciones seguidas después de submitear).
              state.fetchedAt = 0
              return result
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
