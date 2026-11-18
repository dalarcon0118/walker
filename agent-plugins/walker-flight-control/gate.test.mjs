// Harness del plugin real, sin OpenCode.
//   node gate.test.mjs <directorio> [normal|missing|garbage|hang]
//
// normal  → gate cierra sin declaración y abre tras el CLI
// missing → WALKER_CLI inexistente: FAIL-OPEN (no se puede verificar)
// garbage → el CLI existe pero no devuelve JSON: FAIL-OPEN
// hang    → el CLI se cuelga: timeout corto y FAIL-OPEN
import { chmodSync, writeFileSync } from "node:fs"

const dir = process.argv[2] || process.cwd()
const mode = process.argv[3] || "normal"

if (mode === "missing") process.env.WALKER_CLI = "/nonexistent/walker3"
if (mode === "garbage") {
  writeFileSync("/tmp/fake-walker-garbage", "#!/bin/sh\necho esto-no-es-json\n")
  chmodSync("/tmp/fake-walker-garbage", 0o755)
  process.env.WALKER_CLI = "/tmp/fake-walker-garbage"
}
if (mode === "hang") {
  writeFileSync("/tmp/fake-walker-hang", "#!/bin/sh\nsleep 30\n")
  chmodSync("/tmp/fake-walker-hang", 0o755)
  process.env.WALKER_CLI = "/tmp/fake-walker-hang"
}
if (mode === "baddeclare") {
  // Responde "sin declarar" (auto-…) pero implement_task SIEMPRE falla: el
  // escenario que podría dejar la sesión bloqueada para siempre.
  writeFileSync(
    "/tmp/fake-walker-bad",
    [
      "#!/bin/sh",
      'case "$1" in',
      '  */workflow_state) echo \'{"ok":true,"task_id":"auto-x","current_position":"START.SELECT","step_satisfied":true}\'; exit 0;;',
      '  */project_for_path) echo \'{"ok":true,"project_id":"game_reset"}\'; exit 0;;',
      '  *) echo \'{"ok":false,"error":"boom"}\'; exit 1;;',
      "esac",
      "",
    ].join("\n")
  )
  chmodSync("/tmp/fake-walker-bad", 0o755)
  process.env.WALKER_CLI = "/tmp/fake-walker-bad"
}
if (mode !== "normal") {
  // Proyecto conocido sin depender de project_for_path (que también fallaría):
  // así el gate SÍ se evalúa y se prueba el fail-open, no el atajo de projectId.
  process.env.WALKER_PROJECT_ID = "game_reset"
}

const MOD = new URL("./index.js", import.meta.url).href

if (mode === "normal") {
  // El harness prepara el estado "sin declarar" él mismo: si lo hiciera el
  // shell de OpenCode, el gate bloquearía al test runner.
  const { execSync } = await import("node:child_process")
  const cli = process.env.WALKER_CLI || "/Users/davidmartinez/develop/Active-projects/walker/bin/walker3"
  execSync(
    `${cli} game_reset/implement_task '${JSON.stringify({
      task_id: "auto-harness",
      session_id: "harness-1",
      objective: "Condicion de prueba: task_id auto para forzar el gate.",
      files: [],
    })}'`,
    { cwd: "/Users/davidmartinez/develop/Active-projects/walker", stdio: ["ignore", "ignore", "ignore"] }
  )
}

const plugin = (await import(MOD)).default

const tools = new Map()
for (const n of ["edit", "write", "read", "implement_task", "walker_implement_task"]) {
  tools.set(n, { name: n, execute: async (input) => ({ ok: true, tool: n, input }) })
}
// `shell` ejecuta de verdad: en modo normal el gate se libera por el CLI real.
// Se corre en /tmp y con un comando sin redirección para no dejar basura.
tools.set("shell", {
  name: "shell",
  execute: async (input) => {
    const { execSync } = await import("node:child_process")
    const output = execSync(String(input.command), { encoding: "utf8", cwd: "/tmp", stdio: ["ignore", "pipe", "ignore"] })
    return { ok: true, output }
  },
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
const ctx = {
  location: { directory: dir },
  tool: { transform: async (cb) => { cb(makeEditor()); return { dispose: async () => {} } } },
  session: { hook: async (name, cb) => { captured[name] = cb; return { dispose: async () => {} } } },
  event: { subscribe: () => ({ [Symbol.asyncIterator]: () => ({ next: () => new Promise(() => {}) }) }) },
}

const t0 = Date.now()
const dispose = await plugin.setup(ctx)
const setupMs = Date.now() - t0

const call = async (name, input) => {
  try {
    await tools.get(name).execute(input, { sessionID: "harness-1" })
    return "PERMITE"
  } catch (e) {
    return "BLOQUEA"
  }
}

if (mode === "inject") {
  // Con tarea declarada, el hook de contexto debe inyectar el prompt REAL del
  // nodo (sprint/active), no la línea inventada "Complete el paso".
  await call("edit", { path: "x.txt" }) // fuerza isDeclared → rt.activeTask
  const event = {
    sessionID: "harness-1",
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "probar la inyeccion del nodo" }] }],
  }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okPos = /SPRINT\.ACTIVE/.test(text)
  const okTpl = /Sprint Monitoring|PHASE 12/.test(text)
  const noFake = !/Complete el paso:/.test(text)
  console.log(`posicion real en el bloque   -> ${okPos}`)
  console.log(`template del nodo inyectado  -> ${okTpl}`)
  console.log(`sin instruccion inventada    -> ${noFake}`)
  console.log(`chars inyectados             -> ${text.length}`)
  console.log("--- primeras lineas ---")
  console.log(text.split("\n").slice(0, 5).join("\n"))
  if (typeof dispose === "function") dispose()
  process.exit(okPos && okTpl && noFake ? 0 : 1)
}

const res = {}
res["edit sin declarar"] = await call("edit", { path: "x.txt" })
res["write sin declarar"] = await call("write", { path: "x.txt", content: "x" })
res["shell no-walker"] = await call("shell", { command: "echo hola" })
res["shell CLI walker3"] = await call("shell", {
  command:
    "cd /Users/davidmartinez/develop/Active-projects/walker && ./bin/walker3 game_reset/implement_task " +
    `'${JSON.stringify({ task_id: "harness-declared", session_id: "harness-1", objective: "Declaracion del harness. Debe liberar el gate. Sin romper los tests.", files: [] })}'`,
})
res["edit tras declarar"] = await call("edit", { path: "x.txt" })
if (mode === "baddeclare") {
  // Segundo intento fallido: acá se dispara el cortacircuito y el gate abre.
  await call("shell", {
    command:
      "cd /Users/davidmartinez/develop/Active-projects/walker && ./bin/walker3 game_reset/implement_task " +
      `'${JSON.stringify({ task_id: "harness-declared-2", session_id: "harness-1", objective: "Segundo intento. Debe abrir el gate.", files: [] })}'`,
  })
  res["edit tras 2do intento"] = await call("edit", { path: "x.txt" })
}

const expected =
  mode === "normal"
    ? { "edit sin declarar": "BLOQUEA", "write sin declarar": "BLOQUEA", "shell no-walker": "BLOQUEA", "shell CLI walker3": "PERMITE", "edit tras declarar": "PERMITE" }
    : mode === "baddeclare"
      ? { "edit sin declarar": "BLOQUEA", "write sin declarar": "BLOQUEA", "shell no-walker": "BLOQUEA", "shell CLI walker3": "PERMITE", "edit tras declarar": "BLOQUEA", "edit tras 2do intento": "PERMITE" }
      : { "edit sin declarar": "PERMITE", "write sin declarar": "PERMITE", "shell no-walker": "PERMITE", "shell CLI walker3": "PERMITE", "edit tras declarar": "PERMITE" }

let failed = 0
for (const [k, v] of Object.entries(res)) {
  const ok = v === expected[k]
  if (!ok) failed++
  console.log(`${ok ? "ok  " : "FAIL"}  ${k.padEnd(22)} -> ${v} (esperado ${expected[k]})`)
}
console.log(`mode=${mode} setup=${setupMs}ms fail=${failed}`)

if (typeof dispose === "function") dispose()
process.exit(failed ? 1 : 0)
