// Tests de la inyección por turno (AE-INV-10). Sin OpenCode: eventos falsos.
import test from "node:test"
import assert from "node:assert/strict"

import {
  MODES,
  applyInjection,
  describeMessages,
  messageTarget,
  normalizeMode,
  planBlocks,
  sameBlocks,
} from "./injection.mjs"

const PLAN = "[WALKER] Plan: X"
const GATE = "[WALKER] Gate: SELECT"

test("modo desconocido cae a stable, no a once", () => {
  assert.deepEqual(MODES, ["stable", "once", "messages"])
  assert.equal(normalizeMode(""), "stable")
  assert.equal(normalizeMode(undefined), "stable")
  assert.equal(normalizeMode("STABLE"), "stable")
  assert.equal(normalizeMode("raro"), "stable")
})

test("stable repite los mismos bloques cuando la firma no cambia", () => {
  const first = planBlocks({ blocks: [PLAN, GATE], sig: "A", memo: null })
  assert.equal(first.changed, true)
  assert.deepEqual(first.blocks, [PLAN, GATE])

  const second = planBlocks({ blocks: [PLAN, GATE], sig: "A", memo: { sig: "A", blocks: first.blocks } })
  // Lo importante: NO devuelve lista vacía. El system queda byte-idéntico.
  assert.equal(second.reused, true)
  assert.equal(second.changed, false)
  assert.deepEqual(second.blocks, [PLAN, GATE])
})

test("stable cambia cuando cambia la firma (nodo nuevo = un miss, después estable)", () => {
  const res = planBlocks({
    blocks: [PLAN, "[WALKER] Gate: ANALYZE"],
    sig: "B",
    memo: { sig: "A", blocks: [PLAN, GATE] },
  })
  assert.equal(res.changed, true)
  assert.equal(res.reason, "firma-nueva")
})

test("once sigue siendo un modo válido del env (camino previo, no planBlocks)", () => {
  assert.equal(normalizeMode("once"), "once")
})

test("sin bloques no se inyecta nada en ningún modo", () => {
  for (const mode of MODES) {
    const res = planBlocks({ blocks: [], sig: "A", memo: null })
    assert.equal(res.changed, false)
    assert.deepEqual(res.blocks, [])
  }
})

test("sameBlocks ignora vacíos y compara orden", () => {
  assert.equal(sameBlocks(["a", "b"], ["a", "b", "  "]), true)
  assert.equal(sameBlocks(["b", "a"], ["a", "b"]), false)
  assert.equal(sameBlocks(null, undefined), true)
})

test("messages anexa al último turno del usuario, sin tocar el system", () => {
  const event = {
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "hola" }] }],
  }
  const res = applyInjection(event, [PLAN], "messages")
  assert.equal(res.channel, "messages")
  assert.equal(res.fallback, "")
  assert.equal(event.system.length, 0)
  assert.equal(event.messages[0].content.length, 2)
  assert.equal(event.messages[0].content[1].text, PLAN)
})

test("messages soporta el shape {info, parts} (V2) y no muta el assistant", () => {
  const user = { info: { role: "user" }, parts: [{ type: "text", text: "hola" }] }
  assert.equal(messageTarget([user]), user.parts)
  assert.deepEqual(
    applyInjection({ system: [], messages: [user] }, [PLAN], "messages").channel,
    "messages"
  )
  const assistant = { info: { role: "assistant" }, parts: [{ type: "text", text: "ok" }] }
  const res = applyInjection({ system: [], messages: [user, assistant] }, [PLAN], "messages")
  assert.equal(res.channel, "system")
  assert.equal(res.fallback, "ultimo-turno-no-es-usuario")
})

test("messages sin mensajes cae a system y lo declara", () => {
  const event = { system: [] }
  const res = applyInjection(event, [PLAN], "messages")
  assert.equal(res.channel, "system")
  assert.equal(res.fallback, "sin-mensajes")
  assert.equal(event.system.length, 1)
})

test("system ausente no revienta: devuelve canal none y motivo", () => {
  const res = applyInjection({}, [PLAN], "stable")
  assert.equal(res.channel, "none")
  assert.equal(res.applied, 0)
  assert.equal(res.fallback, "system-no-es-array")
})

test("describeMessages no toca nada y reporta la forma real", () => {
  const messages = [
    { info: { role: "user" }, parts: [{ type: "text", text: "hola" }] },
    { role: "user", content: [{ type: "text", text: "otra" }] },
  ]
  const shape = describeMessages(messages)
  assert.equal(shape.count, 2)
  assert.deepEqual(shape.last_keys, ["role", "content"])
  assert.equal(shape.last_role, "user")
  assert.equal(shape.has_content, true)
  assert.equal(shape.has_parts, false)
  assert.equal(shape.target, "anexable")
  // No mutó el mensaje.
  assert.equal(messages[1].content.length, 1)
})

test("describeMessages declara no-anexable si el último turno no es del usuario", () => {
  const shape = describeMessages([
    { info: { role: "assistant" }, parts: [{ type: "text", text: "ok" }] },
  ])
  assert.equal(shape.last_role, "assistant")
  assert.equal(shape.target, "no-anexable")
  assert.equal(describeMessages(undefined).count, 0)
})

test("stable aplica al system y no toca messages", () => {
  const event = { system: [], messages: [{ role: "user", content: [] }] }
  const res = applyInjection(event, [PLAN, GATE], "stable")
  assert.equal(res.channel, "system")
  assert.equal(res.applied, 2)
  assert.deepEqual(event.system.map((b) => b.text), [PLAN, GATE])
  assert.equal(event.messages[0].content.length, 0)
})
