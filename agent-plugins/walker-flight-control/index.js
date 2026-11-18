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
import { appendFileSync } from "node:fs"

const WALKER_ROOT = "/Users/davidmartinez/develop/Active-projects/walker"
const CLI = `${WALKER_ROOT}/bin/walker3`
const ENABLED = process.env.WALKER_FLIGHT_CONTROL !== "0"
const REFRESH_MS = 60_000
const MAX_AUTO_STEPS = 3
// Negación de escrituras: OPT-IN. Con el flag apagado el plugin solo avisa.
// Se enciende para medir cuánto traba, no como default (una sesión en un
// proyecto registrado no podría editar hasta completar el paso pendiente).
// Lo único que movió la aguja fue negar; queda opt-in por costo. Se puede apagar con WALKER_FLIGHT_CONTROL_INTAKE=0.
// Gate de INTAKE: niega escribir hasta que la tarea esté declarada. Es barato
// (una llamada y se abre). `setup` corre una vez por LOCATION: el estado vive en
// `rt` (por setup), no en globales — se midió una sesión en walker bloqueada con
// "game_reset/implement_task" porque el projectId era compartido.
// Sigue opt-in hasta tener tests del bindeo multi-location.
const DENY_INTAKE = process.env.WALKER_FLIGHT_CONTROL_INTAKE === "1"
// Gate de PASOS: además exige completar el sub-estado del workflow. Cuesta ~7x
// en tareas triviales, así que queda opt-in.
const DENY = process.env.WALKER_FLIGHT_CONTROL_DENY === "1"
// `shell` incluido: sin él el agente escribe con `echo >> archivo` y el gate
// de `edit`/`write` no contiene nada (medido: 1/1 bypass por shell).
const MUTATING_TOOLS = ["edit", "write", "shell"]

// OJO: NO hay estado module-global. `setup` corre UNA VEZ POR UBICACIÓN en el
// mismo proceso (medido: tres setups para tres worktrees), así que una variable
// global hacía que la sesión de un proyecto usara el projectId de otro — bloqueó
// una sesión en walker con "game_reset/implement_task". Todo el estado vive en
// `rt`, creado en `runSetup`.

function runTool(tool, payload = {}, project = "") {
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
  } catch (err) {
    // Fail-open (el agente sigue) pero visible: sin traza, un CLI roto parece
    // "walker no tenía nada que decir" en vez de "walker no respondió".
    degrade(null, `cli falló: ${tool}`, err)
    return null
  }
}

// Degradación observable: si una parte del plugin no puede engancharse, no se
// rompe la sesión (ya pasó una vez con `state.data` nulo) pero tampoco se
// calla: se acumula y se traza cuando el proyecto está resuelto.
// `rt` puede faltar: `runTool` y el catch de `setup` no lo tienen a mano. Antes
// se pasaba `null` y el `return` temprano se tragaba el fallo entero — sin
// traza, sin archivo, sin consola. Un fallo sin rastro es indistinguible de
// "no pasó nada", que es el peor estado para diagnosticar.
// ORPHAN es solo diagnóstico: no guarda estado de proyecto, así que no reabre
// el bug multi-location que motivó sacar los globales.
const ORPHAN = { degraded: new Set(), pendingDegrades: [], projectId: "" }

function degrade(rt, what, err) {
  const target = rt || ORPHAN
  const detail = String((err && err.message) || err || "").slice(0, 200)
  const key = `${what}|${detail}`
  if (target.degraded.has(key)) return
  target.degraded.add(key)
  target.pendingDegrades.push({ what, detail })
  // Si el CLI es lo que falla, no hay proyecto donde trazar: queda un archivo
  // como última evidencia (bajo demanda, una línea por tipo de fallo).
  try {
    appendFileSync("/tmp/walker-plugin-degraded.log", `${new Date().toISOString()} ${key}\n`)
  } catch {}
}

function flushDegrades(rt) {
  if (!rt || !rt.pendingDegrades.length || !rt.projectId) return
  const batch = rt.pendingDegrades
  rt.pendingDegrades = []
  for (const d of batch) {
    runTool("trace_push", {
      event_type: "plugin_degraded",
      source: "agent_hook",
      session_id: rt.sessionId || "",
      data: d,
    }, rt.projectId)
  }
}

function relPath(file, rt) {
  // El delta y el CPG trabajan con rutas relativas al proyecto; el tool recibe
  // la ruta absoluta de la sesión (y en macOS /tmp vs /private/tmp difiere).
  const p = String(file || "")
  if (!p) return ""
  const base = String(rt?.projectDir || "").replace(/\/+$/, "")
  if (base && p.startsWith(base + "/")) return p.slice(base.length + 1)
  return p
}

function firstUserText(event) {
  // `event.messages` trae la conversación; el pedido original es el PRIMER
  // mensaje de rol user (el último puede ser un seguimiento o un tool result).
  const msgs = event?.messages || []
  const user = msgs.find((m) => (m?.role || "") === "user")
  const content = user?.content
  const parts = Array.isArray(content) ? content : (content ? [content] : [])
  const text = parts
    .map((p) => (typeof p === "string" ? p : p?.type === "text" ? p.text : ""))
    .filter(Boolean)
    .join(" ")
  return String(text).replace(/\s+/g, " ").trim().slice(0, 240)
}

function taskIdFor(sessionID) {
  const clean = String(sessionID || "ses").replace(/[^a-zA-Z0-9]/g, "")
  return `auto-${clean.slice(-10) || Date.now()}`
}

function baselineBlock(r, file, rt) {
  const cpg = r?.cpg || {}
  const rel = cpg.relations || {}
  const syms = (cpg.symbols || []).filter((s) => s.cyclo)
  syms.sort((a, b) => (b.cyclo || 0) - (a.cyclo || 0))
  const callers = (rel.called_by || []).slice(0, 5).map((x) => x.name).filter(Boolean)
  const parts = [
    `[WALKER] PUNTO A (baseline) — ${file}`,
    `  impacto: ${rel.called_by_total ?? (rel.called_by || []).length} llamadores · ` +
      `${rel.calls_total ?? (rel.calls || []).length} llamadas salientes · ` +
      `${(cpg.imports || []).length} imports`,
  ]
  if (syms.length) {
    parts.push(`  peso: ${syms.slice(0, 4).map((s) => `${s.name}(${s.cyclo})`).join(", ")}`)
  }
  if (callers.length) parts.push(`  quién lo usa: ${callers.join(", ")}`)
  const ckg = (r?.ckg?.results || []).length
  if (ckg) parts.push(`  CKG: ${ckg} decisión(es)/convención(es) del scope`)
  parts.push(`  detalle: ${CLI} ${rt?.projectId || "<proyecto>"}/agent_context '{"file":"${file}"}'`)
  return parts.join("\n")
}

function preflightBlock(pf) {
  if (!pf || pf.error) return ""
  const lines = ["[WALKER] PREFLIGHT — antes de escribir"]
  const base = pf.baseline || {}
  const cx = base.complexity || {}
  if (base.structure) {
    lines.push(
      `  scope ${base.scope || "(proyecto)"}: ${base.structure.files || "?"} archivos · ` +
        `${base.structure.functions || "?"} funciones · cyclo max ${cx.max ?? "?"}`
    )
  }
  for (const f of (pf.files || []).slice(0, 5)) {
    const heavy = (f.heaviest || []).map((h) => `${h.name}(${h.cyclo})`).join(", ")
    lines.push(
      `  ${f.file}: ${f.symbols ?? "?"} símbolos · ${f.callers ?? "?"} llamadores · ` +
        `${f.calls ?? "?"} llamadas${heavy ? ` · pesado: ${heavy}` : ""}`
    )
    if ((f.top_callers || []).length) lines.push(`    lo usan: ${f.top_callers.join(", ")}`)
  }
  if ((pf.tests || []).length) lines.push(`  tests que lo cubren: ${pf.tests.slice(0, 4).join(", ")}`)
  if ((pf.unmapped || []).length) {
    lines.push(`  sin verificar: ${pf.unmapped.map((u) => u.criterion || u.reason).join("; ")}`)
  }
  return lines.join("\n")
}

function bootstrapBlock(rt) {
  // Se manda UNA vez por sesión y se apaga. Sin esto el plugin queda mudo para
  // siempre (la inyección normal exige tarea declarada) y nadie declara nada.
  // Da permiso explícito a ignorarlo: si no, el aviso empuja al agente a
  // caminar el workflow en una consulta o un cambio ad-hoc.
  const proj = rt?.projectId || "<proyecto>"
  return (
    `[WALKER] Si esta sesión es una IMPLEMENTACIÓN de código, declarala una vez: ` +
    `${CLI} ${proj}/implement_task '{"task_id":"...","objective":"...","files":["..."]}'. ` +
    `Si es una consulta, una revisión o un cambio ad-hoc, ignorá este mensaje y seguí normal.`
  )
}

function workflowBlock(state, rt) {
  if (!state?.position) return ""
  const parts = [`[WALKER] Workflow: ${state.position} (${state.execType || ""}).`]
  const proj = rt?.projectId || "<proyecto>"
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
    try {
      await runSetup(ctx)
    } catch (err) {
      // Última red: cualquier error inesperado del plugin se registra y se corta.
      // `rt` vive dentro de runSetup y no está disponible aquí; degrade lo acepta
      // nulo vía ORPHAN. Sin proyecto no hay TraceGraph donde trazar, así que
      // además queda en consola.
      degrade(null, "setup crashed", err)
      console.error("[walker-flight-control] setup crashed:", (err && err.stack) || err)
    }
  },
}

async function runSetup(ctx) {
    if (!ENABLED) return

    // Estado POR UBICACIÓN. `setup` corre una vez por location en el mismo
    // proceso; compartir esto entre ubicaciones fue el bug que hizo que una
    // sesión en walker bloqueara con el rt.projectId de game_reset.
    const rt = {
      projectId: process.env.WALKER_PROJECT_ID || "",
      projectDir: ctx?.location?.directory || ctx?.directory || "",
      sessionId: "",
      activeTask: null,
      sessionGoal: "",
      pendingBaseline: null,
      baselineDone: false,
      degraded: new Set(),
      pendingDegrades: [],
    }
    const run = (tool, payload = {}, project = rt.projectId) => runTool(tool, payload, project)

    if (rt.projectDir) {
      const found = run("project_for_path", { path: rt.projectDir }, "_")
      if (found?.ok && found.project_id) rt.projectId = found.project_id
    }

    // Señal de vida: sin esto no se distingue "el hook no cargó" de "no había
    // estado de workflow que inyectar".
    run("trace_push", {
      event_type: "flight_control_active",
      source: "agent_hook",
      data: { project_id: rt.projectId, directory: rt.projectDir },
    })

    if (!rt.projectId) {
      // Sin proyecto no hay dónde trazar; se calla a propósito (no es un fallo).
      return
    }
    flushDegrades(rt)

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
      const data = run("workflow_state", {})
      if (!data || data.ok === false) return null
      return {
        position: data.current_position || "",
        execType: data.execution_type || "",
        required: Array.isArray(data.required_fields) ? data.required_fields : [],
        satisfied: data.step_satisfied !== false,
        gates: Array.isArray(data.available_gates) ? data.available_gates : [],
        taskId: String(data.task_id || ""),
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
        const res = run("advance", {})
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
      if (!ctx?.session?.hook) {
        degrade(rt, "session.hook missing", "el runtime no expone el hook de contexto")
      } else await ctx.session.hook("context", (event) => {
        // El motor se conduce igual (los `auto` corren sin tokens), pero el
        // texto solo se inyecta cuando hay una TAREA declarada. Medido: la
        // inyección en sesiones ad-hoc hacía que el agente caminara el
        // workflow por su cuenta (4 → 15 pasos sin que nada lo exigiera).
        refresh()
        const sid = event.sessionID || event.sessionId || ""
        rt.sessionId = rt.sessionId || sid
        const entry = sessionOf(sid)
        if (!rt.sessionGoal) rt.sessionGoal = firstUserText(event)
        // Preflight en el PRIMER turno si ya hay tarea declarada: no depende de
        // que el agente use `read` (puede leer con `execute`, y entonces el
        // hook de lectura nunca dispara — medido).
        if (!rt.baselineDone && rt.projectId && state.data?.taskId) {
          rt.baselineDone = true
          const taskCtx = run("agent_context", { task_id: state.data.taskId })
          const files = (taskCtx?.anchor?.files || []).filter(Boolean)
          if (files.length) {
            const pf = run("preflight", { files })
            const text = preflightBlock(pf)
            if (text) {
              rt.pendingBaseline = text
              run("trace_push", {
                event_type: "intake_preflight",
                source: "agent_hook",
                session_id: sid,
                files,
                data: { task_id: state.data.taskId, files },
              })
            }
          }
        }
        if (rt.pendingBaseline) {
          event.system.push({ type: "text", text: rt.pendingBaseline })
          rt.pendingBaseline = null
        }
        let block = ""
        if (rt.activeTask) {
          block = workflowBlock(state.data, rt)
        } else if (!entry.bootstrapped) {
          block = bootstrapBlock(rt)
          entry.bootstrapped = true
        }
        if (!block) return
        if (!Array.isArray(event?.system)) {
          degrade(rt, "event.system no es array", typeof event?.system)
          return
        }
        event.system.push({ type: "text", text: block })
        entry.injections += 1
        entry.position = state.data?.position || ""
      })
    } catch (err) {
      degrade(rt, "hook de contexto falló", err)
    }

    void (async () => {
      try {
        if (!ctx?.event?.subscribe) throw new Error("event.subscribe no disponible")
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
          run("trace_push", {
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
            run("trace_push", {
              event_type: "workflow_suggestion_ignored",
              source: "agent_hook",
              session_id: entry.sessionID,
              data: { injections: entry.injections, position: entry.position },
            })
          }
          sessions.delete(sid || "global")
        }
      } catch (err) {
        degrade(rt, "event.subscribe falló", err)
      }
    })()

    try {
      if (!ctx?.tool?.transform) throw new Error("tool.transform no disponible")
      await ctx.tool.transform((editor) => {
        if (!DENY && !DENY_INTAKE) return
        for (const name of MUTATING_TOOLS) {
          const tool = editor.get(name)
          if (!tool || typeof tool.execute !== "function") continue
          editor.update(name, (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const cur = state.data
              // ── Gate de INTAKE: sin tarea declarada no se escribe ──
              if (DENY_INTAKE && !rt.activeTask && rt.projectId) {
                const path = relPath(input?.path || input?.filePath || "", rt)
                const slug = String(rt.sessionGoal || path || "tarea")
                  .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "tarea"
                run("trace_push", {
                  event_type: "flight_control_denied",
                  source: "agent_hook",
                  session_id: String(callCtx?.sessionID || ""),
                  data: { tool: name, gate: "intake", file: path },
                })
                throw new Error(
                  `[WALKER] Escritura bloqueada: esta sesión todavía no declaró su tarea.
` +
                    `No es un bug ni falta leer código: es el protocolo del proyecto.
` +
                    `Declarala UNA vez y seguí:
` +
                    `  ${CLI} ${rt.projectId}/implement_task '{"task_id":"${slug}","session_id":"${String(callCtx?.sessionID || "")}",` +
                    `"objective":"${(rt.sessionGoal || "cambios sobre " + (path || "el proyecto")).replace(/"/g, "'")}","files":["${path || "..."}"]}'
` +
                    `Después repetí exactamente la herramienta que querías usar: el bloqueo se libera solo.`
                )
              }
              if (!DENY || !cur || cur.execType !== "llm_task" || cur.satisfied || !rt.activeTask) {
                return original(input, callCtx)
              }
              // Si la tarea declaró archivos, el gate solo cubre esos archivos.
              const path = String(input?.path || input?.filePath || input?.file || "")
              const files = rt.activeTask.files || []
              if (path && files.length && !files.some((f) => path.endsWith(f))) {
                return original(input, callCtx)
              }
              const fields = (cur.required || []).length
                ? (cur.required || []).map((f) => `"${f}":"..."`).join(",")
                : ""
              const gate = (cur.gates || [])[0] || ""
              run("trace_push", {
                event_type: "flight_control_denied",
                source: "agent_hook",
                session_id: String(callCtx?.sessionID || ""),
                data: { tool: name, position: cur?.position, required: cur?.required },
              })
              // El mensaje tiene que ser AUTOSUFICIENTE: en el probe anterior el
              // agente gastó 19 de 24 pasos leyendo el fuente del plugin para
              // entender el bloqueo. Todo lo que necesita va acá.
              throw new Error(
                `[WALKER] Escritura bloqueada. El workflow del proyecto está en ` +
                  `${cur?.position} y falta registrar: ${(cur.required || []).join(", ") || "(sin campos)"}.\n` +
                  `Es una regla del proyecto, no un bug: no hace falta leer el código del gate.\n` +
                  `Para desbloquear, en DOS llamadas:\n` +
                  `  1) ${CLI} ${rt.projectId}/submit '{${fields}}'\n` +
                  `  2) ${CLI} ${rt.projectId}/transition '{"gate":"${gate}"}'\n` +
                  `Qué poner en cada campo: el contenido real que exige el paso ` +
                  `(para \`preconditions\`, premisas verificables: comando => resultado; ` +
                  `para \`loop_verification\`, comando => salida).\n` +
                  `Después repetí exactamente la herramienta que querías usar y seguirá normal.`
              )
            }
          })
        }
      })
    } catch (err) {
      degrade(rt, "tool.transform: gate de negación", err)
    }

    try {
      if (!ctx?.tool?.transform) throw new Error("tool.transform no disponible")
      await ctx.tool.transform((editor) => {
        // Auto-declarar la tarea en la primera ESCRITURA de la sesión: el delta
        // solo se llenaba cuando alguien llamaba `implement_task`, y el agente
        // no lo hace. Sin esto el ancla de tarea del contexto nunca existe.
        for (const name of ["edit", "write"]) {
          const tool = editor.get(name)
          if (!tool || typeof tool.execute !== "function") continue
          editor.update(name, (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              if (!rt.activeTask && rt.projectId) {
                const file = relPath(input?.path || input?.filePath || "", rt)
                const taskId = taskIdFor(callCtx?.sessionID)
                const res = run("implement_task", {
                  task_id: taskId,
                  session_id: String(callCtx?.sessionID || ""),
                  objective: rt.sessionGoal || `cambios sobre ${file || "el proyecto"}`,
                  files: file ? [file] : [],
                })
                if (res && res.ok !== false) {
                  rt.activeTask = { taskId, files: file ? [file] : [], auto: true }
                  run("trace_push", {
                    event_type: "task_autodeclared",
                    source: "agent_hook",
                    session_id: String(callCtx?.sessionID || ""),
                    task_id: taskId,
                    files: file ? [file] : [],
                    data: { task_id: taskId, file },
                  })
                }
              } else if (rt.activeTask && input?.path && !rt.activeTask.auto) {
                // tarea ya declarada a mano: no tocamos
              } else if (rt.activeTask && rt.activeTask.auto && input?.path) {
                const f = relPath(input.path, rt)
                if (!rt.activeTask.files.includes(f)) rt.activeTask.files.push(f)
              }
              return original(input, callCtx)
            }
          })
        }
      })
    } catch (err) {
      degrade(rt, "tool.transform: auto-declaración", err)
    }

    try {
      if (!ctx?.tool?.transform) throw new Error("tool.transform no disponible")
      await ctx.tool.transform((editor) => {
        const readTool = editor.get("read")
        if (readTool && typeof readTool.execute === "function") {
          editor.update("read", (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const result = await original(input, callCtx)
              const file = relPath(input?.path || input?.filePath || "", rt)
              if (!rt.baselineDone && file && rt.projectId) {
                rt.baselineDone = true
                const sid = String(callCtx?.sessionID || "")
                // Preferir los archivos QUE DECLARÓ LA TAREA (contexto de todos,
                // no del primero que se abre). Si no hay tarea, cae al archivo.
                let block = ""
                let source = "file"
                const tid = state.data?.taskId || ""
                if (tid) {
                  const taskCtx = run("agent_context", { task_id: tid })
                  const files = (taskCtx?.anchor?.files || []).filter(Boolean)
                  if (files.length) {
                    const pf = run("preflight", { files })
                    block = preflightBlock(pf)
                    if (block) source = "task"
                  }
                }
                if (!block) {
                  const ctxData = run("agent_context", { file })
                  if (ctxData) block = baselineBlock(ctxData, file, rt)
                }
                if (block) {
                  rt.pendingBaseline = block
                  run("trace_push", {
                    event_type: "intake_baseline",
                    source: "agent_hook",
                    session_id: sid,
                    files: file ? [file] : [],
                    data: { file, task_id: tid, from: source },
                  })
                }
              }
              return result
            }
          })
        }
      })
    } catch (err) {
      degrade(rt, "tool.transform: baseline", err)
    }

    try {
      if (!ctx?.tool?.transform) throw new Error("tool.transform no disponible")
      await ctx.tool.transform((editor) => {
        const taskTool = editor.get("implement_task")
        if (taskTool && typeof taskTool.execute === "function") {
          editor.update("implement_task", (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const result = await original(input, callCtx)
              rt.activeTask = {
                taskId: String(input?.task_id || ""),
                files: Array.isArray(input?.files) ? input.files.map(String) : [],
              }
              return result
            }
          })
        }
      })
    } catch (err) {
      degrade(rt, "tool.transform: tarea activa", err)
    }

    try {
      if (!ctx?.tool?.transform) throw new Error("tool.transform no disponible")
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
    } catch (err) {
      degrade(rt, "tool.transform: seguimiento de submit", err)
    }

    return () => controller.abort()
}
