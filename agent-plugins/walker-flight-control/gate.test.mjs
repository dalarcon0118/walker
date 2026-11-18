// Harness del gate de intake: ejercita el plugin real sin OpenCode.
const MOD = "file:///Users/davidmartinez/develop/Active-projects/walker/agent-plugins/walker-flight-control/index.js"
const plugin = (await import(MOD)).default

const tools = new Map()
for (const n of ["edit", "write", "read", "implement_task", "walker_implement_task"]) {
  tools.set(n, { name: n, execute: async (input) => ({ ok: true, tool: n, input }) })
}
// `shell` ejecuta de verdad: el gate se libera por el CLI, así que un stub que
// no corre el comando no probaría nada.
tools.set("shell", {
  name: "shell",
  execute: async (input) => {
    const { execSync } = await import("node:child_process")
    const output = execSync(String(input.command), { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] })
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

const ctx = {
  location: { directory: process.argv[2] },
  tool: { transform: async (cb) => { cb(makeEditor()); return { dispose: async () => {} } } },
  session: { hook: async () => ({ dispose: async () => {} }) },
  event: { subscribe: () => ({ [Symbol.asyncIterator]: () => ({ next: () => new Promise(() => {}) }) }) },
}

const dispose = await plugin.setup(ctx)
const call = async (name, input) => {
  try {
    await tools.get(name).execute(input, { sessionID: "harness-1" })
    return "PERMITE"
  } catch (e) {
    return "BLOQUEA: " + String(e.message).split("\n")[0].replace("[WALKER] ", "").slice(0, 60)
  }
}

console.log("edit sin declarar      ->", await call("edit", { path: "x.txt" }))
console.log("write sin declarar     ->", await call("write", { path: "x.txt", content: "x" }))
console.log("shell no-walker        ->", await call("shell", { command: "echo hola > x.txt" }))
console.log("shell CLI walker3      ->", await call("shell", {
  command:
    "cd /Users/davidmartinez/develop/Active-projects/walker && ./bin/walker3 game_reset/implement_task " +
    `'${JSON.stringify({ task_id: "harness-declared", session_id: "harness-1", objective: "Declaracion del harness. Debe liberar el gate. Sin romper los tests.", files: [] })}'`,
}))
console.log("edit tras declarar     ->", await call("edit", { path: "x.txt" }))

if (typeof dispose === "function") dispose()
process.exit(0)
