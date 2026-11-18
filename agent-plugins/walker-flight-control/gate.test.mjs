// Harness del plugin real, sin OpenCode.
//   node gate.test.mjs <directorio> [normal|missing|garbage|hang|badbind|baddeclare|inject|bind]
//
// El gate tiene DOS fases: (1) sesión ligada a un sprint (session_bind), y
// (2) tarea declarada (implement_task). Cada modo falla una pieza distinta.
import { chmodSync, writeFileSync } from "node:fs"

const dir = process.argv[2] || process.cwd()
const mode = process.argv[3] || "normal"
const SID =
  process.env.HARNESS_SESSION || (mode === "normal" ? `harness-normal-${Date.now()}` : "harness-1")
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
if (mode !== "normal" && !mode.startsWith("inject") && mode !== "bind" && mode !== "noproject") {
  process.env.WALKER_PROJECT_ID = "game_reset"
}

if (mode === "gateoff") process.env.WALKER_FLIGHT_CONTROL_INTAKE = "0"

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
const closeMode = mode === "closegate" || mode === "closeok" || mode === "gateoff"
const ctx = {
  location: { directory: dir },
  tool: { transform: async (cb) => { cb(makeEditor()); return { dispose: async () => {} } } },
  session: {
    hook: async (name, cb) => { captured[name] = cb; return { dispose: async () => {} } },
    synthetic: async ({ sessionID, text }) => { nudges.push({ sessionID, text }); return {} },
  },
  event: {
    subscribe: () => ({
      [Symbol.asyncIterator]: () => {
        let served = false
        return {
          next: async () => {
            if (served || !closeMode) return new Promise(() => {})
            served = true
            // Se emite DESPUÉS de que el test prepare el estado de la sesión.
            await new Promise((r) => setTimeout(r, 2000))
            return { value: { type: "session.execution.succeeded", sessionID: SID }, done: false }
          },
        }
      },
    }),
  },
}

const t0 = Date.now()
const dispose = await plugin.setup(ctx)
const setupMs = Date.now() - t0

const call = async (name, input) => {
  try {
    await tools.get(name).execute(input, { sessionID: SID })
    return "PERMITE"
  } catch {
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
  const okPos = /SPRINT\.ACTIVE/.test(text)
  const okTpl = /Sprint Monitoring|PHASE 12/.test(text)
  const noFake = !/Complete el paso:/.test(text)
  console.log(`posicion real en el bloque   -> ${okPos}`)
  console.log(`template del nodo inyectado  -> ${okTpl}`)
  console.log(`sin instruccion inventada    -> ${noFake}`)
  console.log(`chars inyectados             -> ${text.length}`)
  if (typeof dispose === "function") dispose()
  process.exit(okPos && okTpl && noFake ? 0 : 1)
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
  if (mode === "closeok") {
    await call("shell", { command: "echo pytest" })
  }
  // Esperar el evento de fin de ejecución (2s) + margen.
  await new Promise((r) => setTimeout(r, 3200))
  const nudged = nudges.length
  const okText = nudged > 0 && /No cierres/.test(nudges[0].text)
  const expectedNudge = mode !== "closeok"
  const ok = expectedNudge ? okText : nudged === 0
  console.log(`${ok ? "ok  " : "FAIL"} nudges=${nudged} (esperado ${expectedNudge ? 1 : 0})`)
  if (nudged) console.log(nudges[0].text.split("\n")[0])
  if (typeof dispose === "function") dispose()
  process.exit(ok ? 0 : 1)
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
