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
