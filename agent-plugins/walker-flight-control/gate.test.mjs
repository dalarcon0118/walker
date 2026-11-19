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
if (mode === "inject") {
  // Fixture determinista: el modo NO depende del CKG/CPG real de game_reset
  // (que estaba vacío y hacía fallar baseline/ADR). El scope apunta a un
  // archivo real para que `existsSync` pase; el resto lo responde el fake.
  const scopeFile = `${WALKER}/agent-plugins/walker-flight-control/index.js`
  fake("/tmp/fake-walker-inject", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_plan) echo '{"ok":true,"data":{"plan":{"goal":"Probar la inyeccion del nodo","task":"harness-task","next":"implementar y verificar","scope":["${scopeFile}"],"acceptance":["Debe mostrar el plan","Sin romper los tests"]}}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"testproj"}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"current_position":"IMPLEMENT.WORK","execution_type":"llm_task","step_satisfied":true,"task_id":"harness-task","revision":1}'; exit 0;;`,
    `  */preflight) echo '{"ok":true,"files":[{"file":"index.js","callers":5,"heaviest":[{"cyclo":7}]}],"tests":["t1.py","t2.py"]}'; exit 0;;`,
    `  */agent_context) echo '{"ok":true,"ckg":{"results":[{"_id":"adr-055"}]}}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-TEST","tasks":[]}}}'; exit 0;;`,
    `  */implement_task) echo '{"ok":true,"data":{"task_id":"harness-task"}}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}
if (mode === "normal") {
  // Fixture PROPIO: `normal` no puede depender del `workflow_state` de
  // game_reset. Ese `task_id` es del PROYECTO y otra corrida lo dejaba en un
  // valor no-auto, con lo que el gate abría antes de declarar (medido: segunda
  // corrida consecutiva). El fake emula la transición real: arranca en `auto-x`
  // y solo `implement_task` lo cambia.
  const stateFile = `/tmp/fake-walker3-normal-${process.pid}.state`
  mkdirSync("/tmp/wfc-normal", { recursive: true })
  fake("/tmp/wfc-normal/walker3", [
    "#!/bin/sh",
    `STATE="${stateFile}"`,
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-NORMAL","status":"active","goal":"normal test","tasks":[]}}}'; exit 0;;`,
    `  */workflow_state) TID=$(cat "$STATE" 2>/dev/null || printf 'auto-x'); printf '{"ok":true,"task_id":"%s","current_position":"START.SELECT","execution_type":"","step_satisfied":true,"revision":1}\\n' "$TID"; exit 0;;`,
    `  */implement_task) TID=$(printf '%s' "$2" | sed -n 's/.*"task_id":"\\([^"]*\\)".*/\\1/p'); printf '%s' "\${TID:-harness-task}" > "$STATE"; printf '{"ok":true,"data":{"task_id":"%s"}}\\n' "\${TID:-harness-task}"; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"normal_probe"}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}
if (mode === "evict") process.env.WALKER_FLIGHT_CONTROL_MAX_SESSIONS = "3"
if (mode === "gateprompt") {
  // Un gate `user_input` con su prompt real: el plugin debe poner la PREGUNTA
  // en el contexto del modelo, no solo recordar "complete el paso".
  fake("/tmp/fake-walker-gateprompt", [
    "#!/usr/bin/env node",
    'const args = process.argv.slice(2); const tool = String(args[0] || "")',
    'if (tool.endsWith("/session_bind")) { console.log(JSON.stringify({ ok: true, data: { decision: "bound", bound: { spring_id: "SPR-FAKE", status: "active", goal: "gate test", tasks: [] }, suggestion: "continuá" } })); process.exit(0) }',
    'if (tool.endsWith("/workflow_state")) { console.log(JSON.stringify({ ok: true, task_id: "SPEC-002", current_position: "IMPLEMENT.REPORT_GATE", execution_type: "user_input", required_fields: [], step_satisfied: true, revision: 7, prompt: { template_id: "implement/report-gate", template: "IMPLEMENT.REPORT_GATE - Report\\n\\nPresentá el reporte.\\n\\nOptions:\\n    . proceed -> accepted, continue\\n    . refine  -> needs refinement\\n    . cancel  -> cancel task\\n\\n  walker <p>/submit \'{\\"decision\\":\\"proceed\\"}\'" } })); process.exit(0) }',
    'if (tool.endsWith("/project_for_path")) { console.log(JSON.stringify({ ok: true, project_id: "game_reset" })); process.exit(0) }',
    'console.log(JSON.stringify({ ok: true }))',
  ])
}
if (mode === "reloadverified" || mode === "reloadedit" || mode === "reloaddirty") {
  const stateFile = `/tmp/fake-walker3-reload-${process.pid}.state`
  mkdirSync("/tmp/wfc-reload", { recursive: true })
  // `reloaddirty` prueba el estado SUCIO persistido: un reload tras editar
  // debe conservar wrote=true/verified=false y volver a nudgar.
  const flightState =
    mode === "reloaddirty"
      ? '{"verified":false,"verify_level":"","verified_at":"","dirty_since":"2026-11-19T00:00:00Z","wrote":true,"writes":1,"touched":["x.txt"]}'
      : '{"verified":true,"verify_level":"cli-marker","verified_at":"2026-11-19T00:00:00Z","wrote":true,"writes":1,"touched":["x.txt"]}'
  fake("/tmp/wfc-reload/walker3", [
    "#!/bin/sh",
    `STATE="${stateFile}"`,
    'case "$1" in',
    `  */session_context) echo '{"ok":true,"data":{"flight_state":${flightState}}}'; exit 0;;`,
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-RELOAD","status":"active","goal":"reload test","tasks":[]}}}'; exit 0;;`,
    `  */workflow_state) TID=$(cat "$STATE" 2>/dev/null || printf 'auto-x'); printf '{"ok":true,"task_id":"%s","current_position":"START.SELECT","execution_type":"","step_satisfied":true,"revision":1}\\n' "$TID"; exit 0;;`,
    `  */implement_task) TID=$(printf '%s' "$2" | sed -n 's/.*"task_id":"\\([^"]*\\)".*/\\1/p'); printf '%s' "\${TID:-close-task}" > "$STATE"; printf '{"ok":true,"data":{"task_id":"%s"}}\\n' "\${TID:-close-task}"; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"game_reset"}'; exit 0;;`,
    `  */session_plan) echo '{"ok":true,"data":{"plan":{}}}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  */session_close) echo '{"ok":true}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}

if (mode === "reloadclosedirty" || mode === "reloadcloseverified") {
  // Recarga del plugin SIN hook de contexto: el estado persistido tiene que
  // llegar igual al gate de cierre (hidratación en el camino de cierre). Antes
  // el entry nacía vacío y una sesión sucia cerraba sin verificación.
  mkdirSync("/tmp/wfc-reloadclose", { recursive: true })
  const fs =
    mode === "reloadclosedirty"
      ? '{"verified":false,"verify_level":"","verified_at":"","dirty_since":"2026-11-19T00:00:00Z","wrote":true,"writes":3,"touched":["a.py"]}'
      : '{"verified":true,"verify_level":"cli-marker","verified_at":"2026-11-19T00:00:00Z","dirty_since":"","wrote":true,"writes":1,"touched":["a.py"]}'
  fake("/tmp/wfc-reloadclose/walker3", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_context) echo '{"ok":true,"data":{"flight_state":${fs}}}'; exit 0;;`,
    `  */session_plan) echo '{"ok":true,"data":{"plan":{"goal":"reload close","task":"t","scope":[],"acceptance":["Debe cerrar con verde"]}}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"game_reset"}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}

if (mode === "degraded") {
  // CKG vacío + un `adr-999` de seed: el bloque NO debe citarlo como autoridad
  // (P1.2). Sí debe decir que la memoria está degradada.
  const scopeFile = `${WALKER}/agent-plugins/walker-flight-control/index.js`
  fake("/tmp/fake-walker-degraded", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_plan) echo '{"ok":true,"data":{"plan":{"goal":"Probar CKG degradado","task":"harness-task","next":"implementar y verificar","scope":["${scopeFile}"],"acceptance":["Debe no citar adr-999","Sin romper los tests"]}}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"testproj"}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"current_position":"IMPLEMENT.WORK","execution_type":"llm_task","step_satisfied":true,"task_id":"harness-task","revision":1}'; exit 0;;`,
    `  */preflight) echo '{"ok":true,"files":[{"file":"index.js","callers":5,"heaviest":[{"cyclo":7}]}],"tests":["t1.py"],"memory_health":{"ckg":{"nodes":0,"status":"empty"},"cpg":{"nodes":0,"status":"empty"},"cyclo":{"known":0,"total":0,"status":"unavailable"},"trusted":false}}'; exit 0;;`,
    `  */agent_context) echo '{"ok":true,"memory_health":{"ckg":{"nodes":0,"status":"empty"},"cpg":{"nodes":0,"status":"empty"},"cyclo":{"known":0,"total":0,"status":"unavailable"},"trusted":false},"ckg":{"results":[{"_id":"adr-999","statement":"seed"}]}}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-TEST","tasks":[]}}}'; exit 0;;`,
    `  */implement_task) echo '{"ok":true,"data":{"task_id":"harness-task"}}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}

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
    const command = String(input.command)
    // `closecli` prueba la certificación por el RESULTADO del CLI. El comando
    // es el real; la salida se canjea por un verde determinista para no
    // depender de que el proyecto game_reset tenga predicates ejecutables.
    if (mode === "closecli" && /verify_predicates/.test(command)) {
      return {
        ok: true,
        output: JSON.stringify({
          ok: true,
          data: { all_satisfied: true, results: [], failed_count: 0, passed_count: 0, total: 0 },
        }),
        exitCode: 0,
      }
    }
    if (mode === "closeclipipe" && /verify_predicates/.test(command)) {
      // Salida recortada por un pipe: pierde `all_satisfied` y el exitCode.
      // El parser tiene que certificar por `failed_count: 0`.
      return {
        ok: true,
        output:
          '{"data":{"results":[{"passed":true}],"failed_count":0,"passed_count":2,"total":2}}',
      }
    }
    if (
      (mode === "closeclimark" || mode === "closeclimarkpipe") &&
      /verify_predicates/.test(command)
    ) {
      // El cuerpo viene cortado a propósito: el marcador final es la evidencia.
      return {
        ok: true,
        output:
          '{"data":{"results":[{"passed":true}],"failed_count":0,"passed_c\n' +
          "WALKER_RESULT tool=verify_predicates ok=1 passed=2 failed=0 errors=0 total=2 exit=0",
        exitCode: 0,
      }
    }
    if (mode === "closeclipipefail" && /verify_predicates/.test(command)) {
      // Pipe con exit 0 (tail) y SIN marcador: no es certificable.
      return {
        ok: true,
        output: '{"data":{"results":[{"passed":true}],"failed_count":0',
        exitCode: 0,
      }
    }
    const { execSync } = await import("node:child_process")
    const output = execSync(command, {
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
  mode === "closecli" ||
  mode === "reloadverified" ||
  mode === "reloadedit" ||
  mode === "reloaddirty" ||
  mode === "closeclipipe" ||
  mode === "closeclimark" ||
  mode === "closeclimarkpipe" ||
  mode === "closeclipipefail" ||
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

// `normal` corre contra su propio fake para no leer el estado del proyecto;
// el resto usa el CLI real porque prueba la integración de punta a punta.
const CLI_BIN = (mode === "normal" || mode.startsWith("reload")) ? process.env.WALKER_CLI || REAL_CLI : REAL_CLI
const cli = (task, payload) =>
  `cd ${WALKER} && ${CLI_BIN} game_reset/${task} '${JSON.stringify({ session_id: SID, ...payload })}'`

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
  // El tope de 400 es del bloque de PLAN (contrato de atención). El gate de
  // decisión es otro bloque, con su propio tope (1200): medir el total
  // confundía los dos contratos.
  const blocks = (event.system || []).map((b) => b.text || "")
  const planBlocks = blocks.filter((t) => /\[WALKER\] Plan:/.test(t))
  const okCap = planBlocks.length > 0 && planBlocks.every((t) => t.length <= 400)
  const okTotal = text.length <= 1600
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
  console.log(`tope 400 del plan            -> ${okCap} (${planBlocks.map((t) => t.length).join(",")})`)
  console.log(`total plan+gate <= 1600      -> ${okTotal} (${text.length})`)
  console.log(`estable sin repetir          -> ${stable}`)
  console.log(text.split("\n").slice(0, 4).join(" | "))
  if (typeof dispose === "function") dispose()
  process.exit(okPlan && okTask && noTemplate && okBaseline && okDecision && okCap && okTotal && stable ? 0 : 1)
}

if (mode === "degraded") {
  await call("edit", { path: "x.txt" })
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "probar ckg degradado" }] }] }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okPlan = /\[WALKER\] Plan:/.test(text)
  const okBaseline = /Baseline:/.test(text)
  const noAdr = !/adr-\d+/.test(text)
  const okDegraded = /CKG degradado/.test(text)
  console.log(`bloque de plan               -> ${okPlan}`)
  console.log(`baseline presente            -> ${okBaseline}`)
  console.log(`no cita adr-999              -> ${noAdr}`)
  console.log(`dice CKG degradado           -> ${okDegraded}`)
  console.log(text.split("\n").slice(0, 3).join(" | "))
  if (typeof dispose === "function") dispose()
  process.exit(okPlan && okBaseline && noAdr && okDegraded ? 0 : 1)
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

if (mode === "gateprompt") {
  const event = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "mostrame el gate" }] }],
  }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okPos = /Gate de decisión: IMPLEMENT\.REPORT_GATE/.test(text)
  const okNoDecide = /No decidas por el usuario/.test(text)
  const okOptions = /proceed/.test(text) && /refine/.test(text) && /cancel/.test(text)
  const okSubmit = /\/submit/.test(text)
  const event2 = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "sigo esperando" }] }],
  }
  await captured.context(event2)
  const stable = (event2.system || []).length === 0
  console.log(`posición del gate            -> ${okPos}`)
  console.log(`no decidir por el usuario    -> ${okNoDecide}`)
  console.log(`opciones del template        -> ${okOptions}`)
  console.log(`cómo cerrar el gate          -> ${okSubmit}`)
  console.log(`estable sin repetir          -> ${stable}`)
  console.log(text.split("\n").slice(-6).join(" | "))
  if (typeof dispose === "function") dispose()
  process.exit(okPos && okNoDecide && okOptions && okSubmit && stable ? 0 : 1)
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
  if (mode !== "reloadverified" && mode !== "reloaddirty") {
    await call("edit", { path: "x.txt" })
  }
  if (mode === "closeok" || mode === "closefail") {
    await call("run_tests", {})
  }
  if (mode === "closeshell") {
    // Con acceptance declarada, el shell NO debe certificar: se espera nudge.
    await call("shell", { command: "echo pytest" })
  }
  if (
    mode === "closecli" ||
    mode === "closeclipipe" ||
    mode === "closeclimark" ||
    mode === "closeclimarkpipe" ||
    mode === "closeclipipefail"
  ) {
    // CLI real con `acceptance` declarada: el plugin debe certificar por el
    // RESULTADO (Code Mode invoca las tools por shell, no por el wrapper).
    // Los modos "pipe" agregan el pipe real: el exit code no es del CLI.
    const pipe =
      mode === "closeclipipefail" || mode === "closeclimarkpipe"
        ? " | tail -c 400"
        : ""
    await call("shell", {
      command:
        `cd ${WALKER} && ./bin/walker3 game_reset/verify_predicates '${JSON.stringify({
          session_id: SID,
          task_id: "close-task",
        })}'` + pipe,
    })
  }
  // El evento de fin se empuja DESPUÉS de los pasos: si llega antes del edit,
  // el gate de cierre no ve `wrote` y no avisa.
  pushEvent({ type: "session.execution.succeeded", sessionID: SID })
  await new Promise((r) => setTimeout(r, 1500))
  const nudged = nudges.length
  const okText = nudged > 0 && /No cierres/.test(nudges[0].text)
  const certified = new Set([
    "closeok",
    "closecli",
    "closeclipipe",
    "closeclimark",
    "closeclimarkpipe",
    "reloadverified",
  ])
  const expectedNudge = !certified.has(mode)
  // Un verde no certificado por pipe debe explicar el motivo, no sólo nudgar.
  const okHint =
    mode !== "closeclipipefail" ||
    (nudged > 0 && /pipe|WALKER_RESULT/i.test(nudges[0].text))
  const ok = (expectedNudge ? okText : nudged === 0) && okHint
  console.log(`${ok ? "ok  " : "FAIL"} nudges=${nudged} (esperado ${expectedNudge ? 1 : 0})`)
  if (mode === "closeclipipefail") {
    console.log(`${okHint ? "ok  " : "FAIL"}  hint de pipe en el nudge`)
  }
  if (nudged) console.log(nudges[0].text.split("\n")[0])
  if (typeof dispose === "function") dispose()
  process.exit(ok ? 0 : 1)
}

if (mode === "reloadclosedirty" || mode === "reloadcloseverified") {
  // NO se llama captured.context: el plugin arranca "en frío" (recarga) y el
  // agente cierra. El estado persistido debe decidir el gate.
  pushEvent({ type: "session.execution.succeeded", sessionID: SID })
  await new Promise((r) => setTimeout(r, 1500))
  const expected = mode === "reloadclosedirty" ? 1 : 0
  const ok = nudges.length === expected
  console.log(
    `${ok ? "ok  " : "FAIL"} nudges=${nudges.length} (esperado ${expected}) [sin hook de contexto]`
  )
  if (nudges.length) console.log(nudges[0].text.split("\n")[0])
  if (typeof dispose === "function") dispose()
  process.exit(ok ? 0 : 1)
}

if (mode === "shelltmp") {
  // Un redirect a /tmp (con 2>&1) NO es una mutación del proyecto: no puede
  // invalidar el verde recién obtenido. Medido en sesión real: el agente
  // captura la salida de la verificación a /tmp y el nudge volvía a aparecer.
  fake("/tmp/fake-walker-shelltmp", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-TMP","status":"active","goal":"tmp test","tasks":[]}}}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"task_id":"auto-x","current_position":"START.SELECT","execution_type":"","step_satisfied":true,"revision":1}'; exit 0;;`,
    `  */implement_task) echo '{"ok":true,"data":{"task_id":"tmp-task"}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"tmp_probe"}'; exit 0;;`,
    `  */session_plan) echo '{"ok":true,"data":{"plan":{"goal":"tmp","task":"t","scope":[],"acceptance":["Debe cerrar con verde"]}}}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
  const WCLI = process.env.WALKER_CLI
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "arreglar" }] }] }
  await captured.context(event)
  await call("shell", {
    command: `${WCLI} tmp_probe/session_bind '${JSON.stringify({ session_id: SID, mode: "new", objective: "tmp" })}'`,
  })
  await call("shell", {
    command: `${WCLI} tmp_probe/implement_task '${JSON.stringify({
      session_id: SID,
      task_id: "tmp-task",
      objective: "Debe cerrar con verde. Sin romper los tests.",
      files: [],
    })}'`,
  })
  await call("edit", { path: "x.txt" })
  await call("run_tests", {}) // verde por tools
  await call("shell", { command: "pytest -q 2>&1 | tail -n 1 > /tmp/wfc-shelltmp.log" })
  pushEvent({ type: "session.execution.succeeded", sessionID: SID })
  await new Promise((r) => setTimeout(r, 1500))
  const ok = nudges.length === 0
  console.log(
    `${ok ? "ok  " : "FAIL"} nudges=${nudges.length} (esperado 0; redirect a /tmp no es mutación)`
  )
  if (nudges.length) console.log(nudges[0].text.split("\n")[0])
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

    // Reapertura: la sesión liberada vuelve a usarse y debe recrearse. Este es
    // el caso que faltaba: la evicción no puede dejar la sesión "muerta".
    await hookSid("evict-1")
    pushEvent({ type: "session.execution.succeeded", sessionID: "evict-1" })
    await wait(1500)

    const tap = readFileSync(tapFile, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l))
    const pushes = tap
      .filter((e) => /\/trace_push$/.test(String(e.args[0] || "")))
      .map((e) => JSON.parse(e.args[1]))
    const lastInj = (sid) => {
      const hits = pushes.filter((p) => p.event_type === "workflow_state_injected" && p.session_id === sid)
      return hits.length ? hits[hits.length - 1].data.sessionsState || [] : []
    }
    const ss4 = lastInj("evict-4")
    const keys4 = ss4.map((x) => x.k)
    const evictEvents = pushes.filter((p) => p.event_type === "session_evicted" && p.session_id === "evict-4")
    const evictEvent = evictEvents[evictEvents.length - 1]
    const ss1 = lastInj("evict-1")
    const reopened = ss1.find((x) => x.k === "evict-1")
    const evictedKeys = (evictEvent?.data?.evicted_keys || [])

    // Contrato: nunca supera MAX_SESSIONS, conserva la actual y libera las
    // cerradas más viejas (con 3 cerradas + 1 nueva, caen las dos primeras).
    const evictionOk =
      ss4.length <= 3 &&
      keys4.includes("evict-4") &&
      !keys4.includes("evict-1") &&
      !keys4.includes("evict-2")
    // Traza: la evicción deja rastro y cuenta cuántas liberó.
    const traceOk =
      Boolean(evictEvent) &&
      evictEvent.data.evicted === 2 &&
      evictedKeys.includes("evict-1") &&
      evictedKeys.includes("evict-2") &&
      ss4.some((x) => x.k === "evict-4" && x.e === 2)
    // Reapertura: vuelve al Map como entrada nueva (evicted=0), con actividad.
    const reopenOk =
      Boolean(reopened) && reopened.i >= 1 && reopened.e === 0 && ss1.length <= 3
    const ok = evictionOk && traceOk && reopenOk
    console.log(`${ok ? "ok  " : "FAIL"}  evicción libera cerradas: ${JSON.stringify(keys4)} (size=${ss4.length})`)
    console.log(`${traceOk ? "ok  " : "FAIL"}  traza de evicción: evicted=${evictEvent?.data?.evicted} keys=${JSON.stringify(evictedKeys)} e=${JSON.stringify(ss4.map((x) => x.e))}`)
    console.log(`${reopenOk ? "ok  " : "FAIL"}  reapertura: ${JSON.stringify(reopened)} (size=${ss1.length})`)
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
      await call("shell", { command: "printf x >> out.txt" })
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
    checks.push(["file del redirect", last && String(last.first_mutation?.file || "").endsWith("out.txt")])
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
