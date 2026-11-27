// contract.mjs — proyección 4D del WorkContract en el harness (F2, ADR-WALKER-086).
//
// Puro y sin IO: el gate y las métricas comparten EXACTAMENTE la misma cuenta
// de budget. Antes el harness sólo contaba writes/archivos sin saber contra qué
// cota compararlos; el contrato vive en la spec y llega por `session_context`.
//
// Regla: si el contrato no está, no se inventa budget (`present:false`).

const intOrNull = (value) => (Number.isFinite(Number(value)) ? Number(value) : null)

/** Normaliza el bloque `contract_4d` que publica walker. */
export function contractBlock(raw) {
  const data = raw && typeof raw === "object" ? raw : null
  if (!data || data.present !== true) {
    return {
      present: false,
      spec_id: "",
      goal: "",
      budget: { max_files: null, max_loc: null },
      criteria: { mapped: 0, unmapped: 0 },
    }
  }
  const budget = data.budget && typeof data.budget === "object" ? data.budget : {}
  const criteria = data.criteria && typeof data.criteria === "object" ? data.criteria : {}
  return {
    present: true,
    spec_id: String(data.spec_id || ""),
    goal: String(data.goal || ""),
    budget: {
      max_files: intOrNull(budget.max_files),
      max_loc: intOrNull(budget.max_loc),
    },
    criteria: {
      mapped: Number(criteria.mapped || 0),
      unmapped: Number(criteria.unmapped || 0),
    },
  }
}

/** Archivos tocados y consumo del budget de archivos. */
export function budgetUsage(contract, touched) {
  const files = new Set(
    Array.isArray(touched) ? touched.filter(Boolean) : Array.from(touched || [])
  )
  const maxFiles = contract?.budget?.max_files ?? null
  const used = files.size
  return {
    used_files: used,
    max_files: maxFiles,
    remaining: maxFiles === null ? null : Math.max(0, maxFiles - used),
    exceeded: maxFiles !== null && used > maxFiles,
  }
}

/**
 * Deny del gate de diluvio. Sólo bloquea cuando la escritura AGREGARÍA un
 * archivo nuevo por encima del budget: reescribir uno ya declarado no es
 * diluvio, y sin contrato no hay cota que aplicar.
 *
 * @returns {string} mensaje con el comando exacto que desbloquea, o "".
 */
export function budgetDeny(contract, touched, target, { cli = "walker3", project = "" } = {}) {
  if (!contract?.present) return ""
  const maxFiles = contract?.budget?.max_files ?? null
  if (maxFiles === null) return ""
  const files = Array.isArray(touched) ? touched.filter(Boolean) : Array.from(touched || [])
  const known = new Set(files)
  if (!target || known.has(target)) return ""
  if (known.size < maxFiles) return ""
  const payload = {
    task_id: contract.spec_id || "<task_id>",
    work_contract: {
      budget: { max_files: maxFiles + 1, max_loc: contract?.budget?.max_loc ?? 200 },
    },
  }
  return (
    `[WALKER] Budget de la spec agotado: ${known.size}/${maxFiles} archivos ` +
    `(WorkContract ${contract.spec_id || "sin spec"}).\n` +
    `No se escribe ${target}: ampliá el budget o dividí la spec en otra tarea.\n` +
    `  ${cli} ${project}/capture '${JSON.stringify(payload)}'\n` +
    `Después repetí exactamente la misma herramienta.`
  )
}
