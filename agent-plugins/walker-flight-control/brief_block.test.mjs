// node --test brief_block.test.mjs
import test from "node:test"
import assert from "node:assert/strict"

import { briefBlock, failureBriefBlock } from "./brief_block.mjs"

const payload = {
  step: { position: "IMPLEMENT.IMPLEMENT_WRITE", execution: "auto" },
  gates: { sprint: "SPR-1", task: "f1", next: "verificar con run_tests" },
  plan: { spec_id: "SPEC-001", spec_status: "in_progress" },
  decisions: [{ id: "ADR-083", title: "El destino decide si un comando muta" }],
  impact: { callers: 61, risk: "alto", cached: true },
  consistency: { ok: true },
  missing: [],
}

test("el bloque trae sprint, tarea, paso, spec y decisión", () => {
  const text = briefBlock(payload)
  assert.match(text, /SPR-1/)
  assert.match(text, /f1/)
  assert.match(text, /IMPLEMENT\.IMPLEMENT_WRITE/)
  assert.match(text, /SPEC-001/)
  assert.match(text, /ADR-083/)
  assert.match(text, /riesgo alto/)
  assert.match(text, /\(cache\)/)
})

test("el conflicto se marca con la fuente que gobierna", () => {
  const text = briefBlock({
    ...payload,
    consistency: {
      ok: false,
      governs: "plan",
      reason: "la spec SPEC-001 está completed y el workflow sigue en START.SELECT",
      hint: "no recorras START.SELECT para esta tarea",
    },
  })
  assert.match(text, /CONFLICTO/)
  assert.match(text, /gobierna|start\.select/i)
})

test("respeta el tope de caracteres", () => {
  const text = briefBlock(
    { ...payload, decisions: [{ id: "ADR-1", title: "x".repeat(2000) }] },
    { max: 200 }
  )
  assert.ok(text.length <= 200, `len=${text.length}`)
})

test("payload inválido no produce bloque", () => {
  assert.equal(briefBlock(null), "")
  assert.equal(briefBlock({ error: "boom" }), "")
})

test("el bloque de fallo nombra el comando y el exit", () => {
  const text = failureBriefBlock(payload, {
    command: "python3 -m pytest tests/ -q",
    exitCode: 2,
    text: "E   assert 1 == 2",
  })
  assert.match(text, /FALLÓ/)
  assert.match(text, /pytest/)
  assert.match(text, /exit 2/)
  assert.match(text, /próxima acción/)
  assert.ok(text.length <= 480)
})
