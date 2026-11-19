// Smoke test de carga del plugin walker-flight-control.
// Verifica que el módulo se importa y que setup() no lanza (TDZ, etc.).
//   node load.test.mjs
import assert from "node:assert/strict"

const MOD = new URL("./index.js", import.meta.url).href
const m = await import(MOD)
assert.equal(typeof m.default, "object", "default export debe ser objeto")
assert.equal(m.default.id, "walker-flight-control", "id del plugin")
assert.equal(typeof m.default.setup, "function", "setup debe ser función")

const ctx = {
  location: { directory: process.cwd() },
  tool: { transform: async () => ({ dispose: async () => {} }) },
  session: { hook: async () => ({ dispose: async () => {} }), synthetic: async () => ({}) },
  event: { subscribe: () => ({ [Symbol.asyncIterator]: () => ({ next: async () => ({ done: true }) }) }) },
}

let threw = null
try {
  await m.default.setup(ctx)
} catch (e) {
  threw = e
}
assert.equal(threw, null, `setup() no debe lanzar: ${threw?.message}`)

console.log("OK load.test: plugin carga y setup() no lanza")
process.exit(0)
