// Harness del plugin real, sin OpenCode.
//   node gate.test.mjs <directorio> [normal|missing|garbage|hang|badbind|baddeclare|inject|bind]
//
// El gate tiene DOS fases: (1) sesión ligada a un sprint (session_bind), y
// (2) tarea declarada (implement_task). Cada modo falla una pieza distinta.
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"

const dir = process.argv[2] || process.cwd()
const mode = process.argv[3] || "normal"
const TAPPED = new Set(["persist", "shellmut", "idem", "evict"])
const SID =
  process.env.HARNESS_SESSION ||
  (mode === "normal"
    ? `harness-normal-${Date.now()}`
    : TAPPED.has(mode)
      ? `harness-${mode}-${Date.now()}`
      : "harness-1")
const WALKER = "/Users/davidmartinez/develop/Active-projects/walker"
const REAL_CLI = `${WALKER}/bin/walker3`

const fake = (path, lines) => {
  writeFileSync(path, lines.join("\n") + "\n")
  chmodSync(path, 0o755)
  process.env.WALKER_CLI = path
}

if (mode === "missing" || mode === "noproject") process.env.WALKER_CLI = "/nonexistent/walker3"
if (mode === "garbage") {
  fake("/tmp/fake-walker-garbage", ["#!/bin/sh", "echo esto-no-es-json"])
}
if (mode === "hang") {
  fake("/tmp/fake-walker-hang", ["#!/bin/sh", "sleep 30"])
}
if (mode === "badbind") {
  // session_bind siempre dice "no hay sprint": el gate nunca abre solo.
  fake("/tmp/fake-walker-badbind", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"no_work"}}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"task_id":"auto-x","current_position":"START.SELECT","step_satisfied":true}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"game_reset"}'; exit 0;;`,
    `  *) echo '{"ok":false}'; exit 1;;`,
    "esac",
  ])
}
if (mode === "baddeclare") {
  // El binding anda, pero implement_task falla siempre.
  fake("/tmp/fake-walker-baddeclare", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-FAKE","tasks":[]}}}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"task_id":"auto-x","current_position":"START.SELECT","step_satisfied":true}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"game_reset"}'; exit 0;;`,
    `  *) echo '{"ok":false,"error":"boom"}'; exit 1;;`,
    "esac",
  ])
}
if (mode === "evict") process.env.WALKER_FLIGHT_CONTROL_MAX_SESSIONS = "3"
const tapDir = `/tmp/wfc-tap-${process.pid}`
const tapFile = `${tapDir}/trace.jsonl`
if (TAPPED.has(mode)) {
  mkdirSync(tapDir, { recursive: true })
  const proxy = `${tapDir}/walker3-tap`
  writeFileSync(
    proxy,
    `#!/usr/bin/env node
const { execFileSync } = require("node:child_process")
const { appendFileSync } = require("node:fs")
const args = process.argv.slice(2)
let out = ""
try {
  out = execFileSync(${JSON.stringify(REAL_CLI)}, args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] })
} catch (e) {
  out = String(e.stdout || "")
  process.exitCode = e.status || 1
}
appendFileSync(${JSON.stringify(tapFile)}, JSON.stringify({ args, out: out.slice(0, 3000) }) + String.fromCharCode(10))
process.stdout.write(out)
`
  )
  chmodSync(proxy, 0o755)
  process.env.WALKER_CLI = proxy
}

// Todos los modos que usan el CLI real o el proxy verifican contra el mismo
// proyecto con el que invocan (`game_reset`). Sin esto, `normal`/`shellmut`/
// `persist` dejaban `rt.projectId` vacío y el plugin chequeaba el cwd (walker):
// `isBound` daba falso y bloqueaba escrituras legítimas.
if (mode !== "missing" && !mode.startsWith("inject") && mode !== "bind" && mode !== "noproject") {
  process.env.WALKER_PROJECT_ID = "game_reset"
}

if (mode === "gateoff") process.env.WALKER_FLIGHT_CONTROL_INTAKE = "0"

if (mode === "inject") {
  // Decisión ligada al archivo (convención "aplica a: <path>") para que L3 sea
  // determinista y no dependa del CKG vivo del proyecto.
  const { execSync } = await import("node:child_process")
  try {
    execSync(
      `${REAL_CLI} walker/documenting_knowledge '${JSON.stringify({
        nodes: [{
          id: "adr-999",
          type: "adr",
          subject: "seed de test",
          statement: "Seed del harness: aplica a: agent-plugins/walker-flight-control/index.js.",
        }],
      })}'`,
      { cwd: WALKER, stdio: ["ignore", "ignore", "ignore"] }
    )
  } catch {}
}

if (mode === "normal") {
  // Estado "sin declarar" para que el gate evalúe las dos fases. Se hace acá y
  // no por el shell de OpenCode, que estaría bloqueado por el propio gate.
  const { execSync } = await import("node:child_process")
  execSync(
    `${REAL_CLI} game_reset/implement_task '${JSON.stringify({
      task_id: "auto-harness",
      session_id: SID,
      objective: "Condicion de prueba: task_id auto para forzar el gate.",
      files: [],
    })}'`,
    { cwd: WALKER, stdio: ["ignore", "ignore", "ignore"] }
  )
}

const MOD = new URL("./index.js", import.meta.url).href
const plugin = (await import(MOD)).default

const tools = new Map()
for (const n of ["edit", "write", "read", "implement_task", "walker_implement_task"]) {
  tools.set(n, { name: n, execute: async (input) => ({ ok: true, tool: n, input }) })
}
// `shell` ejecuta de verdad: el desbloqueo pasa por el CLI. Se corre en /tmp.
tools.set("shell", {
  name: "shell",
  execute: async (input) => {
    const { execSync } = await import("node:child_process")
    const output = execSync(String(input.command), {
      encoding: "utf8",
      cwd: "/tmp",
      stdio: ["ignore", "pipe", "ignore"],
    })
    return { ok: true, output }
  },
})
// Verificación por TOOLS (no shell): simula verde/rojo para el gate de cierre
// sin correr pytest real.
tools.set("run_tests", {
  name: "run_tests",
  execute: async () => ({ ok: true, data: { passed: mode !== "closefail" } }),
})

const makeEditor = () => ({
  get: (n) => tools.get(n),
  update: (n, fn) => {
    const t = tools.get(n)
    if (!t) return
    const draft = { ...t }
    fn(draft)
    tools.set(n, draft)
  },
  list: () => [...tools.values()],
  add: () => {},
  remove: () => {},
  namespace: () => {},
})

const captured = {}
const nudges = []
const eventQueue = []
let eventWaiter = null
const pushEvent = (e) => {
  if (eventWaiter) {
    const w = eventWaiter
    eventWaiter = null
    w({ value: e, done: false })
  } else eventQueue.push(e)
}
const eventIterator = {
  [Symbol.asyncIterator]: () => ({
    next: () =>
      eventQueue.length
        ? Promise.resolve({ value: eventQueue.shift(), done: false })
        : new Promise((resolve) => {
            eventWaiter = resolve
          }),
  }),
}
const closeMode =
  mode === "closegate" ||
  mode === "closeok" ||
  mode === "closefail" ||
  mode === "closeshell" ||
  mode === "gateoff"
const ctx = {
  location: { directory: dir },
  tool: { transform: async (cb) => { cb(makeEditor()); return { dispose: async () => {} } } },
  session: {
    hook: async (name, cb) => { captured[name] = cb; return { dispose: async () => {} } },
    synthetic: async ({ sessionID, text }) => { nudges.push({ sessionID, text }); return {} },
  },
  event: { subscribe: () => eventIterator },
}

const t0 = Date.now()
const dispose = await plugin.setup(ctx)
const setupMs = Date.now() - t0

const call = async (name, input) => {
  try {
    await tools.get(name).execute(input, { sessionID: SID })
    return "PERMITE"
  } catch (e) {
    if (process.env.HARNESS_DEBUG) console.error("[DENY]", name, e.message)
    return "BLOQUEA"
  }
}

const cli = (task, payload) =>
  `cd ${WALKER} && ./bin/walker3 game_reset/${task} '${JSON.stringify({ session_id: SID, ...payload })}'`

if (mode === "inject") {
  await call("edit", { path: "x.txt" }) // fuerza el estado de sesión
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "probar la inyeccion del nodo" }] }] }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okPlan = /\[WALKER\] Plan:/.test(text)
  const okTask = /Tarea:/.test(text) && /Próximo:/.test(text)
  const noTemplate = !/Sprint Monitoring|PHASE 12/.test(text)
  const okBaseline = /Baseline:/.test(text) && /llamadores/.test(text)
  const okDecision = /·\s*adr-\d+/.test(text)
  const okCap = text.length <= 400
  const event2 = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "seguimiento" }] }],
  }
  await captured.context(event2)
  const stable = (event2.system || []).length === 0
  console.log(`bloque de plan               -> ${okPlan}`)
  console.log(`tarea y proximo paso         -> ${okTask}`)
  console.log(`sin template del nodo        -> ${noTemplate}`)
  console.log(`baseline desde walker        -> ${okBaseline}`)
  console.log(`decision del CKG (L3)        -> ${okDecision}`)
  console.log(`tope 400 chars               -> ${okCap} (${text.length})`)
  console.log(`estable sin repetir          -> ${stable}`)
  console.log(text.split("\n").slice(0, 4).join(" | "))
  if (typeof dispose === "function") dispose()
  process.exit(okPlan && okTask && noTemplate && okBaseline && okDecision && okCap && stable ? 0 : 1)
}

if (mode === "bind") {
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "quiero arreglar el login" }] }] }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n")
  const okDecision = /session_bind=(bound|resume_available|no_work)/.test(text)
  const okPrev = /Sprint: SPR-/.test(text) || /No hay trabajo previo/.test(text)
  const okCmd = /\/session_bind /.test(text)
  console.log(`decision en el bloque -> ${okDecision}`)
  console.log(`contexto previo       -> ${okPrev}`)
  console.log(`comando para decidir  -> ${okCmd}`)
  console.log(text.slice(0, 420))
  if (typeof dispose === "function") dispose()
  process.exit(okDecision && okPrev && okCmd ? 0 : 1)
}

if (mode === "noproject") {
  // Sin proyecto resoluble: el plugin no debe callarse; avisa una vez.
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "hola" }] }] }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n")
  const okNotice = /Sin proyecto/.test(text) && /_\/prepare/.test(text)
  console.log(`aviso de proyecto ausente -> ${okNotice}`)
  console.log(text.slice(0, 300))
  if (typeof dispose === "function") dispose()
  process.exit(okNotice ? 0 : 1)
}

if (closeMode) {
  // 1) abrir la sesión (hook), 2) vincular, 3) declarar, 4) escribir.
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "arreglar" }] }] }
  await captured.context(event)
  await call("shell", {
    command: cli("session_bind", { mode: "new", objective: "Gate de cierre: sesión de prueba." }),
  })
  await call("shell", {
    command: cli("implement_task", {
      task_id: "close-task",
      objective: "Tarea del gate de cierre. Debe exigir verificación. Sin romper los tests.",
      files: [],
    }),
  })
  await call("edit", { path: "x.txt" })
  if (mode === "closeok" || mode === "closefail") {
    await call("run_tests", {})
  }
  if (mode === "closeshell") {
    // Con acceptance declarada, el shell NO debe certificar: se espera nudge.
    await call("shell", { command: "echo pytest" })
  }
  // El evento de fin se empuja DESPUÉS de los pasos: si llega antes del edit,
  // el gate de cierre no ve `wrote` y no avisa.
  pushEvent({ type: "session.execution.succeeded", sessionID: SID })
  await new Promise((r) => setTimeout(r, 1500))
  const nudged = nudges.length
  const okText = nudged > 0 && /No cierres/.test(nudges[0].text)
  const expectedNudge = mode !== "closeok"
  const ok = expectedNudge ? okText : nudged === 0
  console.log(`${ok ? "ok  " : "FAIL"} nudges=${nudged} (esperado ${expectedNudge ? 1 : 0})`)
  if (nudged) console.log(nudges[0].text.split("\n")[0])
  if (typeof dispose === "function") dispose()
  process.exit(ok ? 0 : 1)
}

if (mode === "scope") {
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "arreglar" }] }] }
  await captured.context(event)
  await call("shell", {
    command: cli("session_bind", { mode: "new", objective: "Probar el gate de desvío. Debe acotar el alcance." }),
  })
  await call("shell", {
    command: cli("implement_task", {
      task_id: "scope-task",
      objective: "Tarea con alcance acotado. Debe permitir solo allowed.py. Sin romper los tests.",
      files: ["allowed.py"],
    }),
  })
  const out = {}
  out["edit en alcance"] = await call("edit", { path: "allowed.py" })
  out["edit fuera"] = await call("edit", { path: "otro/x.txt" })
  out["ampliar alcance"] = await call("shell", {
    command: cli("session_plan", { mode: "set", scope: ["allowed.py", "otro/x.txt"] }),
  })
  out["edit tras ampliar"] = await call("edit", { path: "otro/x.txt" })
  const expected = {
    "edit en alcance": "PERMITE",
    "edit fuera": "BLOQUEA",
    "ampliar alcance": "PERMITE",
    "edit tras ampliar": "PERMITE",
  }
  let failed = 0
  for (const [k, v] of Object.entries(out)) {
    const ok = v === expected[k]
    if (!ok) failed++
    console.log(`${ok ? "ok  " : "FAIL"}  ${k.padEnd(20)} -> ${v} (esperado ${expected[k]})`)
  }
  console.log(`mode=scope fail=${failed}`)
  if (typeof dispose === "function") dispose()
  process.exit(failed ? 1 : 0)
}

if (TAPPED.has(mode)) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms))
  const hookSid = async (sid) => {
    const ev = {
      sessionID: sid,
      system: [],
      messages: [{ role: "user", content: [{ type: "text", text: "tarea" }] }],
    }
    await captured.context(ev)
    return (ev.system || []).map((b) => b.text || "").join("\n")
  }
  const hook = () => hookSid(SID)

  if (mode === "evict") {
    for (let i = 1; i <= 3; i++) {
      await hookSid(`evict-${i}`)
      pushEvent({ type: "session.execution.succeeded", sessionID: `evict-${i}` })
      await wait(800)
    }
    await hookSid("evict-4")
    pushEvent({ type: "session.execution.succeeded", sessionID: "evict-4" })
    await wait(1500)
    const tap = readFileSync(tapFile, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l))
    const pushes = tap
      .filter((e) => /\/trace_push$/.test(String(e.args[0] || "")))
      .map((e) => JSON.parse(e.args[1]))
    const inj = pushes.filter((p) => p.event_type === "workflow_state_injected" && p.session_id === "evict-4")
    const ss = inj.length ? inj[inj.length - 1].data.sessionsState || [] : []
    const keys = ss.map((x) => x.k)
    // Contrato: nunca supera MAX_SESSIONS, conserva la actual y libera las
    // cerradas más viejas (con 3 cerradas + 1 nueva, caen las dos primeras).
    const ok =
      ss.length <= 3 &&
      keys.includes("evict-4") &&
      !keys.includes("evict-1") &&
      !keys.includes("evict-2")
    console.log(`${ok ? "ok  " : "FAIL"}  evicción libera cerradas: ${JSON.stringify(keys)} (size=${ss.length})`)
    if (typeof dispose === "function") dispose()
    process.exit(ok ? 0 : 1)
  }

  await hook() // abre la sesión
  await call("shell", { command: cli("session_bind", { mode: "new", objective: `${mode} test.` }) })

  let planInjections = []
  if (mode === "idem") {
    planInjections = [await hook(), await hook()]
  } else {
    await call("shell", {
      command: cli("implement_task", {
        task_id: `${mode}-task`,
        objective: "Tarea persistente. Sin romper los tests.",
        files: mode === "shellmut" ? ["out.txt"] : ["a.txt", "b.txt"],
      }),
    })
    await hook()
    if (mode === "shellmut") {
      await call("shell", { command: "printf x >> /tmp/wfc-h-out" })
      pushEvent({ type: "session.execution.succeeded", sessionID: SID })
      await wait(1500)
    } else {
      await call("edit", { path: "a.txt" })
      pushEvent({ type: "session.execution.succeeded", sessionID: SID })
      await wait(1500)
      await hook()
      await call("edit", { path: "b.txt" })
      pushEvent({ type: "session.execution.succeeded", sessionID: SID })
      await wait(1500)
    }
  }
  if (mode === "idem") {
    pushEvent({ type: "session.execution.succeeded", sessionID: SID })
    await wait(1500)
    planInjections.push(await hook()) // nueva ejecución: reabre sin reinyectar
    pushEvent({ type: "session.execution.succeeded", sessionID: SID })
    await wait(1500)
  }

  const tap = readFileSync(tapFile, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l))
  const pushes = tap
    .filter((e) => /\/trace_push$/.test(String(e.args[0] || "")))
    .map((e) => JSON.parse(e.args[1]))
  const metrics = pushes.filter((p) => p.event_type === "session_metrics")
  const opens = tap.filter((e) => /\/session_open$/.test(String(e.args[0] || ""))).length
  const closes = tap.filter((e) => /\/session_close$/.test(String(e.args[0] || ""))).length
  const last = metrics.length ? metrics[metrics.length - 1].data : null

  const cycles = mode === "idem" || mode === "persist" ? 2 : 1
  const checks = []
  checks.push([`session_open ${cycles} vez/veces`, opens === cycles])
  checks.push([`session_close ${cycles} vez/veces`, closes === cycles])
  if (mode === "persist") {
    checks.push(["metrics emitidas", metrics.length >= 2])
    checks.push(["writes acumuladas", last && last.writes === 2])
    checks.push(["primera mutación = a.txt", last && String(last.first_mutation?.file || "").endsWith("a.txt")])
    checks.push(["touched a.txt+b.txt", last && ["a.txt", "b.txt"].every((f) => (last.touched || []).some((t) => String(t).endsWith(f)))])
  } else if (mode === "shellmut") {
    checks.push(["writes=1 por shell", last && last.writes === 1])
    checks.push(["firstMutation via shell", last && last.first_mutation?.via === "shell"])
    checks.push(["file del redirect", last && String(last.first_mutation?.file || "").endsWith("wfc-h-out")])
  } else {
    checks.push(["plan inyectado 1 vez", planInjections.filter((t) => /\[WALKER\] Plan:/.test(t)).length === 1])
    checks.push(["sin mutaciones", last && last.writes === 0])
  }

  let failed = 0
  for (const [name, ok] of checks) {
    if (!ok) failed++
    console.log(`${ok ? "ok  " : "FAIL"}  ${name}`)
  }
  if (last) console.log("last metrics:", JSON.stringify({ writes: last.writes, firstMutation: last.first_mutation, touched: last.touched }).slice(0, 220))
  console.log(`mode=${mode} fail=${failed}`)
  try { if (mode === "shellmut") (await import("node:fs")).rmSync("/tmp/wfc-h-out", { force: true }) } catch {}
  if (typeof dispose === "function") dispose()
  process.exit(failed ? 1 : 0)
}

const res = {}
const bindCmd = cli("session_bind", { mode: "new", objective: "Probar el binding en el harness. Debe ligar la sesion al sprint." })
const taskCmd = cli("implement_task", {
  task_id: "harness-task",
  objective: "Tarea del harness. Debe liberar el gate. Sin romper los tests.",
  files: [],
})

if (mode === "normal" || mode === "badbind" || mode === "baddeclare") {
  res["edit sin nada"] = await call("edit", { path: "x.txt" })
  if (mode === "badbind") {
    res["bind intento 1"] = await call("shell", { command: bindCmd })
    res["edit tras bind 1"] = await call("edit", { path: "x.txt" })
    res["bind intento 2"] = await call("shell", { command: bindCmd })
    res["edit tras bind 2"] = await call("edit", { path: "x.txt" })
  } else if (mode === "baddeclare") {
    res["bind intento 1"] = await call("shell", { command: bindCmd })
    res["edit sin tarea"] = await call("edit", { path: "x.txt" })
    res["task intento 1"] = await call("shell", { command: taskCmd })
    res["edit tras task 1"] = await call("edit", { path: "x.txt" })
    res["task intento 2"] = await call("shell", { command: taskCmd })
    res["edit tras task 2"] = await call("edit", { path: "x.txt" })
  } else {
    res["bind"] = await call("shell", { command: bindCmd })
    res["edit sin tarea"] = await call("edit", { path: "x.txt" })
    res["implement_task"] = await call("shell", { command: taskCmd })
    res["edit final"] = await call("edit", { path: "x.txt" })
  }
} else {
  // walker caído / colgado / basura: fail-open en TODO.
  res["edit"] = await call("edit", { path: "x.txt" })
  res["write"] = await call("write", { path: "x.txt", content: "x" })
  res["shell no-walker"] = await call("shell", { command: "echo hola" })
  res["edit 2"] = await call("edit", { path: "x.txt" })
}

const expected = {
  normal: { "edit sin nada": "BLOQUEA", bind: "PERMITE", "edit sin tarea": "BLOQUEA", implement_task: "PERMITE", "edit final": "PERMITE" },
  badbind: { "edit sin nada": "BLOQUEA", "bind intento 1": "PERMITE", "edit tras bind 1": "BLOQUEA", "bind intento 2": "PERMITE", "edit tras bind 2": "PERMITE" },
  baddeclare: { "edit sin nada": "BLOQUEA", "bind intento 1": "PERMITE", "edit sin tarea": "BLOQUEA", "task intento 1": "PERMITE", "edit tras task 1": "BLOQUEA", "task intento 2": "PERMITE", "edit tras task 2": "PERMITE" },
}[mode] || { edit: "PERMITE", write: "PERMITE", "shell no-walker": "PERMITE", "edit 2": "PERMITE" }

let failed = 0
for (const [k, v] of Object.entries(res)) {
  const ok = v === expected[k]
  if (!ok) failed++
  console.log(`${ok ? "ok  " : "FAIL"}  ${k.padEnd(20)} -> ${v} (esperado ${expected[k]})`)
}
console.log(`mode=${mode} setup=${setupMs}ms fail=${failed}`)

if (typeof dispose === "function") dispose()
process.exit(failed ? 1 : 0)
