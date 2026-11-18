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
// Gate de DESVÍO (ADR-WALKER-081): una escritura fuera del `scope` del plan se
// rechaza con el comando exacto para ampliarlo. Escape: ..._SCOPE=0.
const SCOPE_GATE = process.env.WALKER_FLIGHT_CONTROL_SCOPE !== "0"
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
  return (
    `[WALKER] No cierres todavía: esta sesión modificó archivos y no hay verificación VERDE.${crit}${failed}\n` +
    `Corré la suite: ${CLI} ${proj}/run_tests '{"task_id":"<id>"}' (ejecuta pytest y devuelve pass/fail) ` +
    `o verificá los predicados con verify_predicates.`
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
      projectDir: ctx?.location?.directory || ctx?.directory || "",
      sessionId: "",
      activeTask: null,
      sessionGoal: "",
      pendingBaseline: null,
      baselineDone: false,
      degraded: new Set(),
      pendingDegrades: [],
    }
    const run = (tool, payload = {}, project = rt.projectId, timeoutMs) =>
      runTool(tool, payload, project, timeoutMs)

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

    // Costo/tokens de la sesión (el proveedor los reporta por mensaje). Si no
    // se puede leer, `cost` queda null: nunca se reporta 0 como si fuera gratis.
    const sessionCost = async (sid) => {
      try {
        if (!ctx?.session?.context) return { cost: null, tokens: null }
        const msgs = await ctx.session.context({ sessionID: sid })
        let cost = 0
        let known = false
        const tokens = { input: 0, output: 0, cacheRead: 0 }
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
    const workProjectFor = (sid) => {
      const plan = planFor(sid) || {}
      if (plan.project) return String(plan.project)
      const groups = scopeProjects(plan.scope)
      const first = Object.keys(groups)[0]
      return first || rt.projectId
    }

    const decisionCache = new Map()
    // L3: UNA decisión que aplique al archivo. El CKG la devuelve si su
    // statement menciona el path (convención "aplica a: <archivo>"); se filtra
    // por id `adr-NNN` para no citar nodos de sesión ni probes.
    const decisionFor = (proj, files) => {
      if (!proj || !files.length) return ""
      const key = `${proj}|${files.join(",")}`
      const hit = decisionCache.get(key)
      if (hit && Date.now() - hit.at < 60_000) return hit.value
      let found = ""
      for (const f of files.slice(0, 2)) {
        const res = run("agent_context", { project_id: proj, file: f }, proj, BASELINE_TIMEOUT_MS)
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
      const line = `Baseline: ${top.join("; ")}${decision ? " · " + decision : ""}`
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
    const planBlock = (sid, entry) => {
      const plan = planFor(sid)
      if (!plan || !plan.goal) return ""
      const scope = Array.isArray(plan.scope) ? plan.scope : []
      // La firma NO incluye el baseline: si preflight falla o tarda, el bloque
      // no cambia entre llamadas (medido: fallaba y se reinyectaba).
      const sig = JSON.stringify([plan.goal, plan.task, plan.scope, plan.next, plan.acceptance])
      if (entry.planSig === sig) return "" // idéntico al anterior: no se inyecta
      const baseline = baselineLine(scope, entry)
      entry.planSig = sig
      entry.planTask = plan.task || ""
      const goal = String(plan.goal).replace(/\s+/g, " ").slice(0, 90)
      const criteria = Array.isArray(plan.acceptance) ? plan.acceptance.length : 0
      const touched = Array.from(entry.touched || [])
      const outside = scope.length
        ? touched.filter((f) => !inScope(f, scope))
        : []
      const lines = [
        `[WALKER] Plan: ${goal}`,
        `Tarea: ${plan.task || "(sin declarar)"} · Alcance: ${scope.length || "sin definir"} · Criterios: ${criteria}`,
      ]
      if (baseline) lines.push(baseline)
      lines.push(`Próximo: ${String(plan.next || "(definir)").slice(0, 70)}`)
      if (outside.length) lines.push(`⚠ Fuera de alcance: ${outside.slice(0, 3).join(", ")}`)
      // Contrato de atención: el bloque entero <= 400 caracteres.
      const block = lines.join("\n")
      return block.length <= 400 ? block : block.slice(0, 397) + "…"
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
      return {
        decision: data.decision,
        text:
          `${head}${meta}\n${data.suggestion || ""}\n` +
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
        if (sessions.size >= MAX_SESSIONS) {
          const closed = [...sessions.entries()].filter(([k, v]) => v.closed && k !== key)
          closed.sort((a, b) => (a[1].at || 0) - (b[1].at || 0))
          for (const [k] of closed) {
            if (sessions.size < Math.floor(MAX_SESSIONS * 0.75)) break
            sessions.delete(k)
          }
        }
        sessions.set(key, {
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
          verifyLevel: "",
          verifyFailed: false,
          writes: 0,
          scopeDenied: new Set(),
          metricsSent: false,
          closeNudged: false,
          touched: new Set(),
          planSig: "",
          planTask: "",
          baselineInjected: false,
          baselineFiles: [],
          firstMutation: null,
        })
      } else {
        sessions.get(key).at = Date.now() // actividad = recencia para la evicción
      }
      return sessions.get(key)
    }

    const readState = () => {
      const data = run("workflow_state", {}, rt.projectId, GATE_TIMEOUT_MS)
      if (!data || data.ok === false) return null
      return {
        position: data.current_position || "",
        execType: data.execution_type || "",
        required: Array.isArray(data.required_fields) ? data.required_fields : [],
        satisfied: data.step_satisfied !== false,
        gates: Array.isArray(data.available_gates) ? data.available_gates : [],
        taskId: String(data.task_id || ""),
        revision: data.revision ?? 0,
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
    const driveAuto = (current) => {
      let cur = current
      for (let i = 0; i < MAX_AUTO_STEPS; i++) {
        if (!cur || (cur.execType !== "auto" && cur.execType !== "bootstrap")) break
        const res = run("advance", {}, rt.projectId, GATE_TIMEOUT_MS)
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
      } else await ctx.session.hook("context", (event) => {
        // El motor se conduce igual (los `auto` corren sin tokens), pero el
        // texto solo se inyecta cuando hay una TAREA declarada. Medido: la
        // inyección en sesiones ad-hoc hacía que el agente caminara el
        // workflow por su cuenta (4 → 15 pasos sin que nada lo exigiera).
        refresh()
        const sid = event.sessionID || event.sessionId || ""
        rt.sessionId = rt.sessionId || sid
        const entry = sessionOf(sid)
        if (rt.projectId && sid && (!entry.opened || entry.closed)) {
          // `closed` significa "sin actividad", no terminal: si la sesión vuelve
          // a usarse en otra ejecución, se reabre (open_session es idempotente y
          // limpia ended_at). Antes se cerraba en la PRIMERA ejecución.
          entry.opened = true
          entry.closed = false
          // Registro de continuidad: ciclo de vida + jerarquía. Fail-open: si
          // walker no responde, la sesión sigue igual.
          run(
            "session_open",
            {
              session_id: sid,
              project_id: rt.projectId,
              agent: String(event.agent || ""),
            },
            rt.projectId,
            HOOK_TIMEOUT_MS
          )
        }
        if (!rt.sessionGoal) rt.sessionGoal = firstUserText(event)
        // Preflight en el PRIMER turno si ya hay tarea declarada: no depende de
        // que el agente use `read` (puede leer con `execute`, y entonces el
        // hook de lectura nunca dispara — medido).
        if (!rt.baselineDone && rt.projectId && state.data?.taskId) {
          rt.baselineDone = true
          const taskCtx = run("agent_context", { task_id: state.data.taskId }, rt.projectId, HOOK_TIMEOUT_MS)
          const files = (taskCtx?.anchor?.files || []).filter(Boolean)
          if (files.length) {
            const pf = run("preflight", { files }, rt.projectId, HOOK_TIMEOUT_MS)
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
        // El baseline viejo (multilínea) ya no se inyecta: el bloque del plan
        // trae la versión compacta (contrato de atención <= 400 chars). Se
        // consume para no acumularlo; la traza `intake_preflight` queda.
        rt.pendingBaseline = null
        let block = ""
        const st = state.data
        // Decisión POR SESIÓN: `state.data.taskId` es del proyecto y hacía que
        // una sesión nueva heredara "tarea declarada" de otra (medido en el
        // harness: el bloque de binding no aparecía nunca).
        if (!block) block = planBlock(sid, entry)
        if (block) entry.bootstrapped = true // ya se mostró el plan: no ofrecer binding
        if (!block && !entry.bootstrapped) {
          // Antes de escribir: ofrecer continuidad (sprint/goal previo) en vez
          // de solo pedir "declará la tarea".
          const bind = bindBlock(sid, rt.sessionGoal)
          block = bind.text
          if (bind.decision) entry.bind = bind.decision
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

          // ── Gate de CIERRE: hubo escritura y no hay verificación ──
          // No se puede impedir el fin de ejecución, así que se reinyecta un
          // mensaje sintético UNA vez (sin esto el ciclo sería infinito).
          if (
            event.type === "session.execution.succeeded" &&
            entry.opened &&
            entry.wrote &&
            (!entry.verified || entry.verifyFailed) &&
            !entry.closeNudged &&
            entry.sessionID
          ) {
            entry.closeNudged = true
            try {
              if (ctx?.session?.synthetic) {
                await ctx.session.synthetic({
                  sessionID: entry.sessionID,
                  text: closeBlock(rt, planFor(entry.sessionID), entry, workProjectFor(entry.sessionID)),
                })
              }
            } catch (err) {
              degrade(rt, "close nudge falló", err)
            }
            run("trace_push", {
              event_type: "close_blocked",
              source: "agent_hook",
              session_id: entry.sessionID,
              data: {
              reason: entry.verifyFailed ? "verificación falló" : "sin verificación",
              wrote: true,
              verify_failed: Boolean(entry.verifyFailed),
            },
            })
          }

          if (entry.opened && !entry.metricsSent && entry.sessionID) {
            entry.metricsSent = true
            const usage = await sessionCost(entry.sessionID)
            run("trace_push", {
              event_type: "session_metrics",
              source: "agent_hook",
              session_id: entry.sessionID,
              data: {
                writes: entry.writes || 0,
                touched_count: entry.touched.size,
                touched: Array.from(entry.touched).slice(0, 20),
                scope_denials: Array.from(entry.scopeDenied || []).slice(0, 10),
                verified: Boolean(entry.verified),
                verify_level: entry.verifyLevel || "",
                verify_failed: Boolean(entry.verifyFailed),
                cost_usd: usage.cost,
                tokens: usage.tokens,
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
            run(
              "session_close",
              {
                session_id: entry.sessionID,
                outcome: String(event.type || "").replace("session.execution.", ""),
              },
              rt.projectId,
              HOOK_TIMEOUT_MS
            )
          }
          if (entry.injections > 0) {
            run("trace_push", {
              event_type: "workflow_state_injected",
              source: "agent_hook",
              session_id: entry.sessionID,
              data: {
                injections: entry.injections,
                position: entry.position,
                followed: entry.followed,
                followedBy: entry.followedBy || "",
                bind: entry.bind || "",
                planTask: entry.planTask || "",
                wrote: entry.wrote,
                verified: entry.verified,
                verifiedBy: entry.verifiedBy || "",
                sessionsState: Array.from(sessions.entries()).map(([k, v]) => ({
                  k, w: v.wrote, i: v.injections, v: v.verified,
                })),
                outcome: event.type,
              },
            })
            if (!entry.followed) {
              run("trace_push", {
                event_type: "workflow_suggestion_ignored",
                source: "agent_hook",
                session_id: entry.sessionID,
                data: {
                  injections: entry.injections,
                  position: entry.position,
                  followedBy: entry.followedBy || "",
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

            if (isCli) {
              const step = String(cmd).match(/(?:^|\s)(?:\S+\/)?(submit|transition)(?=\s|$)/)
              if (step) {
                const e = sessionOf(key)
                const before = e.position
                state.fetchedAt = 0 // releer: el paso pudo cambiar el nodo
                const after = readState()
                // Solo cuenta si hubo progreso real; un typo en el JSON no marca.
                if (after && (after.satisfied || after.position !== before)) {
                  e.followed = true
                  e.followedBy = "cli"
                }
              }
            }

            // Segunda vía de `wrote`: el wrapper de shell corre siempre, así que
            // cubre el caso en que el agente escribe por shell en vez de `write`.
            if (/(>>?|\brm\b|\bmv\b|\bcp\b|\btee\b|\bsed\s+-i\b|\btouch\b|\bmkdir\b)/.test(cmd)) {
              sessionOf(key).wrote = true
            }

            // Evidencia de verificación (gate de cierre). Es un recordatorio, no
            // una prueba: si el comando lanzó, no se marca.
            if (/\b(pytest|run_tests|verify_predicates)\b/.test(cmd)) {
              const e = sessionOf(key)
              const plan = planFor(key) || {}
              const hasAcceptance =
                Array.isArray(plan.acceptance) && plan.acceptance.length > 0
              // Con acceptance declarada, el shell no certifica: solo una tool
              // de verificación (run_tests/verify_predicates) puede hacerlo.
              // Antes `echo pytest` cerraba una tarea con criterios sin probar.
              if (!hasAcceptance) {
                e.verified = true
                if (e.verifyLevel !== "tools") e.verifyLevel = "shell"
                e.verifiedBy = e.verifiedBy || "shell"
              }
            }

            // El plan cambió (o la tarea): la caché de 10s ya no vale.
            if (/\b(session_plan|implement_task|session_bind)\b/.test(cmd)) {
              planCache.delete(key)
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
              const tid = String(run("workflow_state", {})?.task_id || "")
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
              // En algunos caminos (p. ej. server standalone) el tool no trae
              // sessionID: sin fallback la marca de `wrote` caía en "".
              const sid = String(callCtx?.sessionID || rt.sessionId || "")
              if (
                DENY_INTAKE &&
                rt.projectId &&
                !isWalkerCli &&
                !forceOpen.has(sid || "global")
              ) {
                if (!isBound(sid)) {
                  run("trace_push", {
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
                  const path = relPath(input?.path || input?.filePath || "", rt)
                  const slug = String(rt.sessionGoal || path || "tarea")
                    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "tarea"
                  run("trace_push", {
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
              // ── Gate de DESVÍO: escritura fuera del `scope` del plan ──
              if (SCOPE_GATE && (name === "edit" || name === "write")) {
                const target = relPath(input?.path || input?.filePath || "", rt)
                const scope = (planFor(sid).scope || []).map(String).filter(Boolean)
                if (scope.length && target && !inScope(target, scope)) {
                  sessionOf(sid).scopeDenied.add(target)
                  run("trace_push", {
                    event_type: "flight_control_denied",
                    source: "agent_hook",
                    session_id: sid,
                    data: { tool: name, gate: "scope", file: target, scope },
                  })
                  const newScope = Array.from(new Set([...scope, target]))
                  throw new Error(
                    `[WALKER] Escritura fuera de alcance: ${target}
` +
                      `Alcance declarado: ${scope.slice(0, 5).join(", ")}${scope.length > 5 ? " …" : ""}.
` +
                      `Es una regla del plan de vuelo, no un bug. Si el archivo corresponde, ampliá el alcance y repetí:
` +
                      `  ${CLI} ${rt.projectId}/session_plan '${JSON.stringify({
                        session_id: sid,
                        mode: "set",
                        scope: newScope,
                      })}'`
                  )
                }
              }

              // `wrote` es "la sesión modificó archivos", independiente de qué
              // gate lo haya permitido. Incluye `shell` con redirección: es el
              // bypass real (medido: tras bloquear `write`, el agente usó
              // `printf > archivo` y el gate de cierre no se enteraba).
              if (name === "edit" || name === "write") {
                const entryNow = sessionOf(sid)
                entryNow.wrote = true
                entryNow.writes = (entryNow.writes || 0) + 1
                const touchedPath = relPath(input?.path || input?.filePath || "", rt)
                if (touchedPath) entryNow.touched.add(touchedPath)
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
                if (/(>>?|\brm\b|\bmv\b|\bcp\b|\btee\b|\bsed\s+-i\b|\btouch\b|\bmkdir\b)/.test(cmd)) {
                  const entryNow = sessionOf(sid)
                  entryNow.wrote = true
                  entryNow.writes = (entryNow.writes || 0) + 1
                  if (!entryNow.firstMutation) {
                    // Path del redirect (`> archivo`), si se puede; si no, null.
                    const m = cmd.match(/>>?\s*([^\s;&|]+)/)
                    const target = m ? m[1].replace(/^['"]|['"]$/g, "") : ""
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
                  const taskCtx = run("agent_context", { task_id: tid }, rt.projectId, HOOK_TIMEOUT_MS)
                  const files = (taskCtx?.anchor?.files || []).filter(Boolean)
                  if (files.length) {
                    const pf = run("preflight", { files }, rt.projectId, HOOK_TIMEOUT_MS)
                    block = preflightBlock(pf)
                    if (block) source = "task"
                  }
                }
                if (!block) {
                  const ctxData = run("agent_context", { file }, rt.projectId, HOOK_TIMEOUT_MS)
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
              const payload = (result && result.data) || {}
              // `run_tests` ahora ejecuta pytest: passed=false es rojo aunque ok
              // no venga explícito. verify_predicates ya devuelve ok=failure.
              const green =
                Boolean(result) &&
                result.ok !== false &&
                (String(name).includes("run_tests") ? payload.passed !== false : true)
              if (green) {
                entry.verified = true
                entry.verifyFailed = false
                entry.verifyLevel = "tools"
                entry.verifiedBy = name
              } else {
                entry.verifyFailed = true
                entry.verified = false
                entry.verifiedBy = name
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
              boundCache.delete(key)
              bindFailures.delete(key)
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
