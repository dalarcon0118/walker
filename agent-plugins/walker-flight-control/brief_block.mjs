// Bloque compacto del flight card (`brief`) para inyectar en el contexto.
//
// Por qué existe: una tool disponible es una invitación; el modelo elige
// `read`/`bash` porque están a un token de distancia. La inyección es lo que
// hace que el contexto llegue sin que el modelo tenga que decidir pedirlo.
//
// Contrato: texto plano, acotado (`max` chars), sin JSON. Si el payload no
// sirve, devuelve "" — el plugin no inventa un bloque vacío con jerga.

function trim(value, max) {
  const text = String(value || "").replace(/\s+/g, " ").trim()
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function cap(lines, max) {
  const text = lines.join("\n")
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function nextAction(payload) {
  const gates = payload.gates || {}
  const step = payload.step || {}
  if (gates.next) return trim(gates.next, 140)
  const action = step.next_action || {}
  return trim(action.action || action.tool || action.name || "", 140)
}

export function briefBlock(payload, { max = 600 } = {}) {
  if (!payload || payload.error) return ""
  const step = payload.step || {}
  const gates = payload.gates || {}
  const plan = payload.plan || {}
  const consistency = payload.consistency || {}
  const impact = payload.impact || {}
  const lines = ["[WALKER] FLIGHT CARD — una llamada, sin adivinar"]

  if (gates.sprint || gates.task) {
    lines.push(
      `  sprint ${gates.sprint || "?"} · tarea ${gates.task || "(sin declarar)"}`
    )
  }
  if (step.position) {
    lines.push(`  paso ${step.position}${step.execution ? ` (${step.execution})` : ""}`)
  }
  if (plan.spec_id) {
    lines.push(`  spec ${plan.spec_id}: ${plan.spec_status || "?"}`)
  }
  const next = nextAction(payload)
  if (next) lines.push(`  próxima acción: ${next}`)
  const gaps = (payload.self && Array.isArray(payload.self.gaps)) ? payload.self.gaps : []
  if (gaps.length) {
    const g = gaps[0] || {}
    const detail = trim(g.detail || g.code || "gap", 90)
    const remedy = g.remedy ? ` → ${trim(g.remedy, 90)}` : ""
    lines.push(`  GAP: ${detail}${remedy}`)
  }
  const review = payload.review || {}
  if (review.summary) lines.push(`  review: ${trim(review.summary, 120)}`)
  const meta = payload.metacognition || {}
  if (typeof meta.score === "number") lines.push(`  METACOG SCORE: ${meta.score}/100`)
  const k = meta.knowledge || {}
  if (k.task) {
    lines.push(`  METACOG tarea: ${k.task.callers ?? "?"} callers · riesgo ${k.task.risk || "?"} · ${k.task.criteria ?? "?"} criterios`)
  }
  if (Array.isArray(k.strategies) && k.strategies.length) {
    lines.push(`  METACOG estrategia: ${trim(k.strategies.join(" · "), 140)}`)
  }
  const r = meta.regulation || {}
  if (r.plan && r.plan.strategy) lines.push(`  METACOG plan: ${trim(r.plan.strategy, 120)}`)
  if (r.monitor && r.monitor.next) lines.push(`  METACOG monitor: ${trim(r.monitor.next, 120)}`)
  if (r.evaluate && r.evaluate.adjust) lines.push(`  METACOG evaluar: ${trim(r.evaluate.adjust, 120)}`)
  lines.push("  METACOG: 1) ¿qué decisión/convención gobierna este archivo? 2) ¿qué callers/tests se ven afectados? 3) ¿qué gap queda sin remediar? 4) ¿qué evidencia demuestra verde?")
  const decision = (payload.decisions || [])[0]
  if (decision) {
    lines.push(`  decisión: ${trim(`${decision.id || ""} ${decision.title || ""}`, 130)}`)
  }
  if (impact.risk) {
    lines.push(
      `  impacto: riesgo ${impact.risk} · ${impact.callers ?? "?"} llamadores` +
        (impact.cached ? " (cache)" : "")
    )
  }
  if (consistency.ok === false) {
    lines.push(`  CONFLICTO: ${trim(consistency.reason, 170)}`)
    if (consistency.hint) lines.push(`  → ${trim(consistency.hint, 170)}`)
  }
  const missing = (payload.missing || []).slice(0, 2)
  if (missing.length) lines.push(`  falta: ${trim(missing.join("; "), 180)}`)

  // Un payload sin step/gates/plan/decisiones/impacto no aporta nada: no se
  // inyecta un encabezado vacío (medido en el harness: el fake devuelve
  // {ok:true} y el bloque igual se mandaba, rompiendo la estabilidad).
  if (lines.length <= 1) return ""
  return cap(lines, max)
}

export function failureBriefBlock(payload, failure, { max = 480 } = {}) {
  if (!payload || payload.error || !failure) return ""
  const lines = [
    `[WALKER] FALLÓ: ${trim(failure.command, 100)}${Number.isFinite(failure.exitCode) ? ` (exit ${failure.exitCode})` : ""}`,
  ]
  const step = payload.step || {}
  const gates = payload.gates || {}
  if (step.position) lines.push(`  paso ${step.position}`)
  const next = nextAction(payload)
  if (next) lines.push(`  próxima acción: ${next}`)
  const gaps = (payload.self && Array.isArray(payload.self.gaps)) ? payload.self.gaps : []
  if (gaps.length) {
    const g = gaps[0] || {}
    const detail = trim(g.detail || g.code || "gap", 90)
    const remedy = g.remedy ? ` → ${trim(g.remedy, 90)}` : ""
    lines.push(`  GAP: ${detail}${remedy}`)
  }
  const review = payload.review || {}
  if (review.summary) lines.push(`  review: ${trim(review.summary, 120)}`)
  const meta = payload.metacognition || {}
  if (typeof meta.score === "number") lines.push(`  METACOG SCORE: ${meta.score}/100`)
  const k = meta.knowledge || {}
  if (k.task) {
    lines.push(`  METACOG tarea: ${k.task.callers ?? "?"} callers · riesgo ${k.task.risk || "?"} · ${k.task.criteria ?? "?"} criterios`)
  }
  if (Array.isArray(k.strategies) && k.strategies.length) {
    lines.push(`  METACOG estrategia: ${trim(k.strategies.join(" · "), 140)}`)
  }
  const r = meta.regulation || {}
  if (r.plan && r.plan.strategy) lines.push(`  METACOG plan: ${trim(r.plan.strategy, 120)}`)
  if (r.monitor && r.monitor.next) lines.push(`  METACOG monitor: ${trim(r.monitor.next, 120)}`)
  if (r.evaluate && r.evaluate.adjust) lines.push(`  METACOG evaluar: ${trim(r.evaluate.adjust, 120)}`)
  lines.push("  METACOG: 1) ¿qué decisión/convención gobierna este archivo? 2) ¿qué callers/tests se ven afectados? 3) ¿qué gap queda sin remediar? 4) ¿qué evidencia demuestra verde?")
  const decision = (payload.decisions || [])[0]
  if (decision) {
    lines.push(`  decisión: ${trim(`${decision.id || ""} ${decision.title || ""}`, 120)}`)
  }
  const consistency = payload.consistency || {}
  if (consistency.ok === false) {
    lines.push(`  CONFLICTO: ${trim(consistency.reason, 150)}`)
  }
  lines.push("  usá el error de arriba; no repitas el mismo comando sin cambiarlo")
  return cap(lines, max)
}
