// Inyección de contexto del turno — módulo puro y testeable sin OpenCode.
//
// Problema que resuelve (AE-INV-10): el `system` es la CABEZA del prefijo de la
// conversación. Si el plugin le agrega bloques distintos en cada request, el
// proveedor no puede reutilizar nada de lo ya enviado: se pierde el cache de
// prompt y, en modelos que firman bloques de razonamiento, el bloque anterior se
// descarta y el modelo vuelve a pensar. Medido en el repo: el hook `context`
// corría en cada request y empujaba hasta 4 bloques que dependían del paso.
//
// Modos:
//   - `stable`   (default): los bloques son byte-idénticos mientras la firma del
//                nodo no cambie. Se repiten en cada request (cuestan input, pero
//                input cacheado) en vez de desaparecer después del primero.
//   - `once`     : comportamiento previo (un bloque por cambio de firma). Se
//                conserva para comparar A/B y para el harness.
//   - `messages` : los bloques van al último mensaje del turno, no al `system`.
//                Sólo aplica si la forma del mensaje es reconocible y el último
//                turno es del usuario; si no, cae a `system` y lo declara.

export const MODES = ["stable", "once", "messages"]

export function normalizeMode(value) {
  const mode = String(value ?? "")
    .trim()
    .toLowerCase()
  return MODES.includes(mode) ? mode : "stable"
}

function clean(blocks) {
  return (Array.isArray(blocks) ? blocks : []).filter(
    (b) => typeof b === "string" && b.trim().length > 0
  )
}

export function sameBlocks(a, b) {
  const left = clean(a)
  const right = clean(b)
  if (left.length !== right.length) return false
  for (let i = 0; i < left.length; i++) {
    if (left[i] !== right[i]) return false
  }
  return true
}

/**
 * Decide qué se manda en este request (modos `stable` y `messages`).
 *
 * En modo `once` el caller usa el camino previo: el plan se repite siempre y el
 * gate/brief/fallo se mandan una sola vez por firma. Ese modo no pasa por acá
 * a propósito, para que el A/B compare dos caminos reales y no una bandera.
 *
 * @param {object} input
 * @param {string[]} input.blocks       bloques contextuales ya calculados
 * @param {string}   input.sig          firma del nodo (posición|revisión|tarea|brief)
 * @param {object}   [input.memo]       {sig, blocks} del request anterior
 * @returns {{blocks: string[], changed: boolean, reused: boolean, reason: string}}
 */
export function planBlocks({ blocks, sig, memo }) {
  const list = clean(blocks)
  if (!list.length) return { blocks: [], changed: false, reused: false, reason: "sin-bloques" }
  if (memo && memo.sig === sig && sameBlocks(memo.blocks, list)) {
    return { blocks: memo.blocks, changed: false, reused: true, reason: "firma-igual" }
  }
  return { blocks: list, changed: true, reused: false, reason: "firma-nueva" }
}

function partsOf(message) {
  if (!message || typeof message !== "object") return null
  if (Array.isArray(message.parts)) return message.parts
  if (Array.isArray(message.content)) return message.content
  if (message.info && Array.isArray(message.info.parts)) return message.info.parts
  return null
}

function roleOf(message) {
  if (!message || typeof message !== "object") return ""
  return String(
    message?.info?.role || message?.role || message?.info?.type || message?.type || ""
  )
}

/**
 * Devuelve el array de partes donde se puede anexar sin romper el turno.
 *
 * Sólo se anexa al ÚLTIMO mensaje y sólo si es del usuario: mutar un turno del
 * asistente invalidaría los bloques de razonamiento que ese turno lleva.
 * Devolver `null` es la señal de "no hay destino seguro" (se cae a `system`).
 */
export function messageTarget(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return null
  const last = messages[messages.length - 1]
  const parts = partsOf(last)
  if (!parts) return null
  const role = roleOf(last)
  if (role && role !== "user") return null
  return parts
}

/**
 * Fotografía de la forma de `event.messages` para decidir si el canal
 * `messages` es viable. No toca nada: sólo describe. Se traza una vez por
 * sesión (`context_probe`) porque la forma no está tipada en el paquete de
 * plugins y adivinarla sería peor que medirla.
 */
export function describeMessages(messages) {
  const list = Array.isArray(messages) ? messages : []
  const last = list[list.length - 1]
  const safeLast = last && typeof last === "object" ? last : null
  return {
    count: list.length,
    last_keys: safeLast ? Object.keys(safeLast).slice(0, 8) : [],
    last_role: roleOf(safeLast) || "",
    last_type: String(safeLast?.type || ""),
    has_info: Boolean(safeLast && safeLast.info && typeof safeLast.info === "object"),
    has_parts: Array.isArray(safeLast?.parts),
    has_content: Array.isArray(safeLast?.content),
    target: messageTarget(list) ? "anexable" : "no-anexable",
  }
}

/**
 * Aplica los bloques al evento del hook.
 *
 * @returns {{channel: string, applied: number, fallback: string}}
 *   `fallback` no vacío significa que el modo pedido no se pudo usar y se
 *   inyectó por `system`. Nunca es silencioso: el caller lo traza.
 */
export function applyInjection(event, blocks, mode) {
  const list = clean(blocks)
  if (!list.length) return { channel: "none", applied: 0, fallback: "" }
  const resolved = normalizeMode(mode)
  if (resolved === "messages") {
    const parts = messageTarget(event && event.messages)
    if (parts) {
      for (const text of list) parts.push({ type: "text", text })
      return { channel: "messages", applied: list.length, fallback: "" }
    }
    // Sin destino seguro: system. El motivo viaja en `fallback`.
    const reason = Array.isArray(event && event.messages) && event.messages.length
      ? "ultimo-turno-no-es-usuario"
      : "sin-mensajes"
    if (!Array.isArray(event && event.system)) {
      return { channel: "none", applied: 0, fallback: reason }
    }
    for (const text of list) event.system.push({ type: "text", text })
    return { channel: "system", applied: list.length, fallback: reason }
  }
  if (!Array.isArray(event && event.system)) {
    return { channel: "none", applied: 0, fallback: "system-no-es-array" }
  }
  for (const text of list) event.system.push({ type: "text", text })
  return { channel: "system", applied: list.length, fallback: "" }
}
