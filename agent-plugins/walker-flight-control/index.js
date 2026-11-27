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
import { appendFileSync, existsSync } from "node:fs"
import { join } from "node:path"

import { shellEffect, shellMutation as shellMutationImpl } from "./shell_mutation.mjs"
import { briefBlock, failureBriefBlock } from "./brief_block.mjs"
import { budgetDeny, budgetUsage, contractBlock } from "./contract.mjs"
import { applyInjection, describeMessages, normalizeMode, planBlocks } from "./injection.mjs"

const WALKER_ROOT = process.env.WALKER_ROOT || "/Users/davidmartinez/develop/Active-projects/walker"
// Configurable para poder probar el camino "walker no está" sin renombrar el
// binario real. Default: el CLI del repo.
const CLI = process.env.WALKER_CLI || `${WALKER_ROOT}/bin/walker3`
// Timeout corto para el camino del gate: walker es una llamada sincrónica y un
// cuelgue acá se siente como "OpenCode no responde". Lo que no contesta en 3s
// se trata como NO DISPONIBLE (fail-open), no como "sin declarar".
const GATE_TIMEOUT_MS = 2_000
// El contexto (agent_context/preflight) puede tardar en repos grandes, pero no
// puede colgar el turno: es inyección de contexto, no un requisito.
const HOOK_TIMEOUT_MS = 5_000
// El baseline hace preflight + CKG: 2s no alcanza y un fallo cambiaba la firma.
const BASELINE_TIMEOUT_MS = 6_000
const ENABLED = process.env.WALKER_FLIGHT_CONTROL !== "0"
const REFRESH_MS = 60_000
const MAX_AUTO_STEPS = 3
// Negación de escrituras: OPT-IN. Con el flag apagado el plugin solo avisa.
// Se enciende para medir cuánto traba, no como default (una sesión en un
// proyecto registrado no podría editar hasta completar el paso pendiente).
// Lo único que movió la aguja fue negar; queda opt-in por costo. Se puede apagar con WALKER_FLIGHT_CONTROL_INTAKE=0.
// Gate de INTAKE: niega ESCRITURAS hasta que la tarea esté declarada. Es barato
// (una llamada y se abre). `setup` corre una vez por LOCATION: el estado vive en
// `rt` (por setup), no en globales — se midió una sesión en walker bloqueada con
// "game_reset/implement_task" porque el projectId era compartido.
// ON por defecto: con el gate apagado el agente escribe primero y declara nunca,
// y la spec queda vacía (el auto-declare la rellenaba con el primer mensaje del
// usuario, sin criterios). Escape hatch: WALKER_FLIGHT_CONTROL_INTAKE=0.
const DENY_INTAKE = process.env.WALKER_FLIGHT_CONTROL_INTAKE !== "0"
// Gate de DESVÍO (FLIGHT-CONTROL §2.4): una escritura fuera del `scope` se
// negocia (session_plan mode=add, una vez por archivo, trazado; nunca reescribe
// el plan). Escape: ..._SCOPE=0.
const SCOPE_GATE = process.env.WALKER_FLIGHT_CONTROL_SCOPE !== "0"
// Gate de DILUVIO (F2, ADR-WALKER-086): con un WorkContract que declara
// `budget.max_files`, no se agrega un archivo nuevo por encima de la cota. Sin
// contrato no hay cota y el gate no se activa (no inventa presupuesto).
// Escape: WALKER_FLIGHT_CONTROL_BUDGET=0.
const BUDGET_GATE = process.env.WALKER_FLIGHT_CONTROL_BUDGET !== "0"
// Gate de PASOS: además exige completar el sub-estado del workflow. Cuesta ~7x
// en tareas triviales, así que queda opt-in.
const DENY = process.env.WALKER_FLIGHT_CONTROL_DENY === "1"
// FC-INV-17: con sprint y tarea declarados, una decisión ABIERTA bloquea la
// escritura. ON por defecto (la estrategia "si hay sprint, seguí el workflow");
// `WALKER_FLIGHT_CONTROL_STEP_GATE=0` es el escape explícito.
const DENY_STEP = process.env.WALKER_FLIGHT_CONTROL_STEP_GATE !== "0"
// `shell` incluido: sin él el agente escribe con `echo >> archivo` y el gate
// de `edit`/`write` no contiene nada (medido: 1/1 bypass por shell).
const MUTATING_TOOLS = ["edit", "write", "shell"]

// Canal de inyección del turno (AE-INV-10). El default deja el `system`
// byte-idéntico mientras el nodo no cambie: es lo que permite que el proveedor
// reutilice el prefijo. `once` reproduce el comportamiento previo (un bloque por
// cambio de firma) y existe para comparar A/B. `messages` anexa al último turno
// del usuario y sólo se activa con evidencia: si la forma no se reconoce, cae a
// `system` y lo traza (`injection_fallback`).
const INJECTION_MODE = normalizeMode(process.env.WALKER_FLIGHT_CONTROL_INJECTION)
// Rondas de cierre sin verificación antes de pasarlo a un humano (AE-INV-12).
// Antes era 1 (>255 `close_blocked`): insistir no verificaba, sólo cobraba.
const CLOSE_ROUNDS = Math.max(1, Number(process.env.WALKER_FLIGHT_CONTROL_CLOSE_ROUNDS || 3))

// OJO: NO hay estado module-global. `setup` corre UNA VEZ POR UBICACIÓN en el
// mismo proceso (medido: tres setups para tres worktrees), así que una variable
// global hacía que la sesión de un proyecto usara el projectId de otro — bloqueó
// una sesión en walker con "game_reset/implement_task". Todo el estado vive en
// `rt`, creado en `runSetup`.

function runTool(tool, payload = {}, project = "", timeoutMs = 4_000) {
  try {
    const stdout = execFileSync(
      CLI,
      [`${project || "_"}/${tool}`, JSON.stringify(payload)],
      {
        cwd: WALKER_ROOT,
        encoding: "utf8",
        timeout: timeoutMs,
        killSignal: "SIGKILL",
        maxBuffer: 8 * 1024 * 1024,
        stdio: ["ignore", "pipe", "ignore"],
      }
    )
    return JSON.parse(stdout)
  } catch (err) {
    // Fail-open (el agente sigue) pero visible: sin traza, un CLI roto parece
    // "walker no tenía nada que decir" en vez de "walker no respondió".
    // Nunca se propaga: un plugin de gobierno no puede tumbar la sesión.
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
  // mensaje de rol user que NO sea un bloque inyectado por el entorno.
  // Un `<conversation-checkpoint>` o un `<system-update>` no es el pedido y,
  // tomado como goal, envenena la spec (medido 2026-11-21: overlap 0.0 entre
  // el checkpoint y el objetivo declarado).
  const msgs = event?.messages || []
  for (const m of msgs) {
    if ((m?.role || "") !== "user") continue
    const content = m?.content
    const parts = Array.isArray(content) ? content : content ? [content] : []
    const text = parts
      .map((p) => (typeof p === "string" ? p : p?.type === "text" ? p.text : ""))
      .filter(Boolean)
      .join(" ")
    const clean = String(text).replace(/\s+/g, " ").trim()
    if (!clean) continue
    const low = clean.toLowerCase()
    if (low.startsWith("<") || low.includes("<conversation-checkpoint")) continue
    return clean.slice(0, 240)
  }
  return ""
}

function taskIdFor(sessionID) {
  const clean = String(sessionID || "ses").replace(/[^a-zA-Z0-9]/g, "")
  return `auto-${clean.slice(-10) || Date.now()}`
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

function projectMissingBlock(rt) {
  const dir = rt?.projectDir || "(sin directorio)"
  return (
    `[WALKER] Sin proyecto: no se pudo resolver ${dir} contra el registro.\n` +
    `Walker queda APAGADO para esta sesión (sin workflow, gates ni trazas), o walker no responde.\n` +
    `No inventes un project_id para las tools de walker: el proyecto es el dominio de trabajo.\n` +
    `Si el proyecto existe, registralo con: ${CLI} _/prepare '{"path":"${dir}"}' (lo decide el usuario).`
  )
}

function closeBlock(rt, plan, entry, workProject) {
  const proj = workProject || rt?.projectId || "<proyecto>"
  const criteria = plan && Array.isArray(plan.acceptance) ? plan.acceptance : []
  const crit = criteria.length
    ? `\nCriterios: ${criteria
        .slice(0, 3)
        .map((c, i) => `${i + 1}) ${String(c).slice(0, 70)}`)
        .join(" ")}`
    : ""
  const failed =
    entry && entry.verifyFailed ? "\nLa última verificación FALLÓ: corregí antes de terminar." : ""
  const hidden = entry && (entry.verifyHint === "pipe" || entry.verifyHint === "hidden")
  const pipe =
    hidden
      ? "\nLa última verificación no mostró su resultado (pipe o redirección): el plugin sólo certifica lo que ve. Repetila SIN pipe y SIN redirección (`> archivo` también oculta la salida), o asegurate de que aparezca WALKER_RESULT."
      : ""
  // La captura de conocimiento resuelve la spec y la traza por (task, session).
  // Si el nudge manda `<id>` y sin sesión, el hecho no se puede anclar y se
  // pierde (o peor: hereda la identidad de otra tarea). Se propaga lo que el
  // plan ya declara.
  const task = plan && plan.task ? String(plan.task) : "<id>"
  const sid = entry && entry.sessionID ? `,"session_id":"${entry.sessionID}"` : ""
  // SPEC-013: el nudge dice QUÉ falló y DÓNDE, no sólo que falta el verde. Sin
  // la salida el agente repite el mismo comando; con la cola del error y los
  // archivos tocados corrige sobre el fallo real. La cola se recorta: el nudge
  // compite por atención y no debe traer el volcado completo.
  const touched = Array.from((entry && entry.touched) || []).slice(0, 3)
  const files = touched.length ? `\nArchivos modificados: ${touched.join(", ")}` : ""
  const failure = entry && entry.lastFailure ? entry.lastFailure : null
  const detail = failure
    ? `\nÚltimo intento (rojo): ${String(failure.command || "").slice(0, 90)}` +
      (String(failure.text || "").trim()
        ? `\n${String(failure.text).trim().split("\n").slice(-4).join("\n").slice(-240)}`
        : "")
    : ""
  return (
    `[WALKER] No cierres todavía: esta sesión modificó archivos y no hay verificación VERDE.${crit}${failed}${pipe}${files}${detail}\n` +
    `Corré la suite: ${CLI} ${proj}/run_tests '{"task_id":"${task}"${sid}}' (ejecuta pytest y devuelve pass/fail) ` +
    `o verificá los predicados con: ${CLI} ${proj}/verify_predicates '{"task_id":"${task}"${sid}}'.`
  )
}

// ── Sello de verificación (AE-INV-12) ────────────────────────────────────
// El cierre no es una frase: es un artefacto consultable. `kind` dice QUÉ
// verificó (tests, predicados, e2e, comando suelto) y `none` es un valor
// legítimo — el problema no es no verificar, es no poder demostrarlo.
const STAMP_KINDS = ["tests", "predicates", "e2e", "command", "none"]

const VERIFY_LEVEL_TO_KIND = {
  predicates: "predicates",
  tools: "predicates",
  tool: "tests",
  "cli-marker": "tests",
  "cli-marker-pipe": "tests",
  cli: "tests",
  "cli-pipe": "command",
  shell: "command",
  e2e: "e2e",
  tests: "tests",
}

export function verificationStamp(entry, plan, now = new Date().toISOString()) {
  const wrote = Boolean(entry && entry.wrote)
  const verified = Boolean(entry && entry.verified) && !entry?.verifyFailed
  const level = String((entry && entry.verifyLevel) || "").toLowerCase()
  const kind = wrote && verified ? VERIFY_LEVEL_TO_KIND[level] || "command" : "none"
  const criteria = plan && Array.isArray(plan.acceptance) ? plan.acceptance.length : 0
  return {
    kind: STAMP_KINDS.includes(kind) ? kind : "none",
    wrote,
    verified,
    level: level || "none",
    weak: level === "cli-pipe" || Boolean(entry && entry.verifyWeak),
    criteria,
    rounds: Number((entry && entry.closeRounds) || 0),
    // Cuándo verificó el PLUGIN (si vio el verde). Sin este dato, un cierre
    // con `none` no se puede distinguir de "no me enteré del verde" y pisaría
    // evidencia guardada por la tool.
    verified_at: String((entry && entry.verifiedAt) || ""),
    at: now,
  }
}

export function stampLine(stamp) {
  if (!stamp || stamp.kind === "none") return "verificación: NONE (sin evidencia)"
  return `verificación: ${String(stamp.kind).toUpperCase()}${stamp.weak ? " (pipe: exit code no confiable)" : ""}`
}

function escalateBlock(rt, plan, entry, workProject) {
  const proj = workProject || rt?.projectId || "<proyecto>"
  const rounds = Number((entry && entry.closeRounds) || 3)
  const criteria = plan && Array.isArray(plan.acceptance) ? plan.acceptance : []
  const crit = criteria.length
    ? `\nCriterios sin certificar: ${criteria
        .slice(0, 3)
        .map((c, i) => `${i + 1}) ${String(c).slice(0, 60)}`)
        .join(" ")}`
    : ""
  // SPEC-013: la escalada también nombra el trabajo pendiente (archivos), para
  // que el humano reciba el estado y no una queja genérica.
  const touched = Array.from((entry && entry.touched) || []).slice(0, 3)
  const files = touched.length ? `\nArchivos modificados sin certificar: ${touched.join(", ")}` : ""
  return (
    `[WALKER] Tercera ronda sin verificación VERDE (${rounds} intentos).${crit}${files}\n` +
    `No sigas reintentando solo: PARÁ y pedile al usuario qué falta o qué criterio se cae. ` +
    `Si el trabajo quedó incompleto, decilo explícitamente y no lo presentes como terminado.\n` +
    `Cuando haya verde, el cierre queda sellado con: ${CLI} ${proj}/run_tests '{"task_id":"${
      plan && plan.task ? plan.task : "<id>"
    }"${entry && entry.sessionID ? `,"session_id":"${entry.sessionID}"` : ""}}'.`
  )
}

export default {
  id: "walker-flight-control",

  async setup(ctx) {
    try {
      // El cleanup aborta el subscribe; si no se devuelve, cada reload deja un
      // subscriber vivo y duplica trazas (auditoría fail-open, hallazgo #2).
      return await runSetup(ctx)
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
      // Proyecto de TRABAJO (donde están los archivos), distinto del cwd.
      workProjectId: "",
      projectDir: ctx?.location?.directory || ctx?.directory || "",
      sessionId: "",
      activeTask: null,
      boundBySession: {},
      activeTaskBySession: {},
      sessionGoal: "",
      pendingBaseline: null,
      baselineDone: false,
      captured: false,
      degraded: new Set(),
      pendingDegrades: [],
    }
    const run = (tool, payload = {}, project = rt.projectId, timeoutMs) =>
      runTool(tool, payload, project, timeoutMs)

    // Per-session accessors (SPEC-023)
    const getSessionId = () => rt.sessionId || ""
    const getBound = () => rt.boundBySession[getSessionId()] || null
    const setBound = (v) => { rt.boundBySession[getSessionId()] = v }
    const getActiveTask = () => rt.activeTaskBySession[getSessionId()] || null
    const setActiveTask = (v) => { rt.activeTaskBySession[getSessionId()] = v }
    Object.defineProperty(rt, "bound", {
      get() { return getBound() },
      set(v) { setBound(v) },
    })
    Object.defineProperty(rt, "activeTask", {
      get() { return getActiveTask() },
      set(v) { setActiveTask(v) },
    })

    // Protocolo de continuidad (delta-sprint): antes de que el agente escriba,
    // se le dice qué sprint existe, cuál era su goal y cuántas tareas tiene,
    // para que PREGUNTE al usuario si continúa o arranca un goal nuevo.
    // `auto` es read-only: no crea nada. Si walker no responde, se cae al
    // bootstrap viejo (el agente igual puede trabajar).
    const planCache = new Map()
    // Lectura única del plan (bloque y gate de desvío comparten la caché).
    const planFor = (sid) => {
      if (!rt.projectId || !sid) return {}
      const hit = planCache.get(sid)
      if (hit && Date.now() - hit.at < 10_000) return hit.plan || {}
      const res = run("session_plan", { session_id: sid, mode: "get" }, rt.projectId, HOOK_TIMEOUT_MS)
      const data = res?.data || res
      const plan = (data && data.plan) || {}
      planCache.set(sid, { at: Date.now(), plan })
      return plan
    }

    // Hidratación del estado de vuelo desde el TRACE (SSOT): una recarga del
    // plugin no pierde `verified`/`wrote`. Se hace UNA vez por entry, en
    // cualquier camino (context o gate de cierre): si el plugin se recargó y
    // no hubo hook de contexto, el estado persistido igual llega al gate.
    const hydrateFlight = (sid, entry, opts = {}) => {
      if (!sid || !entry || (entry.hydrated && !opts.force)) return
      entry.hydrated = true // fail-open: no reintentar en cada evento
      try {
        const res = run("session_context", { session_id: sid }, rt.projectId, HOOK_TIMEOUT_MS)
        const data = (res && (res.data || res)) || {}
        // F2 (ADR-WALKER-086): el contrato 4D viaja en la MISMA lectura. Se
        // cachea aunque no haya flight_state: el budget no depende del vuelo.
        if (data && data.contract_4d) entry.contract4d = contractBlock(data.contract_4d)
        const fs = (data && data.flight_state) || (res && res.flight_state)
        if (!fs || typeof fs !== "object" || !Object.keys(fs).length) return
        // Sin `force` se conserva el contrato previo: el estado local manda.
        if (!opts.force && (entry.verified || entry.wrote || entry.writes)) return
        // P1.11: el verde puede venir de una TOOL que el plugin no vio
        // (Code Mode, shell del host, subagente). Se adopta solo si el
        // `verified_at` del trace es posterior a la última escritura conocida:
        // así no se resucita un verde viejo después de editar.
        const persistedAt = Date.parse(fs.verified_at || "") || 0
        const dirtyAt = Date.parse(entry.dirtySince || "") || 0
        const localAt = Date.parse(entry.verifiedAt || "") || 0
        if (typeof fs.verified === "boolean") {
          if (fs.verified === true) {
            if (persistedAt > dirtyAt && persistedAt >= localAt) {
              entry.verified = true
              entry.verifyFailed = false
              entry.verifiedAt = fs.verified_at || entry.verifiedAt || ""
              entry.verifyLevel = fs.verify_level || entry.verifyLevel || ""
              // P3c: el verde puede ser débil (se certificó con reglas que el
              // cambio tocó). Viaja al sello como `weak`, no se descarta.
              if (fs.weak === true) entry.verifyWeak = true
              entry.dirtySince = ""
            }
          } else if (!entry.verified) {
            entry.verifyFailed =
              fs.verify_failed === undefined
                ? entry.verifyFailed
                : Boolean(fs.verify_failed)
          }
        }
        if (entry.verified) entry.verifyLevel = entry.verifyLevel || fs.verify_level || ""
        entry.verifiedAt = entry.verifiedAt || fs.verified_at || ""
        if (!entry.wrote && fs.wrote !== undefined) entry.wrote = Boolean(fs.wrote)
        if (!entry.writes && typeof fs.writes === "number") entry.writes = fs.writes
        if (!entry.touched.size && Array.isArray(fs.touched)) {
          entry.touched = new Set(fs.touched)
        }
        if (!entry.verified && !entry.dirtySince) {
          entry.dirtySince = fs.dirty_since || ""
        }
        entry.statePersisted = true
        // La sesión existía antes de la recarga: el gate de cierre debe
        // evaluarla (si no, una sesión sucia "desaparece" al recargar).
        if (entry.wrote || entry.verified) entry.opened = true
      } catch (err) {
        degrade(rt, "session_context hydration falló", err)
      }
    }

    // ¿El comando de shell muta ARCHIVOS DEL PROYECTO? La lógica vive en
    // `shell_mutation.mjs` (testeable sin OpenCode). Acá sólo se le pasa el
    // `projectDir` de la sesión.
    const shellMutation = (cmd) => shellMutationImpl(cmd, rt.projectDir)

    // Costo/tokens de la sesión. Dos caminos, en ese orden:
    //   1) `Session.Info` (sesión): ya agrega costo y tokens INCLUYENDO
    //      cache.read/write y reasoning. Es el único que permite distinguir un
    //      miss de prefijo (cacheWrite sube, cacheRead cae).
    //   2) `session.context()`: suma mensajes. Se conserva para runtimes sin
    //      `session.get` y para no perder el dato si el primero falla.
    // Si ninguno responde, `cost` queda null: nunca 0 como si fuera gratis.
    const sessionCost = async (sid) => {
      try {
        if (ctx?.session?.get) {
          const info = await ctx.session.get({ sessionID: sid })
          const t = info?.tokens
          if (t && typeof t === "object") {
            return {
              cost: typeof info.cost === "number" ? info.cost : null,
              tokens: {
                input: Number(t.input || 0),
                output: Number(t.output || 0),
                cacheRead: Number(t.cache?.read ?? t.cacheRead ?? 0),
                cacheWrite: Number(t.cache?.write ?? t.cacheWrite ?? 0),
                reasoning: Number(t.reasoning || 0),
              },
            }
          }
        }
      } catch (err) {
        degrade(null, "session.get no disponible", err)
      }
      try {
        if (!ctx?.session?.context) return { cost: null, tokens: null }
        const msgs = await ctx.session.context({ sessionID: sid })
        let cost = 0
        let known = false
        const tokens = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, reasoning: 0 }
        for (const m of msgs || []) {
          const info = m?.info || m || {}
          if (typeof info.cost === "number") {
            cost += info.cost
            known = true
          }
          const t = info.tokens || {}
          tokens.input += Number(t.input || 0)
          tokens.output += Number(t.output || 0)
          tokens.cacheRead += Number(t.cacheRead || t.cache_read || 0)
          tokens.cacheWrite += Number(t.cacheWrite || t.cache_write || 0)
          tokens.reasoning += Number(t.reasoning || 0)
        }
        return { cost: known ? cost : null, tokens }
      } catch (err) {
        degrade(rt, "session cost no disponible", err)
        return { cost: null, tokens: null }
      }
    }

    // Resolución de proyecto POR PATH (una sesión puede editar otro repo).
    const projectPathCache = new Map()
    const projectForPath = (abs) => {
      const hit = projectPathCache.get(abs)
      if (hit && Date.now() - hit.at < 60_000) return hit.value
      const res = run("project_for_path", { path: abs }, "_", GATE_TIMEOUT_MS)
      const value = res && res.ok && res.project_id ? res.project_id : ""
      projectPathCache.set(abs, { at: Date.now(), value })
      return value
    }

    const scopeProjects = (scope) => {
      const files = (Array.isArray(scope) ? scope : [])
        .map(String)
        .filter((sc) => sc && !sc.endsWith("/") && sc.includes("."))
      const groups = {}
      for (const sc of files) {
        const candidates = sc.startsWith("/")
          ? [sc]
          : [join(rt.projectDir || "", sc), join(WALKER_ROOT, sc)]
        for (const abs of candidates) {
          // `project_for_path` resuelve por PREFIJO: sin exigir que el archivo
          // exista, el candidato de la sesión "gana" siempre y el baseline es
          // del proyecto equivocado (medido: game_reset para un archivo walker).
          if (!existsSync(abs)) continue
          const proj = projectForPath(abs)
          if (proj) {
            ;(groups[proj] = groups[proj] || []).push({ sc, abs })
            break
          }
        }
      }
      return groups
    }

    // Proyecto de TRABAJO (puede diferir del cwd): plan.project -> scope -> cwd.
    // La sesión vive en `rt.projectId` (continuidad), pero el workflow, la
    // memoria y las trazas son del proyecto donde están los archivos. Sin
    // esto, una sesión abierta en game_reset trabajando en walker consultaba
    // el CKG vacío de game_reset (medido 2026-11-19).
    const workProjectFor = (sid) => {
      if (rt.workProjectId) return rt.workProjectId
      const plan = planFor(sid) || {}
      let proj = plan.project ? String(plan.project) : ""
      if (!proj) {
        const groups = scopeProjects(plan.scope)
        proj = Object.keys(groups)[0] || ""
      }
      rt.workProjectId = proj || rt.projectId
      return rt.workProjectId
    }

    // Trazas al proyecto de TRABAJO: una sesión abierta en game_reset que
    // edita walker no debe llenar de métricas el trace equivocado.
    const tracePush = (payload, _project, _timeoutMs) =>
      run(
        "trace_push",
        payload,
        workProjectFor((payload && payload.session_id) || rt.sessionId),
        HOOK_TIMEOUT_MS
      )

    const decisionCache = new Map()
    // L3: UNA decisión que aplique al archivo. El CKG la devuelve si su
    // statement menciona el path (convención "aplica a: <archivo>"); se filtra
    // por id `adr-NNN` para no citar nodos de sesión ni probes.
    //
    // P1.2: si `memory_health.trusted` es false (CKG/CPG vacío), NO se cita un
    // `adr-` como autoridad. Un `adr-999` de seed en un CKG sin índice no es una
    // decisión del proyecto; citarlo es peor que no citar nada.
    const decisionFor = (proj, files) => {
      if (!proj || !files.length) return ""
      const key = `${proj}|${files.join(",")}`
      const hit = decisionCache.get(key)
      if (hit && Date.now() - hit.at < 60_000) return hit.value
      let found = ""
      for (const f of files.slice(0, 2)) {
        const res = run("agent_context", { project_id: proj, file: f }, proj, BASELINE_TIMEOUT_MS)
        const health = (res && res.memory_health) || null
        if (health && health.trusted === false) {
          rt.ckgDegraded = health
          return ""
        }
        rt.ckgDegraded = null
        const results = (res && res.ckg && res.ckg.results) || []
        const adr = results.find((r) => /^adr-\d+$/.test(String((r && r._id) || "")))
        if (adr) {
          found = String(adr._id)
          break
        }
      }
      decisionCache.set(key, { at: Date.now(), value: found })
      return found
    }

    const preflightCache = new Map()
    const baselineLine = (scope, entry) => {
      const groups = scopeProjects(scope)
      if (!Object.keys(groups).length) return ""
      const rows = []
      for (const [proj, items] of Object.entries(groups)) {
        const key = `${proj}|${items.map((i) => i.sc).join(",")}`
        const hit = preflightCache.get(key)
        let pf = hit && Date.now() - hit.at < 60_000 ? hit.value : null
        if (!pf) {
          pf = run(
            "preflight",
            { project_id: proj, files: items.map((i) => i.sc).slice(0, 4) },
            proj,
            BASELINE_TIMEOUT_MS
          )
          preflightCache.set(key, { at: Date.now(), value: pf || null })
        }
        if (!pf || pf.error || !Array.isArray(pf.files)) continue
        const tests = Array.isArray(pf.tests) ? pf.tests.length : 0
        for (const f of pf.files) {
          const name = String(f.file || "").split("/").pop()
          const callers = Number(f.callers || 0)
          const cyclo = Number((Array.isArray(f.heaviest) && f.heaviest[0] && f.heaviest[0].cyclo) || 0)
          rows.push({ name, callers, cyclo, tests })
        }
      }
      // Prioridad por RIESGO: blast radius × complejidad; máximo dos archivos.
      rows.sort((a, b) => b.callers * Math.max(b.cyclo, 1) - a.callers * Math.max(a.cyclo, 1))
      const top = rows.slice(0, 2).map((r) => {
        const parts = [String(r.callers) + " llamadores"]
        if (r.cyclo) parts.push("cyclo " + r.cyclo)
        parts.push(r.tests === 1 ? "1 test" : r.tests + " tests")
        return `${r.name} ${parts.join(" · ")}`
      })
      if (!top.length) return ""
      const dominant = Object.keys(groups)[0]
      const decision = decisionFor(dominant, (groups[dominant] || []).map((i) => i.sc))
      // Sin memoria confiable no se cita `adr-`: se dice que está degradada.
      const tail = decision
        ? " · " + decision
        : rt.ckgDegraded
          ? " · CKG degradado"
          : ""
      const line = `Baseline: ${top.join("; ")}${tail}`
      if (entry) {
        entry.baselineInjected = true
        entry.baselineFiles = rows.slice(0, 2).map((r) => String(r.name || ""))
      }
      return line.slice(0, 140)
    }

    const inScope = (file, scope) => {
      const f = String(file)
      return scope.some((raw) => {
        const sc = String(raw || "")
        if (!sc) return false
        if (f === sc) return true
        if (f.endsWith("/" + sc)) return true
        if (sc.endsWith("/") && (f.startsWith(sc) || f.includes("/" + sc))) return true
        // Directorio declarado sin extensión: prefijo con límite de path.
        if (!sc.includes(".") && (f.startsWith(sc + "/") || f.includes("/" + sc + "/"))) return true
        return false
      })
    }

    // Bloque CORTO y estable: destino, tarea, próximo paso y desvío. Se emite
    // solo cuando cambia la firma del plan (no rompe el caché ni repite ruido).
    const planSigOf = (plan) =>
      JSON.stringify([
        plan.goal,
        plan.task,
        plan.scope,
        plan.next,
        plan.acceptance,
        plan.approved_by_user,
      ])

    // Texto del plan SIN dedupe: en `stable` el request necesita los mismos
    // bytes otra vez, no "una vez y nunca más" (AE-INV-10).
    const planText = (sid, entry) => {
      const plan = planFor(sid)
      if (!plan || !plan.goal) return ""
      const scope = Array.isArray(plan.scope) ? plan.scope : []
      entry.planTask = plan.task || ""
      const baseline = baselineLine(scope, entry)
      const goal = String(plan.goal).replace(/\s+/g, " ").slice(0, 90)
      const criteria = Array.isArray(plan.acceptance) ? plan.acceptance.length : 0
      const touched = Array.from(entry.touched || [])
      const outside = scope.length
        ? touched.filter((f) => !inScope(f, scope))
        : []
      // SPEC-014: el bloque dice CUÁNTOS criterios se verificaron, no sólo que
      // hubo un verde. `1/3` es un estado distinto de "verificado" y se ve sin
      // abrir el trace.
      const counts = entry.verifyCounts
      const verif =
        counts && counts.total
          ? ` · Verif: ${counts.passed == null ? "?" : counts.passed}/${counts.total}`
          : ""
      // SPEC-011: la aprobación del usuario se ve en el bloque, no sólo en la
      // tool. Un plan no trivial sin aprobar se declara pendiente; nunca se
      // asume aprobado por silencio.
      const approved = plan.approved_by_user === true
      const approval =
        scope.length >= 2 && criteria >= 2
          ? ` · Aprobado: ${approved ? "sí" : "PENDIENTE"}`
          : ""
      const lines = [
        `[WALKER] Plan: ${goal}`,
        `Tarea: ${plan.task || "(sin declarar)"} · Alcance: ${scope.length || "sin definir"} · Criterios: ${criteria}${approval}${verif}`,
      ]
      if (baseline) lines.push(baseline)
      lines.push(`Próximo: ${String(plan.next || "(definir)").slice(0, 70)}`)
      if (outside.length) lines.push(`⚠ Fuera de alcance: ${outside.slice(0, 3).join(", ")}`)
      // ADR-084 F0.2: el espejo del agente, en una línea. Se deriva del estado
      // local del plugin (no requiere CLI extra); si no hubo escrituras, no se
      // inyecta. ≤120 chars para no romper el presupuesto de atención.
      const mirror = entry.writes
        ? `Espejo: ${entry.writes} writes · ${entry.verified ? "verif ok" : "sin verif · " + (entry.closeRounds || 0) + " nudge(s)"}`.slice(0, 120)
        : ""
      if (mirror) lines.push(mirror)
      // Contrato de atención: el bloque entero <= 400 caracteres.
      const block = lines.join("\n")
      return block.length <= 400 ? block : block.slice(0, 397) + "…"
    }

    const planBlock = (sid, entry) => {
      const plan = planFor(sid)
      if (!plan || !plan.goal) return ""
      // La firma NO incluye el baseline: si preflight falla o tarda, el bloque
      // no cambia entre llamadas (medido: fallaba y se reinyectaba).
      // La firma del PLAN no cambia con la verificación; la del bloque sí: si
      // se verificaron predicados, el bloque tiene que volver a emitirse con el
      // conteo (SPEC-014). Sin esto el memo lo dejaba viejo.
      const counts = entry.verifyCounts
      const sig = `${planSigOf(plan)}|v:${counts ? `${counts.passed}/${counts.total}` : ""}`
      if (entry.planSig === sig) return "" // idéntico al anterior: no se inyecta
      entry.planSig = sig
      return planText(sid, entry)
    }

    // Los gates `user_input` son la PREGUNTA que el usuario tiene que
    // responder. El plugin descartaba el prompt del nodo: el agente solo veía
    // "complete el paso con submit" y tenía que adivinar qué preguntar (o
    // peor, decidía por el usuario). El bloque va al contexto del modelo, que
    // es lo único que el plugin puede hacer para que la pregunta se muestre.
    const GATE_MAX = 1200
    const gateOptions = (template) => {
      // Los templates cierran con "Options:" y líneas ". approve -> ...".
      const lines = String(template || "").split("\n")
      let start = -1
      for (let i = lines.length - 1; i >= 0; i--) {
        if (/^\s*(options|opciones)\s*:/i.test(lines[i])) {
          start = i
          break
        }
      }
      if (start === -1) {
        // Fallback: opciones declaradas en las llamadas a submit.
        const found = [...String(template || "").matchAll(/"decision"\s*:\s*"([a-z_]+)"/gi)].map(
          (m) => m[1]
        )
        const uniq = [...new Set(found)]
        return uniq.length ? `Opciones: ${uniq.join(" · ")}` : ""
      }
      const picked = []
      for (let i = start; i < lines.length && picked.length < 10; i++) {
        const raw = lines[i]
        if (i > start && /^\s*$/.test(raw) && picked.length) break
        const line = raw.trim()
        if (!line) continue
        if (/walker\s+.*\/submit/i.test(line)) break
        picked.push(line)
      }
      return picked.join("\n")
    }
    const gateText = (sid, st) => {
      if (!st || st.execType !== "user_input") return ""
      // Sesiones ajenas al flujo no reciben la pregunta de otra tarea.
      if (!planFor(sid) && !st.taskId) return ""
      const options = gateOptions(st.prompt?.template || "")
      const lines = [
        `[WALKER] Gate de decisión: ${st.position}`,
        "El proceso espera TU decisión. No decidas por el usuario: mostrale las opciones y esperá.",
      ]
      if (options) lines.push(options)
      lines.push(
        `Cerrá el gate con: ${CLI} ${rt.projectId}/submit '{"decision":"<opción>","evidence":"<motivo>"}'`
      )
      const block = lines.join("\n")
      return block.length <= GATE_MAX ? block : block.slice(0, GATE_MAX - 1) + "…"
    }
    const gateBlock = (sid, entry, st) => {
      if (!st || st.execType !== "user_input") return ""
      const sig = `${st.position}|${st.revision}|${st.prompt?.template_id || ""}`
      if (entry.gateSig === sig) return ""
      entry.gateSig = sig
      return gateText(sid, st)
    }

    const bindBlock = (sid, sessionGoal) => {
      const fallback = { text: bootstrapBlock(rt), decision: "" }
      if (!rt.projectId || !sid) return fallback
      const res = run(
        "session_bind",
        { session_id: sid, project_id: rt.projectId, mode: "auto", objective: sessionGoal || "" },
        rt.projectId,
        HOOK_TIMEOUT_MS
      )
      const data = res?.data || res
      if (!data || data.ok === false || !data.decision) return fallback
      const b = data.bound || data.previous || {}
      const head = `[WALKER] session_bind=${data.decision} · sesión ${sid} · proyecto ${rt.projectId}`
      const meta = b.spring_id
        ? `\nSprint: ${b.spring_id} (${b.status || "?"}) · goal: ${b.goal || "(sin goal)"} · ` +
          `tareas: ${b.tasks_count ?? 0} · sesiones: ${(b.sessions || []).join(", ") || "(sin registrar)"}`
        : ""
      // SPEC-006: reanudar no es "hay un sprint": es saber qué quedó abierto y
      // cuál era el próximo paso. Sin esto el agente relee el repo para
      // reconstruir el estado que el plan ya tenía.
      const open = Array.isArray(b.open_tasks) ? b.open_tasks : []
      const openIds = open
        .slice(0, 3)
        .map((task) => String(task && (task.task_id || task) || ""))
        .filter(Boolean)
        .join(", ")
      const handoff =
        (open.length ? `\nabiertas: ${open.length}${openIds ? ` (${openIds})` : ""}` : "") +
        (b.next ? `\nnext previo: ${String(b.next).replace(/\s+/g, " ").slice(0, 80)}` : "")
      return {
        decision: data.decision,
        text:
          `${head}${meta}${handoff}\n${data.suggestion || ""}\n` +
          `Si el usuario confirma, ejecutá: ${CLI} ${rt.projectId}/session_bind ` +
          `'{"session_id":"${sid}","mode":"new|continue"}' y después implement_task.`,
      }
    }

    if (rt.projectDir) {
      // Timeout corto: esto corre en el arranque del plugin y bloquea el event
      // loop. Un walker colgado no puede retrasar el inicio de OpenCode.
      const found = run("project_for_path", { path: rt.projectDir }, "_", GATE_TIMEOUT_MS)
      if (found?.ok && found.project_id) rt.projectId = found.project_id
    }

    // Señal de vida: sin esto no se distingue "el hook no cargó" de "no había
    // estado de workflow que inyectar".
    run(
      "trace_push",
      {
        event_type: "flight_control_active",
        source: "agent_hook",
        data: { project_id: rt.projectId, directory: rt.projectDir },
      },
      rt.projectId,
      GATE_TIMEOUT_MS
    )

    if (!rt.projectId) {
      // Sin dominio no hay protocolo, pero el agente NO puede quedar en silencio:
      // sin aviso sigue intentando tools de walker con un proyecto inventado.
      try {
        const noticed = new Set()
        if (ctx?.session?.hook) {
          await ctx.session.hook("context", (event) => {
            const sid = event?.sessionID || event?.sessionId || ""
            if (noticed.has(sid)) return
            noticed.add(sid)
            if (Array.isArray(event?.system)) {
              event.system.push({ type: "text", text: projectMissingBlock(rt) })
            }
          })
        }
      } catch (err) {
        degrade(null, "aviso de proyecto faltante", err)
      }
      return
    }
    flushDegrades(rt)

    const state = { data: null, fetchedAt: 0 }
    const sessions = new Map()
    // Declaraciones de tarea observadas EN PROCESO, por sesión. `implement_task`
    // corre en otro proceso (CLI) o como tool: el estado del workflow no alcanza
    // como señal porque el orquestador escribe `auto-<sid>` por su cuenta
    // (medido: una declaración real quedó pisada por un auto-… en la misma sesión).
    const declared = new Map()
    // Cortacircuito: si la declaración por CLI falla dos veces (proyecto mal
    // registrado, JSON inválido, walker a medias), se abre el gate. Un protocolo
    // que no se puede satisfacer no puede bloquear la sesión para siempre.
    const declareFailures = new Map()
    const controller = new AbortController()

    const keyFor = (sessionID) => String(sessionID || rt.sessionId || "global")

    const MAX_SESSIONS = Number(process.env.WALKER_FLIGHT_CONTROL_MAX_SESSIONS || 200)
    const sessionOf = (sessionID) => {
      const key = keyFor(sessionID)
      if (!sessions.has(key)) {
        // Evicción (SPEC-001): con muchas sesiones y recargas el Map crecía sin
        // cota. Se liberan las CERRADAS más viejas; la actual nunca se toca.
        const evicted = []
        if (sessions.size >= MAX_SESSIONS) {
          const closed = [...sessions.entries()].filter(([k, v]) => v.closed && k !== key)
          closed.sort((a, b) => (a[1].at || 0) - (b[1].at || 0))
          for (const [k] of closed) {
            if (sessions.size < Math.floor(MAX_SESSIONS * 0.75)) break
            sessions.delete(k)
            evicted.push(k)
          }
        }
        sessions.set(key, {
          // Traza (SPEC-001): sin esto una evicción no deja rastro y una
          // sesión recreada es indistinguible de una nueva. Se emite al cerrar
          // la ejecución, no en el camino del hook (no agrega latencia).
          evicted: evicted.length,
          evictedKeys: evicted,
          evictionTraced: false,
          at: Date.now(),
          injections: 0,
          followed: false,
          position: "",
          sessionID: sessionID || "",
          bootstrapped: false,
          bind: "",
          opened: false,
          closed: false,
          wrote: false,
          verified: false,
          verifiedAt: "",
          dirtySince: "",
          verifyLevel: "",
          verifyFailed: false,
          writes: 0,
          hydrated: false,
          statePersisted: false,
          scopeDenied: new Set(),
          scopeExpanded: new Set(),
          metricsSent: false,
          // Rondas de cierre (AE-INV-12): 1..n insiste, la última escala al
          // humano. `closeNudged` queda por compatibilidad de lectura de trazas.
          closeNudged: false,
          closeRounds: 0,
          closeEscalated: false,
          // Inyección (AE-INV-10): memo del prefijo estable y modo efectivo.
          injectMemo: null,
          injectionMode: "",
          briefText: "",
          usage: null,
          turns: 0,
          touched: new Set(),
          planSig: "",
          planTask: "",
          parentSessionId: "",
          model: "",
          baselineInjected: false,
          baselineFiles: [],
          firstMutation: null,
          gateSig: "",
        })
      } else {
        sessions.get(key).at = Date.now() // actividad = recencia para la evicción
      }
      return sessions.get(key)
    }

    const readState = (sid) => {
      const data = run("workflow_state", {}, workProjectFor(sid || rt.sessionId), GATE_TIMEOUT_MS)
      if (!data || data.ok === false) return null
      return {
        position: data.current_position || "",
        execType: data.execution_type || "",
        required: Array.isArray(data.required_fields) ? data.required_fields : [],
        satisfied: data.step_satisfied !== false,
        gates: Array.isArray(data.available_gates) ? data.available_gates : [],
        taskId: String(data.task_id || ""),
        revision: data.revision ?? 0,
        // Lo que el motor PIDE para este paso (`submit`/`advance`/`transition`).
        // El plugin no lo usaba: no podía medir si el agente siguió la
        // sugerencia ni con qué acción (drift sin instrumento).
        next: data.next_action && typeof data.next_action === "object"
          ? {
              tool: String(data.next_action.tool || ""),
              reason: String(data.next_action.reason || ""),
              detail: String(data.next_action.detail || ""),
            }
          : null,
        // El nodo ya trae su propio prompt (template_id + template). El plugin
        // lo descartaba y armaba "complete el paso con submit", que no coincide
        // con lo que el nodo pide: el agente recibía una instrucción falsa y la
        // ignoraba (medido: workflow_suggestion_ignored 2/2, followed=false).
        prompt: data.prompt && typeof data.prompt === "object" ? data.prompt : null,
      }
    }

    // Los sub-estados `execution: auto` los corre el MOTOR, no el modelo: no
    // tiene sentido esperar a que el agente se acuerde de submitear un paso
    // que no requiere criterio. Hasta ahora nadie los disparaba, así que todos
    // los proyectos quedaban clavados en `START.LIST` (medido: 7/7).
    const driveAuto = (current, sid) => {
      let cur = current
      for (let i = 0; i < MAX_AUTO_STEPS; i++) {
        if (!cur || (cur.execType !== "auto" && cur.execType !== "bootstrap")) break
        const res = run("advance", {}, workProjectFor(sid || rt.sessionId), GATE_TIMEOUT_MS)
        if (!res || res.ok === false) break
        cur = readState(sid) || cur
      }
      return cur
    }

    // Poda de ceremonia (2): un paso `llm_task` del camino de implementación
    // NO se desbloquea porque el agente se acuerde de llamar `advance`: se
    // desbloquea porque hay TRABAJO y VERIFICACIÓN. El `submit`/`transition`
    // queda para los gates de decisión (`user_input`), que expresan criterio
    // del usuario y no se auto-cruzan nunca.
    //
    // Condición estricta: hubo escritura (`wrote`) y verificación verde en la
    // sesión. Sin eso no se avanza: el paso no se salta por existir.
    const EVIDENCE_NODES = /^(IMPLEMENT|VERIFY)\./
    const driveEvidence = (current, sid, entry) => {
      if (!entry || !current) return current
      if (!entry.wrote || !entry.verified || entry.verifyFailed) return current
      // La verificación tiene que ser POSTERIOR al momento en que se vio este
      // paso. Si no, un verde de la tarea anterior avanzaría un paso en el que
      // todavía no se trabajó (el estado `verified` es de la sesión).
      const verifiedAt = entry.verifiedAt ? Date.parse(entry.verifiedAt) : 0
      if (!verifiedAt || verifiedAt < (entry.evidenceSeenAt || 0)) return current
      // Solo el paso que EFFECTIVAMENTE se vio (y por lo tanto se trabajó).
      if (entry.evidencePosition !== current.position) return current
      let cur = current
      for (let i = 0; i < MAX_AUTO_STEPS; i++) {
        if (!cur || cur.execType !== "llm_task" || cur.satisfied) break
        if (!EVIDENCE_NODES.test(String(cur.position || ""))) break
        const from = cur.position
        const res = run("advance", {}, workProjectFor(sid || rt.sessionId), GATE_TIMEOUT_MS)
        if (!res || res.ok === false) break
        cur = readState(sid) || cur
        if (cur.position === from) break
        tracePush({
          event_type: "workflow_evidence_advance",
          source: "agent_hook",
          session_id: sid || entry.sessionID,
          data: { from, to: cur.position, reason: "trabajo verificado", verified: true },
        })
        // Un paso por refresh: la evidencia cubre el paso que se vio, no los
        // siguientes (esos necesitan su propio trabajo y su propio verde).
        break
      }
      return cur
    }

    const refresh = (sid) => {
      if (Date.now() - state.fetchedAt < REFRESH_MS) return state.data
      const entry = sessionOf(sid || rt.sessionId)
      state.data = driveAuto(driveEvidence(readState(sid), sid, entry), sid)
      if (state.data) {
        // Marca de cuándo se vio este paso por primera vez: la evidencia que
        // lo avanza tiene que ser posterior (ver `driveEvidence`).
        if (entry.evidencePosition !== (state.data.position || "")) {
          entry.evidencePosition = state.data.position || ""
          entry.evidenceSeenAt = Date.now()
        }
        if (state.data.next) {
          // Última sugerencia observada: la firma del drift (qué pidió el motor).
          entry.lastSuggestion = {
            tool: state.data.next.tool,
            position: state.data.position || "",
          }
        }
      }
      state.fetchedAt = Date.now()
      return state.data
    }

    // ¿Hay tarea declarada? `rt.activeTask` solo se entera si la declaración
    // entra por la TOOL del plugin; el camino normal es el CLI (otro proceso).
    // Por eso además se consulta el estado: `task_id` deja de ser `auto-…`
    // cuando `implement_task` persiste la spec (medido antes/después).
    // ¿Hay tarea declarada? `rt.activeTask` solo se entera si la declaración
    // entra por la TOOL del plugin o por el CLI observado; el estado del
    // workflow es del PROYECTO, no de la sesión — por eso se cachea por sesión.
    const declCache = new Map()
    const isDeclared = (sessionID) => {
      try {
        const key = sessionID || "global"
        if (declared.has(key)) return true
        const mine = boundTasks.get(key) || []
        if (mine.some((t) => String(t.session_id || "") === key)) return true
        // OJO: `rt.activeTask` NO cuenta acá. `isBound` también lo tocaba y eso
        // abría el gate de tarea con solo tener sprint (bug medido: baddeclare
        // daba PERMITE donde debía BLOQUEAR).
        const hit = declCache.get(key)
        if (hit && Date.now() - hit.at < 3_000) return hit.value
        const s = run("workflow_state", {}, rt.projectId, GATE_TIMEOUT_MS)
        // FAIL-OPEN: si walker no responde (caído, timeout, JSON inválido) no se
        // puede verificar la declaración. Bloquear la escritura en ese caso es
        // exactamente "el agente se rompió": el protocolo es un servicio, no un
        // requisito de funcionamiento de OpenCode.
        if (!s || s.ok === false) {
          degrade(null, "workflow_state no disponible: gate abierto", "sin respuesta")
          declCache.set(key, { at: Date.now(), value: true })
          return true
        }
        const tid = String(s.task_id || "")
        const ok = Boolean(tid) && !tid.startsWith("auto-")
        declCache.set(key, { at: Date.now(), value: ok })
        if (ok) {
          declared.set(key, { taskId: tid, at: Date.now() })
          state.fetchedAt = 0
        }
        return ok
      } catch (err) {
        // Cualquier excepción acá también es fail-open.
        degrade(null, "isDeclared falló: gate abierto", err)
        return true
      }
    }

    // ¿La sesión tiene sprint (delta-sprint)? `session_bind mode=auto` es la
    // fuente autoritativa y es session-scoped, a diferencia del `task_id` del
    // workflow, que es del proyecto: sin esto, una sesión podía escribir porque
    // OTRA sesión ya había declarado tarea.
    const boundCache = new Map()
    // Tareas del sprint de la sesión, tal como las devuelve `session_bind`.
    // Es la señal session-scoped de "hay tarea declarada"; el task_id del
    // workflow es del PROYECTO y otra sesión lo pisa (medido: mi sesión perdió
    // la declaración cuando corrió un e2e en el mismo proyecto).
    const boundTasks = new Map()
    const bindFailures = new Map()
    // Sesiones donde el protocolo no se puede satisfacer (walker roto para
    // bind/declaración): se abren del todo. Un protocolo insatisfacible no
    // puede dejar la sesión muerta.
    const forceOpen = new Set()
    const isBound = (sessionID) => {
      try {
        const key = sessionID || "global"
        const hit = boundCache.get(key)
        if (hit && Date.now() - hit.at < 3_000) return hit.value
        const res = run(
          "session_bind",
          { session_id: key, project_id: rt.projectId, mode: "auto" },
          rt.projectId,
          GATE_TIMEOUT_MS
        )
        const data = res?.data || res
        // FAIL-OPEN: sin respuesta no se puede verificar; el protocolo es un
        // servicio, no un requisito de funcionamiento de OpenCode.
        if (!data || data.ok === false || !data.decision) {
          degrade(null, "session_bind no disponible: gate abierto", "sin respuesta")
          boundCache.set(key, { at: Date.now(), value: true })
          return true
        }
        const value = data.decision === "bound"
        boundCache.set(key, { at: Date.now(), value })
        boundTasks.set(key, (data.bound && data.bound.tasks) || [])
        if (value) {
          // Señal SEPARADA de la tarea: `rt.bound` dice "hay sprint", no
          // "hay tarea". Mezclarlas abría el gate de tarea sin tarea.
          rt.bound = (data.bound || {}).spring_id || "?"
        }
        return value
      } catch (err) {
        degrade(null, "isBound falló: gate abierto", err)
        return true
      }
    }

    try {
      if (!ctx?.session?.hook) {
        degrade(rt, "session.hook missing", "el runtime no expone el hook de contexto")
      } else await ctx.session.hook("context", async (event) => {
        // El motor se conduce igual (los `auto` corren sin tokens), pero el
        // texto solo se inyecta cuando hay una TAREA declarada. Medido: la
        // inyección en sesiones ad-hoc hacía que el agente caminara el
        // workflow por su cuenta (4 → 15 pasos sin que nada lo exigiera).
        const sid = event.sessionID || event.sessionId || ""
        refresh(sid)
        rt.sessionId = rt.sessionId || sid
        const entry = sessionOf(sid)
        if (rt.projectId && sid && (!entry.opened || entry.closed)) {
          // `closed` significa "sin actividad", no terminal: si la sesión vuelve
          // a usarse en otra ejecución, se reabre (open_session es idempotente y
          // limpia ended_at). Antes se cerraba en la PRIMERA ejecución.
          entry.opened = true
          entry.closed = false
          // Jerarquía (F5.4): `Session.Info.parentID` es la fuente. Un subagente
          // de OpenCode es una sesión hija; sin este dato la sesión quedaba como
          // raíz y su costo no se podía agregar a la tarea que lo lanzó.
          // Fail-open: si `session.get` no existe o falla, se abre sin padre.
          let parentId = ""
          try {
            if (ctx?.session?.get) {
              const info = await ctx.session.get({ sessionID: sid })
              parentId = String(info?.parentID || info?.parent_id || "")
              if (!entry.model) entry.model = String(info?.model?.id || info?.model || "")
            }
          } catch (err) {
            degrade(null, "session.get falló", err)
          }
          entry.parentSessionId = parentId
          // El pedido del usuario se captura ANTES de abrir la sesión: es el
          // único ancla del intake. Antes se tomaba después y `session_open`
          // quedaba con el placeholder `sesión <id>`; el engine nunca veía la
          // request (medido 2026-11-21: 0 pares sesión↔spec).
          if (!rt.sessionGoal) rt.sessionGoal = firstUserText(event)
          // Registro de continuidad: ciclo de vida + jerarquía. Fail-open: si
          // walker no responde, la sesión sigue igual.
          run(
            "session_open",
            {
              session_id: sid,
              project_id: rt.projectId,
              agent: String(event.agent || ""),
              ...(rt.sessionGoal ? { goal: rt.sessionGoal } : {}),
              ...(parentId ? { parent_session_id: parentId } : {}),
              ...(entry.model ? { model: entry.model } : {}),
            },
            rt.projectId,
            HOOK_TIMEOUT_MS
          )
        }
        // Primer turno con tarea declarada: UNA llamada a `brief` (absorbe
        // agent_context + preflight) y se INYECTA. Antes se calculaba y se
        // tiraba (`rt.pendingBaseline = null`): dos CLI de ~600 ms por nada.
        // La inyección ocurre sólo si hay tarea: en sesiones ad-hoc el agente
        // caminaba el workflow por su cuenta (medido: 4 → 15 pasos).
        if (!rt.baselineDone && rt.projectId) {
          const planned = planFor(sid) || {}
          // Sólo la tarea DE ESTA SESIÓN. `state.data.taskId` es del proyecto y
          // hacía que una sesión nueva inyectara el flight card de otra tarea
          // (medido en el harness: se inyectó `f1-f2-…` en sesiones fixture).
          const declared = planned.task || ""
          if (declared && !entry.briefInjected) {
            rt.baselineDone = true
            entry.briefInjected = true
            const started = Date.now()
            const card = run(
              "brief",
              {
                session_id: sid,
                task_id: String(declared),
                // El pedido original: el brief lo usa para recuperar decisiones
                // y skills del área, no sólo el baseline del repo.
                ...(rt.sessionGoal ? { text: String(rt.sessionGoal).slice(0, 1500) } : {}),
              },
              workProjectFor(sid),
              HOOK_TIMEOUT_MS
            )
            const text = briefBlock(card)
            if (text) {
              rt.pendingBrief = text
              tracePush({
                event_type: "brief_injected",
                source: "agent_hook",
                session_id: sid,
                data: {
                  trigger: "intake",
                  task_id: String(declared),
                  chars: text.length,
                  ms: Date.now() - started,
                  decisions: (card?.decisions || []).length,
                  cached: Boolean(card?.impact?.cached),
                  degraded: Boolean(card?.degraded),
                  gaps: (card?.self?.gaps || []).map((g) =>
                    typeof g === "string" ? g : String(g?.code || "")
                  ).filter(Boolean),
                },
              })
            }
          }
        }
        // Probe de forma (una vez por sesión): ¿`event.messages` sirve como
        // canal? Se mide, no se asume. Costo: un trace por sesión.
        if (!entry.shapeProbed) {
          entry.shapeProbed = true
          const shape = describeMessages(event?.messages)
          tracePush({
            event_type: "context_probe",
            source: "agent_hook",
            session_id: sid,
            data: { ...shape, mode: INJECTION_MODE, system_blocks: Array.isArray(event?.system) ? event.system.length : -1 },
          })
        }
        // El baseline viejo (multilínea) ya no se inyecta: el flight card lo
        // reemplaza. Se consume para no acumularlo; la traza queda.
        rt.pendingBaseline = null
        // Hidratación del estado de vuelo desde el TRACE (SSOT): una recarga del
        // plugin no pierde `verified`/`wrote` (ver `hydrateFlight`).
        hydrateFlight(sid, entry)
        let block = ""
        const st = state.data
        // Decisión POR SESIÓN: `state.data.taskId` es del proyecto y hacía que
        // una sesión nueva heredara "tarea declarada" de otra (medido en el
        // harness: el bloque de binding no aparecía nunca).
        if (!block) block = INJECTION_MODE === "once" ? planBlock(sid, entry) : planText(sid, entry)
        if (block) entry.bootstrapped = true // ya se mostró el plan: no ofrecer binding
        // El binding/bootstrap es un AVISO de una vez, no estado: no entra al
        // conjunto contextual (si entrara, se repetiría para siempre).
        let nudge = ""
        if (!block && !entry.bootstrapped) {
          // Antes de escribir: ofrecer continuidad (sprint/goal previo) en vez
          // de solo pedir "declará la tarea".
          const bind = bindBlock(sid, rt.sessionGoal)
          nudge = bind.text
          if (bind.decision) entry.bind = bind.decision
          entry.bootstrapped = true
        }
        // En un gate de decisión la pregunta del nodo SIEMPRE se agrega, aunque
        // el plan ya se haya mostrado: es el paso que está bloqueado. Va como
        // bloque APARTE para no romper el contrato de atención del plan (400).
        // En `stable` se recalcula el texto sin dedupe: el request necesita los
        // mismos bytes, no "una vez y nunca más".
        const gate =
          INJECTION_MODE === "once" ? gateBlock(sid, entry, st) : gateText(sid, st)
        // Fallo de comando: el contexto llega sin que el modelo tenga que pedirlo.
        // Es un EVENTO, no estado: se inyecta una vez por firma y como máximo 2
        // por sesión. Repetirlo no informa más y compite por atención.
        let failureText = ""
        if (entry.lastFailure && !entry.lastFailure.injected && (entry.failureBriefs || 0) < 2) {
          const failure = entry.lastFailure
          const started = Date.now()
          const declaration = entry.planTask || ""
          const card = run(
            "brief",
            {
              session_id: sid,
              text: String(failure.text || failure.command || "").slice(-600),
              ...(declaration ? { task_id: String(declaration) } : {}),
            },
            workProjectFor(sid),
            HOOK_TIMEOUT_MS
          )
          failureText = failureBriefBlock(card, failure)
          failure.injected = true // aunque el CLI falle: no reintentar en loop
          if (failureText) {
            entry.failureBriefs = (entry.failureBriefs || 0) + 1
            tracePush({
              event_type: "brief_injected",
              source: "agent_hook",
              session_id: sid,
              data: {
                trigger: "failure",
                command: String(failure.command || "").slice(0, 160),
                exit: failure.exitCode ?? null,
                chars: failureText.length,
                ms: Date.now() - started,
                decisions: (card?.decisions || []).length,
                gaps: (card?.self?.gaps || []).map((g) =>
                  typeof g === "string" ? g : String(g?.code || "")
                ).filter(Boolean),
              },
            })
          }
        }
        // El flight card es ESTADO del plan: en `stable` se conserva para
        // repetirlo idéntico; en `once` se manda una vez y se descarta.
        const incomingBrief = rt.pendingBrief || ""
        if (rt.pendingBrief) {
          entry.briefText = rt.pendingBrief
          rt.pendingBrief = null
        }
        const briefText = INJECTION_MODE === "once" ? "" : entry.briefText || ""

        // FC-INV-17 · El paso del motor está DELANTE. El brief con su `paso`
        // se congela en el intake; el plan muestra el `next` que escribió el
        // agente. El `next_action` del MOTOR nunca llegaba al modelo, así que
        // seguirlo era opcional y por eso no se seguía. Línea corta (≤120),
        // regenerada cuando cambia la firma (posición/revisión).
        const stepSig = `${st?.position || ""}|${st?.revision ?? ""}|${st?.next?.tool || ""}`
        let stepText = (() => {
          if (!st || !st.position) return ""
          if (!entry.planTask && !rt.bound) return ""
          const next = st.next && st.next.tool ? ` · motor pide: ${st.next.tool}` : ""
          const type = st.execType ? ` (${st.execType})` : ""
          return `[WALKER] paso ${st.position}${type}${next}`.slice(0, 120)
        })()
        if (stepText && INJECTION_MODE === "once") {
          // En `once` (el control del A/B) se manda una vez por firma, igual
          // que el plan: repetirlo rompería la línea base que compara.
          if (entry.stepSig === stepSig) stepText = ""
          else entry.stepSig = stepSig
        }
        entry.position = st?.position || entry.position || ""

        if (INJECTION_MODE === "once") {
          // Camino previo (línea base del A/B): el plan se repite, gate/brief/
          // fallo se mandan una sola vez. No se toca: es el control.
          const legacy = [block, stepText, gate, incomingBrief, failureText].filter(Boolean)
          if (!legacy.length) return
          const res = applyInjection(event, legacy, "once")
          if (res.channel === "none") {
            degrade(rt, "event.system no es array", typeof event?.system)
            return
          }
          entry.injections += 1
          entry.injectionMode = "once"
          entry.position = st?.position || ""
          return
        }

        // `stable` / `messages`: el conjunto contextual se memoiza por firma.
        // La firma incluye lo que puede cambiar el texto de los bloques.
        const sig = [
          st?.position || "",
          st?.revision ?? "",
          st?.prompt?.template_id || "",
          entry.planTask || "",
          (entry.briefText || "").length,
          // SPEC-014: el bloque del plan cambia con el conteo de verificación.
          entry.verifyCounts
            ? `${entry.verifyCounts.passed}/${entry.verifyCounts.total}`
            : "",
        ].join("|")
        const planned = planBlocks({
          blocks: [block, stepText, gate, briefText].filter(Boolean),
          sig,
          memo: entry.injectMemo,
        })
        const contextual = planned.blocks
        if (planned.changed && contextual.length) entry.injectMemo = { sig, blocks: contextual }
        // El aviso de binding no se memoiza: va una vez y se olvida. El fallo
        // tampoco: es un evento.
        const blocks = contextual.concat([nudge, failureText].filter(Boolean))
        if (!blocks.length) return

        const applied = applyInjection(event, blocks, INJECTION_MODE)
        if (applied.channel === "none") {
          degrade(rt, "event.system no es array", typeof event?.system)
          return
        }
        if (applied.fallback) {
          // Visible, no degradación silenciosa: el modo pedido no se pudo usar.
          tracePush({
            event_type: "injection_fallback",
            source: "agent_hook",
            session_id: sid,
            data: { mode: INJECTION_MODE, channel: applied.channel, reason: applied.fallback },
          })
        }
        if (planned.changed) {
          tracePush({
            event_type: "injection_prefix",
            source: "agent_hook",
            session_id: sid,
            data: {
              mode: INJECTION_MODE,
              channel: applied.channel,
              blocks: applied.applied,
              chars: blocks.join("").length,
              sig,
              reason: planned.reason,
            },
          })
        }
        entry.injections += 1
        entry.injectionMode = INJECTION_MODE
        entry.position = st?.position || ""
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

          // Recarga del plugin: el estado de vuelo persistido se hidrata antes
          // de decidir el cierre. Si no, una sesión sucia "desaparece" al
          // recargar y el cierre pasa sin verificación (medido).
          hydrateFlight(sid, entry)

          // ── Telemetría por TURNO (AE-INV-10) ──
          // El cache de prefijo se juega por request, no por sesión: agregar
          // `cacheRead` al final no dice dónde se rompió. Se publica el delta
          // contra la lectura anterior, con el modo de inyección vigente.
          if (entry.opened && entry.sessionID) {
            const usage = await sessionCost(entry.sessionID)
            const nowTok = usage.tokens || { input: 0, output: 0, cacheRead: 0 }
            // Primera lectura de la sesión: es la LÍNEA BASE, no un turno. Sin
            // esto un reload del plugin publicaba todo el acumulado como si
            // fuera un turno de un millón de tokens.
            if (!entry.usage) {
              entry.usage = nowTok
            } else {
              const prevTok = entry.usage
              const delta = {
                input: nowTok.input - prevTok.input,
                output: nowTok.output - prevTok.output,
                cacheRead: nowTok.cacheRead - prevTok.cacheRead,
                cacheWrite: (nowTok.cacheWrite || 0) - (prevTok.cacheWrite || 0),
                reasoning: (nowTok.reasoning || 0) - (prevTok.reasoning || 0),
              }
              if (delta.input > 0 || delta.output > 0 || delta.cacheRead > 0) {
                entry.usage = nowTok
                entry.turns = (entry.turns || 0) + 1
                tracePush({
                  event_type: "token_turn",
                  source: "agent_hook",
                  session_id: entry.sessionID,
                  data: {
                    turn: entry.turns,
                    input: delta.input,
                    output: delta.output,
                    cache_read: delta.cacheRead,
                    cache_write: delta.cacheWrite,
                    reasoning: delta.reasoning,
                    hit_rate: delta.input > 0 ? Number((delta.cacheRead / delta.input).toFixed(3)) : null,
                    // `write_ratio` alto con `hit_rate` bajo es la firma de un
                    // prefijo invalidado: se pagó de nuevo todo el contexto.
                    write_ratio:
                      delta.input > 0 ? Number((delta.cacheWrite / delta.input).toFixed(3)) : null,
                    injection_mode: entry.injectionMode || INJECTION_MODE,
                    cost_usd: usage.cost,
                  },
                })
              }
            }
          }

          // ── Gate de CIERRE: hubo escritura y no hay verificación ──
          // No se puede impedir el fin de ejecución, así que se reinyecta un
          // mensaje sintético. Rondas: hasta CLOSE_ROUNDS-1 insiste; en la
          // última escala al humano y después calla. Antes era UNA vez y luego
          // nada: la sesión quedaba "verde" sin evidencia (255 `close_blocked`).
          // Antes de nudgar se fuerza la hidratación: walker pudo registrar el
          // verde desde la TOOL y el plugin no haber visto el comando.
          if (
            event.type === "session.execution.succeeded" &&
            entry.opened &&
            entry.wrote &&
            (!entry.verified || entry.verifyFailed) &&
            entry.sessionID
          ) {
            hydrateFlight(sid, entry, { force: true })
          }
          if (
            event.type === "session.execution.succeeded" &&
            entry.opened &&
            entry.wrote &&
            (!entry.verified || entry.verifyFailed) &&
            entry.sessionID
          ) {
            entry.closeRounds = (entry.closeRounds || 0) + 1
            const round = entry.closeRounds
            if (round <= CLOSE_ROUNDS) {
              const escalate = round === CLOSE_ROUNDS
              const plan = planFor(entry.sessionID)
              try {
                if (ctx?.session?.synthetic) {
                  await ctx.session.synthetic({
                    sessionID: entry.sessionID,
                    text: escalate
                      ? escalateBlock(rt, plan, entry, workProjectFor(entry.sessionID))
                      : closeBlock(rt, plan, entry, workProjectFor(entry.sessionID)),
                  })
                }
              } catch (err) {
                degrade(rt, "close nudge falló", err)
              }
              if (escalate) entry.closeEscalated = true
              tracePush({
                event_type: "close_blocked",
                source: "agent_hook",
                session_id: entry.sessionID,
                data: {
                  reason: entry.verifyFailed ? "verificación falló" : "sin verificación",
                  round,
                  limit: CLOSE_ROUNDS,
                  escalation: escalate,
                  stamp: verificationStamp(entry, plan).kind,
                  wrote: true,
                  verify_failed: Boolean(entry.verifyFailed),
                },
              })
            }
          }

          if (entry.opened && !entry.metricsSent && entry.sessionID) {
            entry.metricsSent = true
            const usage = await sessionCost(entry.sessionID)
            tracePush({
              event_type: "session_metrics",
              source: "agent_hook",
              session_id: entry.sessionID,
              data: {
                writes: entry.writes || 0,
                // F2 (ADR-WALKER-086): la métrica 4D viaja con la sesión. Es
                // lo que faltaba para poder decir "esta spec usó 2/3 archivos"
                // o "esta sesión trabajó sin contrato".
                contract: entry.contract4d
                  ? {
                      present: entry.contract4d.present,
                      spec_id: entry.contract4d.spec_id,
                      goal: entry.contract4d.goal,
                      criteria: entry.contract4d.criteria,
                    }
                  : { present: false, spec_id: "", goal: "", criteria: { mapped: 0, unmapped: 0 } },
                budget: budgetUsage(entry.contract4d, Array.from(entry.touched || [])),
                touched_count: entry.touched.size,
                touched: Array.from(entry.touched).slice(0, 20),
                scope_denials: Array.from(entry.scopeDenied || []).slice(0, 10),
                scope_expanded: Array.from(entry.scopeExpanded || []).slice(0, 10),
                verified: Boolean(entry.verified),
                verified_at: entry.verifiedAt || "",
                dirty_since: entry.dirtySince || "",
                verify_level: entry.verifyLevel || "",
                verify_failed: Boolean(entry.verifyFailed),
                cost_usd: usage.cost,
                tokens: usage.tokens,
                turns: entry.turns || 0,
                close_rounds: entry.closeRounds || 0,
                close_escalated: Boolean(entry.closeEscalated),
                injection_mode: entry.injectionMode || INJECTION_MODE,
                stamp: verificationStamp(entry, planFor(entry.sessionID)).kind,
                plan_task: entry.planTask || "",
                bind: entry.bind || "",
                baseline_injected: Boolean(entry.baselineInjected),
                baseline_files: (entry.baselineFiles || []).slice(0, 2),
                first_mutation: entry.firstMutation || null,
                adherence: entry.firstMutation
                  ? {
                      after_baseline: entry.firstMutation.after_baseline,
                      in_baseline: entry.firstMutation.in_baseline,
                    }
                  : null,
              },
            })
          }
          if (entry.opened && !entry.closed && entry.sessionID) {
            entry.closed = true
            const stamp = verificationStamp(entry, planFor(entry.sessionID))
            // Costo/tokens al cierre: quedan en la sesión del delta para que la
            // jerarquía publique costo por subagente sin recorrer la traza.
            const usage = await sessionCost(entry.sessionID)
            run(
              "session_close",
              {
                session_id: entry.sessionID,
                outcome: String(event.type || "").replace("session.execution.", ""),
                stamp,
                ...(usage.cost != null ? { cost_usd: usage.cost } : {}),
                ...(usage.tokens ? { tokens: usage.tokens } : {}),
              },
              rt.projectId,
              HOOK_TIMEOUT_MS
            )
          }
          if (entry.evicted > 0 && !entry.evictionTraced) {
            entry.evictionTraced = true
            tracePush({
              event_type: "session_evicted",
              source: "agent_hook",
              session_id: entry.sessionID,
              data: {
                evicted: entry.evicted,
                evicted_keys: (entry.evictedKeys || []).slice(0, 20),
                size: sessions.size,
                max: MAX_SESSIONS,
              },
            })
          }
          if (entry.injections > 0) {
            // El drift se mide con la sugerencia REAL del motor y con la acción
            // que el agente hizo. Antes el evento sólo traía `position` (y a
            // menudo vacío): se contaba el desvío sin poder decir de qué.
            const suggestion = entry.lastSuggestion || {}
            const did = entry.followedBy || "none"
            tracePush({
              event_type: "workflow_state_injected",
              source: "agent_hook",
              session_id: entry.sessionID,
              data: {
                injections: entry.injections,
                position: entry.position,
                suggested_next: String(suggestion.tool || ""),
                suggested_position: String(suggestion.position || ""),
                did,
                followed: entry.followed,
                followedBy: entry.followedBy || "",
                bind: entry.bind || "",
                planTask: entry.planTask || "",
                wrote: entry.wrote,
                verified: entry.verified,
                verifiedBy: entry.verifiedBy || "",
                sessionsState: Array.from(sessions.entries()).map(([k, v]) => ({
                  k, w: v.wrote, i: v.injections, v: v.verified, e: v.evicted || 0,
                })),
                outcome: event.type,
              },
            })
            if (!entry.followed) {
              tracePush({
                event_type: "workflow_suggestion_ignored",
                source: "agent_hook",
                session_id: entry.sessionID,
                data: {
                  injections: entry.injections,
                  position: entry.position || String(suggestion.position || ""),
                  suggested_next: String(suggestion.tool || ""),
                  did,
                  followedBy: entry.followedBy || "",
                  wrote: Boolean(entry.wrote),
                  verified: Boolean(entry.verified),
                  reason: "sin accion del agente en la ejecucion",
                },
              })
            }
          }
          // Reset PER-EJECUCIÓN. El estado PER-SESIÓN (firstMutation,
          // scopeDenied, writes, verified, metricsSent, closed, touched,
          // planSig, baseline*) SOBREVIVE entre ejecuciones: antes
          // `sessions.delete` lo borraba y las métricas salían vacías.
          entry.injections = 0
          entry.followed = false
          entry.followedBy = ""
          entry.position = ""
          entry.metricsSent = false
          // `closeRounds` NO se resetea: la cuenta es de la sesión, no de la
          // ejecución. Resetearlo fue lo que hacía que el nudge se repitiera
          // para siempre (255 close_blocked) sin escalar nunca.
          entry.closeNudged = false
        }
      } catch (err) {
        degrade(rt, "event.subscribe falló", err)
      }
    })()

    // La declaración por CLI ocurre en OTRO proceso: se detecta observando el
    // comando y se registra por sesión. Sin esto, `isDeclared` no se entera y el
    // gate deja la sesión bloqueada justo después de que el agente obedeció.
    try {
      if (!ctx?.tool?.transform) throw new Error("tool.transform no disponible")
      await ctx.tool.transform((editor) => {
        const shell = editor.get("shell")
        if (!shell || typeof shell.execute !== "function") return
        editor.update("shell", (draft) => {
          const original = draft.execute
          draft.execute = async (input, callCtx) => {
            const cmd = String(input?.command || "")
            const isCli =
              /^\s*(?:cd\s+[^\s;&|]+\s*&&\s*)?(?:\S*\/)?walker3\s/.test(cmd)
            const result = await original(input, callCtx)
            const key = String(callCtx?.sessionID || rt.sessionId || "global")

            // Fallo de verificación/build: se guarda para que el hook de
            // contexto inyecte el flight card del área en el próximo request
            // del MISMO turno. Antes el error quedaba sólo en la salida y el
            // modelo improvisaba el diagnóstico.
            const rawOutput =
              typeof result === "string"
                ? result
                : String(result?.output ?? result?.stdout ?? result?.data?.output ?? "")
            const rawExit = Number(
              result?.exitCode ?? result?.exit_code ?? result?.metadata?.exitCode ?? NaN
            )
            const isCheck = /\b(pytest|run_tests|verify_predicates|npm\s+(?:run\s+)?test|make\s+test|ruff|mypy|tsc|node\s+--test)\b/.test(cmd)
            if (isCheck) {
              const failedEntry = sessionOf(key)
              if (rawExit > 0) {
                const tail = rawOutput.slice(-800)
                failedEntry.lastFailure = {
                  command: cmd.slice(0, 160),
                  exitCode: rawExit,
                  text: tail,
                  signature: `${cmd.slice(0, 120)}|${rawExit}|${tail.slice(0, 120)}`,
                  injected: false,
                }
              } else if (rawExit === 0) {
                failedEntry.lastFailure = null
              }
            }

            if (isCli) {
              // El plan cambió por CLI: el cache de `planFor` (10s) deja de
              // valer. Sin esto, el auto-declare leía el plan viejo y seguía
              // bloqueando una escritura que el plan ya declaraba.
              if (/\bsession_plan\b/.test(cmd)) planCache.delete(key)
              const step = String(cmd).match(/(?:^|\s)(?:\S+\/)?(submit|transition)(?=\s|$)/)
              if (step) {
                const e = sessionOf(key)
                const before = e.position
                state.fetchedAt = 0 // releer: el paso pudo cambiar el nodo
                const after = readState(key)
                // Solo cuenta si hubo progreso real; un typo en el JSON no marca.
                if (after && (after.satisfied || after.position !== before)) {
                  e.followed = true
                  e.followedBy = "cli"
                }
              }
            }

            // Segunda vía de `wrote`: el wrapper de shell corre siempre, así que
            // cubre el caso en que el agente escribe por shell en vez de `write`.
            // `shellMutation` ignora /dev/null, `2>&1`, /tmp y los `>` citados.
            if (shellMutation(cmd).mutates) {
              sessionOf(key).wrote = true
            }

            // Evidencia de verificación (gate de cierre).
            //
            // Dos caminos:
            //  1. `walker3 <proyecto>/run_tests|verify_predicates` ES la tool de
            //     verificación: ejecuta pytest/predicados y devuelve el
            //     resultado. En Code Mode el agente la invoca por shell (las MCP
            //     tools no pasan por este wrapper), así que con `acceptance`
            //     declarada el gate nunca podría abrirse. Se certifica por el
            //     RESULTADO, no por el nombre: un `echo pytest` sigue sin contar.
            //  2. `pytest` suelto: compat, solo certifica si no hay acceptance.
            const cliVerify = cmd.match(
              /(?:^|\s)(?:\S*\/)?walker3\s+\S+\/(run_tests|verify_predicates)\b/
            )
            // Con pipe el exit code es el del ÚLTIMO comando (`tail` → 0),
            // así que no puede usarse como evidencia. Sólo el marcador.
            const hasPipe = cliVerify ? /\|/.test(cmd) : false
            if (cliVerify) {
              const e = sessionOf(key)
              const output =
                typeof result === "string"
                  ? result
                  : String(
                      (result &&
                        (result.output ?? result.stdout ?? result.data?.output)) ||
                        ""
                    )
              const exitCode = Number(
                (result &&
                  (result.exitCode ??
                    result.exit_code ??
                    result.metadata?.exitCode)) ??
                  NaN
              )
              // 1. Marcador estable (contrato nuevo): gana sobre contadores y
              // sobre el exit code. Es una línea final, sobrevive al recorte.
              const marker = output.match(/(?:^|\n)\s*WALKER_RESULT\b([^\n\r]*)/)
              if (marker) {
                const kv = {}
                for (const m of marker[1].matchAll(/([a-z_]+)=(\S+)/g)) kv[m[1]] = m[2]
                const failed = Number(kv.failed || 0)
                const errors = Number(kv.errors || 0)
                const exit = Number(kv.exit || 0)
                const green =
                  kv.ok === "1" && failed === 0 && errors === 0 && exit === 0
                const wasVerifiedMarker = e.verified === true
                e.verifyLevel = hasPipe ? "cli-marker-pipe" : "cli-marker"
                e.verifiedBy = cliVerify[1]
                e.verified = green
                e.verifyFailed = !green
                e.verifyHint = ""
                if (green) {
                  e.verifiedAt = new Date().toISOString()
                  e.dirtySince = ""
                  if (!wasVerifiedMarker) {
                    tracePush({
                      event_type: "session_state",
                      source: "agent_hook",
                      session_id: key,
                      data: {
                        verified: true,
                        verified_at: e.verifiedAt,
                        verify_level: e.verifyLevel,
                        dirty_since: "",
                        wrote: Boolean(e.wrote),
                        writes: e.writes || 0,
                        touched: Array.from(e.touched || []).slice(0, 20),
                      },
                    })
                  }
                  // Auto-capture proactivo (una vez por sesión).
                  if (!rt.captured && rt.activeTask && e.wrote) {
                    try {
                      const sid = key
                      const touched = Array.from(e.touched || []).slice(0, 50)
                      run(
                        "capture",
                        {
                          session_id: sid,
                          task_id: String(rt.activeTask.taskId || ""),
                          evidence: "auto: verificacion en verde",
                          artifacts: touched,
                        },
                        rt.projectId,
                        HOOK_TIMEOUT_MS
                      )
                      rt.captured = true
                      tracePush({
                        event_type: "auto_capture",
                        source: "agent_hook",
                        session_id: sid,
                        data: { task_id: rt.activeTask.taskId, files: touched.length },
                      })
                    } catch (err) {
                      degrade(rt, "auto-capture falló", err)
                    }
                  }
                }
              } else if (hasPipe) {
                // No hay marcador y el exit code no es confiable: no se
                // certifica. No se toca `verified`; el nudge explica el motivo.
                e.verifyLevel = "cli-pipe"
                e.verifyHint = "pipe"
              } else if (!output.trim()) {
                // Sin salida no hay evidencia: el resultado pudo irse a un
                // archivo (`> log`) o la tool lo recortó. Antes esta rama no
                // marcaba nada y el nudge quedaba mudo (medido 2026-11-21).
                e.verifyLevel = "cli"
                e.verifyHint = "hidden"
              } else {
                // 2. Compatibilidad: parseo por contadores/exit code. El
                // resultado puede venir recortado (preview de la tool): no
                // alcanza con buscar `all_satisfied`; los contadores y el
                // returncode quedan al final del JSON.
                const failedCount = output.match(/"failed_count"\s*:\s*(\d+)/)
                const returnCode = output.match(/"returncode"\s*:\s*(-?\d+)/)
                const red =
                  exitCode > 0 ||
                  /"passed"\s*:\s*false/.test(output) ||
                  /"all_satisfied"\s*:\s*false/.test(output) ||
                  (failedCount && Number(failedCount[1]) > 0) ||
                  (returnCode && Number(returnCode[1]) !== 0) ||
                  /"ok"\s*:\s*false/.test(output) ||
                  /"error"\s*:/.test(output)
                const green =
                  output.trim().length > 0 &&
                  !red &&
                  (Number.isNaN(exitCode) ||
                    exitCode === 0 ||
                    /"passed"\s*:\s*true/.test(output) ||
                    /"all_satisfied"\s*:\s*true/.test(output) ||
                    (failedCount && Number(failedCount[1]) === 0) ||
                    (returnCode && Number(returnCode[1]) === 0))
                e.verifyLevel = "cli"
                e.verifiedBy = cliVerify[1]
                if (green && !red) {
                  const wasVerifiedCli = e.verified === true
                  e.verified = true
                  e.verifyFailed = false
                  e.verifyHint = ""
                  e.verifiedAt = new Date().toISOString()
                  e.dirtySince = ""
                  if (!wasVerifiedCli) {
                    tracePush({
                      event_type: "session_state",
                      source: "agent_hook",
                      session_id: key,
                      data: {
                        verified: true,
                        verified_at: e.verifiedAt,
                        verify_level: e.verifyLevel,
                        dirty_since: "",
                        wrote: Boolean(e.wrote),
                        writes: e.writes || 0,
                        touched: Array.from(e.touched || []).slice(0, 20),
                      },
                    })
                  }
                } else if (red) {
                  e.verified = false
                  e.verifyFailed = true
                }
              }
            } else if (/\b(pytest|run_tests|verify_predicates)\b/.test(cmd)) {
              const e = sessionOf(key)
              // P1.14: verificar ES seguir la sugerencia (antes sólo contaba
              // submit/transition y la métrica reportaba 63-81 "ignoradas").
              if (!e.followed) {
                e.followed = true
                e.followedBy = "verify"
              }
              const plan = planFor(key) || {}
              const hasAcceptance =
                Array.isArray(plan.acceptance) && plan.acceptance.length > 0
              // Antes `echo pytest` cerraba una tarea con criterios sin probar.
              if (!hasAcceptance) {
                const wasVerifiedShell = e.verified === true
                e.verified = true
                if (e.verifyLevel !== "tools") e.verifyLevel = "shell"
                e.verifiedBy = e.verifiedBy || "shell"
                e.verifiedAt = new Date().toISOString()
                e.dirtySince = ""
                if (!wasVerifiedShell) {
                  tracePush({
                    event_type: "session_state",
                    source: "agent_hook",
                    session_id: key,
                    data: {
                      verified: true,
                      verified_at: e.verifiedAt,
                      verify_level: e.verifyLevel,
                      dirty_since: "",
                      wrote: Boolean(e.wrote),
                      writes: e.writes || 0,
                      touched: Array.from(e.touched || []).slice(0, 20),
                    },
                  })
                }
              }
            }

            // El plan cambió (o la tarea): la caché de 10s ya no vale.
            if (/\b(session_plan|implement_task|session_bind)\b/.test(cmd)) {
              planCache.delete(key)
              rt.workProjectId = ""
              // P1.14: declarar/vincular/ampliar el plan es una acción REAL
              // sobre el protocolo. No es "ignorar la sugerencia".
              const fe = sessionOf(key)
              if (!fe.followed) {
                fe.followed = true
                fe.followedBy = "cli"
              }
            }

            if (isCli && /\bsession_bind\b/.test(cmd)) {
              // El binding corre en otro proceso: se verifica contra el estado y
              // se invalida la caché para que el gate abra en la próxima llamada
              // (sin esto, el agente obedece y el gate lo sigue bloqueando).
              const res = run(
                "session_bind",
                { session_id: key, project_id: rt.projectId, mode: "auto" },
                rt.projectId,
                GATE_TIMEOUT_MS
              )
              const d = res?.data || res
              const ok = Boolean(d) && d.decision === "bound"
              boundCache.set(key, { at: Date.now(), value: ok })
              if (ok) {
                bindFailures.delete(key)
              } else {
                const n = (bindFailures.get(key) || 0) + 1
                bindFailures.set(key, n)
                degrade(null, "session_bind no verificada", (d && d.decision) || "(sin respuesta)")
                if (n >= 2) {
                  forceOpen.add(key)
                  boundCache.set(key, { at: Date.now(), value: true })
                  degrade(null, "gate abierto tras 2 bindings fallidos", key)
                }
              }
            }

            if (isCli && /\bimplement_task\b/.test(cmd)) {
              // No alcanza con que el comando "parezca" la declaración: se
              // verifica contra el estado. Un typo en el JSON del CLI devuelve
              // error y la sesión quedaría marcada como declarada sin serlo.
              const tid = String(run("workflow_state", {}, workProjectFor(key))?.task_id || "")
              if (tid && !tid.startsWith("auto-")) {
                declared.set(key, {
                  taskId: tid,
                  at: Date.now(),
                  cmd: cmd.slice(0, 160),
                })
                declCache.set(key, { at: Date.now(), value: true })
                // El hook necesita saber que hay tarea para inyectar el prompt
                // del nodo en vez del bloque de binding.
                rt.activeTask = rt.activeTask || { taskId: tid, files: [] }
              } else {
                // No se pudo verificar: puede ser un typo o un walker a medias.
                // Se cuenta por sesión y, al segundo intento, se abre el gate:
                // un protocolo insatisfacible no puede dejar la sesión muerta.
                const n = (declareFailures.get(key) || 0) + 1
                declareFailures.set(key, n)
                degrade(null, "declaración por CLI no verificada", tid || "(sin task_id)")
                if (n >= 2) {
                  forceOpen.add(key)
                  degrade(null, "gate abierto tras 2 declaraciones fallidas", key)
                }
              }
            }
            return result
          }
        })
      })
    } catch (err) {
      degrade(rt, "tool.transform: declaración por CLI", err)
    }

    try {
      if (!ctx?.tool?.transform) throw new Error("tool.transform no disponible")
      await ctx.tool.transform((editor) => {
        for (const name of MUTATING_TOOLS) {
          const tool = editor.get(name)
          if (!tool || typeof tool.execute !== "function") continue
          editor.update(name, (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const cur = state.data
              // ── Gate de INTAKE: sin tarea declarada no se escribe ──
              // El desbloqueo se hace por `shell` (CLI de walker). Si el gate
              // bloqueara `shell`, no habría forma de declarar la tarea y la
              // sesión quedaría muerta: se permite SOLO la invocación del CLI.
              // El resto de shell sigue cubierto porque `echo >> archivo` es la
              // escritura que el gate de edit/write no ve (medido: 1/1 bypass).
              // Allowlist ESTRECHA: el comando tiene que EMPEZAR por el CLI de
              // walker (con `cd` opcional). Un `rm -rf x # walker3` no pasa.
              const isWalkerCli =
                name === "shell" &&
                /^\s*(?:cd\s+[^\s;&|]+\s*&&\s*)?(?:\S*\/)?walker3\s+\S+(?:\s+'[^']*')?\s*$/.test(
                  String(input?.command || "")
                )
              // ADR-084 F3a (FC-INV-04): la decisión de intake es por EFECTO.
              // `read` y `verify` no mutan: bloquearlos convertía observar en un
              // peaje (medido: `grep`/`--help` bloqueados antes de ligar). El
              // `unknown` NO se degrada a read (FC-INV-06).
              const shellCmd = String(input?.command || "")
              const effect =
                name === "shell" ? shellEffect(shellCmd, rt.projectDir).effect : "write"
              const isReadOrProtocol =
                name === "shell" && ["read", "verify", "protocol"].includes(effect)
              // En algunos caminos (p. ej. server standalone) el tool no trae
              // sessionID: sin fallback la marca de `wrote` caía en "".
              const sid = String(callCtx?.sessionID || rt.sessionId || "")
              if (
                DENY_INTAKE &&
                rt.projectId &&
                !isWalkerCli &&
                !isReadOrProtocol &&
                !forceOpen.has(sid || "global")
              ) {
                if (!isBound(sid)) {
                  tracePush({
                    event_type: "flight_control_denied",
                    source: "agent_hook",
                    session_id: sid,
                    data: { tool: name, gate: "bind" },
                  })
                  throw new Error(
                    `[WALKER] Escritura bloqueada: esta sesión no tiene sprint (delta-sprint).
` +
                      `No es un bug: es el protocolo de continuidad y se abre en dos pasos.
` +
                      `  1) VINCULAR: ${CLI} ${rt.projectId}/session_bind '${JSON.stringify({
                        session_id: sid,
                        mode: "auto",
                      })}'
` +
                      `     Te dice si hay goal previo (resume_available) o nada (no_work).
` +
                      `  2) SEGÚN la respuesta, preguntale al usuario y ejecutá
` +
                      `     mode="continue" (mismo goal) o mode="new" (goal nuevo).
` +
                      `Después repetí exactamente la misma herramienta.`
                  )
                }
                if (!isDeclared(sid)) {
                  // P1.10: si el PLAN ya declara la tarea, se declara sola. El
                  // gate sigue existiendo para el caso "no hay plan": ahí el
                  // agente debe decir qué está haciendo. Medido: 7 escrituras
                  // bloqueadas por no repetir una tarea que el plan ya tenía.
                  const planned = String((planFor(sid) || {}).task || "").trim()
                  let auto = false
                  if (planned) {
                    try {
                      const res = run(
                        "session_plan",
                        {
                          session_id: sid,
                          project_id: rt.projectId,
                          mode: "set",
                          task: planned,
                        },
                        rt.projectId,
                        HOOK_TIMEOUT_MS
                      )
                      const data = res?.data || res
                      declCache.delete(sid || "global")
                      if (data && data.ok !== false && isDeclared(sid)) {
                        auto = true
                        rt.activeTask = rt.activeTask || { taskId: planned, files: [] }
                        tracePush({
                          event_type: "flight_control_autodeclared",
                          source: "agent_hook",
                          session_id: sid,
                          data: { tool: name, task: planned, from: "plan" },
                        })
                      }
                    } catch (err) {
                      // El intento falla abierto: queda el bloqueo accionable.
                      degrade(null, "auto-declare falló", err)
                    }
                  }
                  if (auto) {
                    // declarada: la escritura sigue
                  } else {
                  const path = relPath(input?.path || input?.filePath || "", rt)
                  const slug = String(rt.sessionGoal || path || "tarea")
                    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "tarea"
                  tracePush({
                    event_type: "flight_control_denied",
                    source: "agent_hook",
                    session_id: sid,
                    data: { tool: name, gate: "intake", file: path },
                  })
                  throw new Error(
                    `[WALKER] Escritura bloqueada: la sesión no declaró su tarea.
` +
                      `No es un bug: es el protocolo del proyecto y se abre con UNA llamada.
` +
                      `  ${CLI} ${rt.projectId}/implement_task '${JSON.stringify({
                        task_id: slug,
                        session_id: sid,
                        objective:
                          "<síntoma y reproducción>. Asumiendo <hipótesis>. Debe <criterio observable>. Sin romper los tests de <área>",
                        files: path ? [path] : [],
                      })}'
` +
                      `El objective NO es un título: walker deriva de ahí los criterios de aceptación ("debe…", "sin romper…") y las precondiciones ("asumiendo…"). Sin criterios no hay verificación posible, y el cierre no se puede validar.
` +
                      `Después repetí exactamente la misma herramienta: el bloqueo se libera solo.`
                  )
                  }
                }
              }
              // ── Gate de METACOGNICIÓN: sin plan declarado no se escribe ──
              // El brief ya se inyecta al intake; lo que falta es el plan de
              // una línea (tarea + alcance) que obliga a reconocer el problema
              // y elegir estrategia. El protocolo CLI pasa para poder declararlo.
              if (
                !isWalkerCli &&
                !isReadOrProtocol &&
                !forceOpen.has(sid || "global") &&
                rt.projectId &&
                !isDeclared(sid)
              ) {
                const planNow = planFor(sid) || {}
                const taskNow = String(planNow.task || "").trim()
                const scopeNow = (planNow.scope || []).filter(Boolean)
                if (!taskNow || !scopeNow.length) {
                  tracePush({
                    event_type: "flight_control_denied",
                    source: "agent_hook",
                    session_id: sid,
                    data: { tool: name, gate: "metacog", plan: Boolean(taskNow), scope: scopeNow.length },
                  })
                  throw new Error(
                    `[WALKER] Antes de escribir: declará el plan (tarea + alcance).\n` +
                      `Esto activa la metacognición: qué vas a cambiar y por qué.\n` +
                      `  ${CLI} ${rt.projectId}/session_plan '${JSON.stringify({
                        session_id: sid,
                        mode: "set",
                        task: "<task_id o descripción>",
                        scope: ["<archivo>"],
                        acceptance: ["<criterio verificable>"],
                      })}'\n` +
                      `Después repetí exactamente la misma herramienta.`
                  )
                }
              }

              // ── Gate de DILUVIO: budget del WorkContract (F2, ADR-086) ──
              // La unidad de revisión es la SPEC: si el contrato declara
              // `budget.max_files` y ya se tocaron esos archivos, agregar uno
              // nuevo exige ampliar el budget o dividir la spec. El deny trae
              // el comando exacto; sin contrato el gate no aplica.
              if (BUDGET_GATE && (name === "edit" || name === "write")) {
                const entryNow = sessionOf(sid)
                const target = relPath(input?.path || input?.filePath || "", rt)
                const denyBudget = budgetDeny(
                  entryNow.contract4d,
                  Array.from(entryNow.touched || []),
                  target,
                  { cli: CLI, project: rt.projectId }
                )
                if (denyBudget) {
                  tracePush({
                    event_type: "budget_exceeded",
                    source: "agent_hook",
                    session_id: sid,
                    data: {
                      tool: name,
                      file: target,
                      spec_id: entryNow.contract4d?.spec_id || "",
                      budget: budgetUsage(
                        entryNow.contract4d,
                        Array.from(entryNow.touched || [])
                      ),
                    },
                  })
                  throw new Error(denyBudget)
                }
              }

              // ── Gate de DESVÍO: escritura fuera del `scope` del plan ──
              if (SCOPE_GATE && (name === "edit" || name === "write")) {
                const target = relPath(input?.path || input?.filePath || "", rt)
                const scope = (planFor(sid).scope || []).map(String).filter(Boolean)
                if (scope.length && target && !inScope(target, scope)) {
                  const entryNow = sessionOf(sid)
                  const already = entryNow.scopeExpanded.has(target)
                  let expanded = already
                  if (!already) {
                    // P1.10: la negociación es UNA llamada que hace el plugin,
                    // una vez por archivo. Antes el agente recibía un bloqueo y
                    // tenía que reconstruir el `scope` completo (55 entradas) a
                    // mano; medido: 20 bloqueos de scope y 5 ampliaciones en la
                    // misma sesión. Queda trazado como desvío, no silencioso.
                    try {
                      const res = run(
                        "session_plan",
                        {
                          session_id: sid,
                          project_id: rt.projectId,
                          mode: "add",
                          scope: [target],
                        },
                        rt.projectId,
                        HOOK_TIMEOUT_MS
                      )
                      const data = res?.data || res
                      if (data && data.ok !== false) {
                        expanded = true
                        entryNow.scopeExpanded.add(target)
                        planCache.delete(sid) // el plan cambió: no leer cache
                        state.fetchedAt = 0
                        tracePush({
                          event_type: "flight_control_scope_expanded",
                          source: "agent_hook",
                          session_id: sid,
                          data: { tool: name, file: target, throughput: "auto" },
                        })
                      }
                    } catch (err) {
                      degrade(null, "scope auto-expand falló", err)
                    }
                  }
                  if (!expanded) {
                    entryNow.scopeDenied.add(target)
                    tracePush({
                      event_type: "flight_control_denied",
                      source: "agent_hook",
                      session_id: sid,
                      data: { tool: name, gate: "scope", file: target, scope },
                    })
                    const newScope = Array.from(new Set([...scope, target]))
                    throw new Error(
                      `[WALKER] No se pudo ampliar el alcance para ${target} (walker no respondió al plan).
` +
                        `Alcance declarado: ${scope.slice(0, 5).join(", ")}${scope.length > 5 ? " …" : ""}.
` +
                        `Ampliá el alcance y repetí:
` +
                        `  ${CLI} ${rt.projectId}/session_plan '${JSON.stringify({
                          session_id: sid,
                          mode: "set",
                          scope: newScope,
                        })}'`
                    )
                  }
                }
              }

              // `wrote` es "la sesión modificó archivos", independiente de qué
              // gate lo haya permitido. Incluye `shell` con redirección: es el
              // bypass real (medido: tras bloquear `write`, el agente usó
              // `printf > archivo` y el gate de cierre no se enteraba).
              if (name === "edit" || name === "write") {
                const entryNow = sessionOf(sid)
                // P1.14: escribir es seguir la sugerencia ("implementá").
                if (!entryNow.followed) {
                  entryNow.followed = true
                  entryNow.followedBy = "edit"
                }
                const wasVerifiedMut = entryNow.verified === true
                const wasWroteMut = entryNow.wrote === true
                entryNow.wrote = true
                entryNow.writes = (entryNow.writes || 0) + 1
                const touchedPath = relPath(input?.path || input?.filePath || "", rt)
                if (touchedPath) entryNow.touched.add(touchedPath)
                // Transición a sucio: también en la PRIMERA escritura de una
                // sesión no verificada (wrote=false), para que un reload no
                // pierda la deuda.
                if (wasVerifiedMut || !wasWroteMut) {
                  entryNow.verified = false
                  entryNow.verifyFailed = false
                  entryNow.dirtySince = new Date().toISOString()
                  tracePush({
                    event_type: "session_state",
                    source: "agent_hook",
                    session_id: sid,
                    data: {
                      verified: false,
                      dirty_since: entryNow.dirtySince,
                      wrote: true,
                      writes: entryNow.writes,
                      touched: Array.from(entryNow.touched || []).slice(0, 20),
                    },
                  })
                }
                if (touchedPath && !entryNow.firstMutation) {
                  const planNow = planFor(sid) || {}
                  entryNow.firstMutation = {
                    file: touchedPath,
                    after_baseline: Boolean(entryNow.baselineInjected),
                    in_baseline: (entryNow.baselineFiles || []).some((n) =>
                      touchedPath.endsWith("/" + n) || touchedPath === n
                    ),
                    in_scope: inScope(touchedPath, planNow.scope || []),
                  }
                }
              } else if (name === "shell") {
                const cmd = String(input?.command || "")
                const mut = shellMutation(cmd)
                if (mut.mutates) {
                  const entryNow = sessionOf(sid)
                  // P1.14: también una mutación por shell es "hacer algo".
                  if (!entryNow.followed) {
                    entryNow.followed = true
                    entryNow.followedBy = "shell"
                  }
                  const wasVerifiedShellMut = entryNow.verified === true
                  const wasWroteShellMut = entryNow.wrote === true
                  entryNow.wrote = true
                  entryNow.writes = (entryNow.writes || 0) + 1
                  if (wasVerifiedShellMut || !wasWroteShellMut) {
                    entryNow.verified = false
                    entryNow.verifyFailed = false
                    entryNow.dirtySince = new Date().toISOString()
                    tracePush({
                      event_type: "session_state",
                      source: "agent_hook",
                      session_id: sid,
                      data: {
                        verified: false,
                        dirty_since: entryNow.dirtySince,
                        wrote: true,
                        writes: entryNow.writes,
                        touched: Array.from(entryNow.touched || []).slice(0, 20),
                      },
                    })
                  }
                  if (!entryNow.firstMutation) {
                    // Path del redirect, sólo si el destino es del proyecto
                    // (un `> /tmp/log` no es una mutación del repo).
                    const target = mut.target || ""
                    if (target) {
                      const rel = relPath(target, rt)
                      if (rel) entryNow.touched.add(rel)
                    }
                    const planNow = planFor(sid) || {}
                    entryNow.firstMutation = {
                      file: target,
                      via: "shell",
                      after_baseline: Boolean(entryNow.baselineInjected),
                      in_baseline: target
                        ? (entryNow.baselineFiles || []).some(
                            (n) => target.endsWith("/" + n) || target === n
                          )
                        : null,
                      in_scope: target ? inScope(relPath(target, rt), planNow.scope || []) : null,
                    }
                  }
                }
              }
              // FC-INV-17: con sprint y tarea declarados, una DECISIÓN abierta
              // no se saltea escribiendo. El runtime no puede ordenar el loop
              // del modelo; sí puede negar la escritura en el único momento en
              // que el agente querría pasar por encima del gate. Los hubs
              // (`user_input` sin campos) no bloquean: ahí no hay nada abierto.
              // El REMEDIO nunca se bloquea (FC-INV-05): los verbos del
              // protocolo por CLI pasan aunque la decisión esté abierta. Se
              // reconoce por estructura, igual que `isCli`; un redirect con la
              // palabra "submit" adentro NO pasa.
              const protocolCmd =
                name === "shell" &&
                /^\s*(?:cd\s+[^\s;&|]+\s*&&\s*)?(?:\S*\/)?walker3\s+\S+\/(submit|advance|transition|run_tests|verify_predicates|capture|session_bind|session_plan|spec_status|implement_task)\b/.test(
                  String(input?.command || "")
                )
              if (
                DENY_STEP &&
                !protocolCmd &&
                cur &&
                rt.activeTask &&
                cur.execType === "user_input" &&
                (cur.required || []).length &&
                !cur.satisfied
              ) {
                const decisionFields = (cur.required || [])
                  .map((f) => `"${f}":"..."`)
                  .join(",")
                tracePush({
                  event_type: "flight_control_denied",
                  source: "agent_hook",
                  session_id: String(callCtx?.sessionID || ""),
                  data: {
                    tool: name,
                    gate: "decision",
                    position: cur?.position,
                    required: cur?.required,
                    remedy: `submit {${decisionFields}}`,
                  },
                })
                throw new Error(
                  `[WALKER] El paso ${cur.position} espera una DECISIÓN; no se ` +
                    `escribe por encima.\n` +
                    `Falta: ${(cur.required || []).join(", ")}.\n` +
                    `Mostrale las opciones al usuario y registrá su decisión:\n` +
                    `  ${CLI} ${rt.projectId}/submit '{${decisionFields}}'` +
                    ((cur.gates || []).length
                      ? `\n  ${CLI} ${rt.projectId}/transition '{"gate":"${cur.gates[0]}"}'`
                      : "")
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
              tracePush({
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
              // Con el gate ON, auto-declarar lo volvería decorativo: la tarea se
              // crearía con el primer mensaje del usuario como objective, sin
              // criterios ni hipótesis (medido: spec truncada a 160 chars). El
              // auto-declare queda solo para el modo escape (gate apagado).
              if (!DENY_INTAKE && !rt.activeTask && rt.projectId) {
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
                  tracePush({
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
                // Una sola llamada: `brief` con el archivo como ancla. Antes
                // eran agent_context + preflight (~1,2 s) y el bloque se
                // descartaba antes de inyectarse. Si no hay nada que aportar
                // (sin decisiones ni gates), no se inyecta ruido.
                const started = Date.now()
                const card = run(
                  "brief",
                  { session_id: sid, file },
                  workProjectFor(sid),
                  HOOK_TIMEOUT_MS
                )
                const text = briefBlock(card)
                if (text) {
                  rt.pendingBrief = text
                  tracePush({
                    event_type: "brief_injected",
                    source: "agent_hook",
                    session_id: sid,
                    files: file ? [file] : [],
                    data: {
                      trigger: "read",
                      file,
                      chars: text.length,
                      ms: Date.now() - started,
                      decisions: (card?.decisions || []).length,
                      cached: Boolean(card?.impact?.cached),
                      gaps: (card?.self?.gaps || []).map((g) =>
                        typeof g === "string" ? g : String(g?.code || "")
                      ).filter(Boolean),
                    },
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
        // El nombre efectivo de la tool MCP lleva namespace (`walker_…`): buscar
        // solo `implement_task` no enganchaba nunca y `rt.activeTask` quedaba
        // nulo aunque la tarea estuviera declarada.
        for (const name of ["implement_task", "walker_implement_task"]) {
          const taskTool = editor.get(name)
          if (!taskTool || typeof taskTool.execute !== "function") continue
          editor.update(name, (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const result = await original(input, callCtx)
              const followTask = sessionOf(String(callCtx?.sessionID || rt.sessionId || "global"))
              if (!followTask.followed) {
                followTask.followed = true
                followTask.followedBy = "task"
              }
              rt.activeTask = {
                taskId: String(input?.task_id || ""),
                files: Array.isArray(input?.files) ? input.files.map(String) : [],
              }
              const key = String(callCtx?.sessionID || "global")
              declared.set(key, { taskId: rt.activeTask.taskId, at: Date.now() })
              declCache.set(key, { at: Date.now(), value: true })
              state.fetchedAt = 0
              return result
            }
          })
        }
        // Evidencia de verificación por las tools (resultado ok).
        for (const name of ["run_tests", "verify_predicates", "walker_run_tests", "walker_verify_predicates"]) {
          const verifyTool = editor.get(name)
          if (!verifyTool || typeof verifyTool.execute !== "function") continue
          editor.update(name, (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const result = await original(input, callCtx)
              const entry = sessionOf(String(callCtx?.sessionID || rt.sessionId || "global"))
              // P1.14: llamar la verificación es seguir la sugerencia, verde o
              // rojo (el agente hizo lo que se le pidió).
              if (!entry.followed) {
                entry.followed = true
                entry.followedBy = "verify"
              }
              const payload = (result && result.data) || {}
              const isPredicates = String(name).includes("verify_predicates")
              // `run_tests` marca passed=false; `verify_predicates` devuelve
              // ok=true con `all_satisfied=false`. Antes el wrapper trataba
              // todo verify_predicates como verde y el gate de cierre cerraba
              // sin criterios cumplidos (medido 2026-11-20).
              const green =
                Boolean(result) &&
                result.ok !== false &&
                (isPredicates
                  ? payload.all_satisfied !== false &&
                    Number(payload.failed_count || 0) === 0
                  : payload.passed !== false)
              // SPEC-014: cuántos predicados se verificaron. El bloque lo
              // muestra; sin el conteo sólo puede decir "verificado".
              const total = Number(payload.total)
              if (Number.isFinite(total) && total > 0) {
                const failed = Number(payload.failed_count ?? payload.failures) || 0
                const errors = Number(payload.errors) || 0
                const passedCount = Number(payload.passed_count)
                entry.verifyCounts = {
                  passed: Number.isFinite(passedCount)
                    ? passedCount
                    : Math.max(0, total - failed - errors),
                  total,
                  by: isPredicates ? "predicates" : String(payload.test_framework || "tests"),
                }
              }
              if (green) {
                const wasVerifiedTools = entry.verified === true
                entry.verified = true
                entry.verifyFailed = false
                entry.verifyLevel = "tools"
                entry.verifiedBy = name
                entry.verifiedAt = new Date().toISOString()
                entry.dirtySince = ""
                // La verificación habilita la poda de ceremonia: el próximo
                // `refresh` puede avanzar el paso con evidencia.
                state.fetchedAt = 0
                if (!wasVerifiedTools) {
                  tracePush({
                    event_type: "session_state",
                    source: "agent_hook",
                    session_id: String(callCtx?.sessionID || rt.sessionId || "global"),
                    data: {
                      verified: true,
                      verified_at: entry.verifiedAt,
                      verify_level: entry.verifyLevel,
                      dirty_since: "",
                      wrote: Boolean(entry.wrote),
                      writes: entry.writes || 0,
                      touched: Array.from(entry.touched || []).slice(0, 20),
                    },
                  })
                }
                // Auto-capture proactivo: al verificar en verde, se guarda la
                // evidencia (archivos tocados + resultado) sin esperar a que el
                // agente lo pida. Una sola vez por sesión.
                if (!rt.captured && rt.activeTask && entry.wrote) {
                  try {
                    const sid = String(callCtx?.sessionID || rt.sessionId || "global")
                    const touched = Array.from(entry.touched || []).slice(0, 50)
                    run(
                      "capture",
                      {
                        session_id: sid,
                        task_id: String(rt.activeTask.taskId || ""),
                        evidence: "auto: verificacion en verde",
                        artifacts: touched,
                      },
                      rt.projectId,
                      HOOK_TIMEOUT_MS
                    )
                    rt.captured = true
                    tracePush({
                      event_type: "auto_capture",
                      source: "agent_hook",
                      session_id: sid,
                      data: { task_id: rt.activeTask.taskId, files: touched.length },
                    })
                  } catch (err) {
                    degrade(rt, "auto-capture falló", err)
                  }
                }
              } else {
                entry.verifyFailed = true
                entry.verified = false
                entry.verifiedBy = name
                entry.verifyCounts = null
                // SPEC-013: el rojo se guarda con su salida. El nudge de
                // cierre la muestra; sin ella el agente repite el comando.
                const tail = String(
                  payload.output_tail || payload.output || (result && result.error) || ""
                )
                const code = Number(payload.returncode)
                entry.lastFailure = {
                  command: String(name),
                  exitCode: Number.isFinite(code) ? code : 1,
                  text: tail.slice(-800),
                  signature: `${name}|rojo|${tail.slice(0, 120)}`,
                  injected: false,
                }
              }
              return result
            }
          })
        }

        // El binding hecho por la TOOL (no por el CLI) también tiene que abrir
        // el gate: se invalida la caché para que el próximo chequeo relea.
        for (const name of ["session_bind", "walker_session_bind"]) {
          const bindTool = editor.get(name)
          if (!bindTool || typeof bindTool.execute !== "function") continue
          editor.update(name, (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const result = await original(input, callCtx)
              const key = String(callCtx?.sessionID || rt.sessionId || "global")
              const bindEntry = sessionOf(key)
              if (!bindEntry.followed) {
                bindEntry.followed = true
                bindEntry.followedBy = "bind"
              }
              boundCache.delete(key)
              bindFailures.delete(key)
              return result
            }
          })
        }
        // P1.14: `session_plan` es la otra acción del protocolo. Además de
        // contar como seguimiento, invalida la caché del plan (antes sólo se
        // invalidaba si el agente lo llamaba por shell).
        for (const name of ["session_plan", "walker_session_plan"]) {
          const planTool = editor.get(name)
          if (!planTool || typeof planTool.execute !== "function") continue
          editor.update(name, (draft) => {
            const original = draft.execute
            draft.execute = async (input, callCtx) => {
              const result = await original(input, callCtx)
              const key = String(callCtx?.sessionID || rt.sessionId || "global")
              planCache.delete(key)
              state.fetchedAt = 0
              const planEntry = sessionOf(key)
              if (!planEntry.followed) {
                planEntry.followed = true
                planEntry.followedBy = "plan"
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
              const followEntry = sessionOf(String(callCtx?.sessionID || rt.sessionId || "global"))
              followEntry.followed = true
              followEntry.followedBy = "tool"
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
