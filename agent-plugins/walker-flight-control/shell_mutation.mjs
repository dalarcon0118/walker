// shell_mutation.mjs — ¿este comando de shell escribe EN EL PROYECTO?
//
// Vive fuera de index.js para poder testearlo sin OpenCode: el plugin lo usa
// como clasificador (flight control) y la evidencia de escritura sigue siendo
// el diff del watcher (FC-INV-11).
//
// Reglas:
//   - redirects (`> f`, `>> f`): muta sólo si el destino está dentro del
//     proyecto. `cmd > /tmp/log` y `2>&1` no cuentan.
//   - `cp`/`mv`/`install`: decide el DESTINO (último path). `cp x /tmp/y`
//     copia fuera: no es escritura del proyecto (medido 2026-11-20: copiar la
//     sqlite a /tmp disparaba el nudge de cierre).
//   - `rm`/`touch`/`mkdir`/`truncate`/`tee`/`sed -i`: opera en sitio; muta si
//     ALGÚN path cae dentro del proyecto.
//   - `dd`: conservador (escribe donde le digan).
//   - sin proyecto resoluble: conservador (muta), como antes.
//
// Lo que no se puede demostrar (`python -c`, `make`, binarios) sigue siendo
// "no muta" para el clasificador; el watcher lo ve igual.

import { isAbsolute, resolve } from "node:path"

const IN_PLACE = new Set(["rm", "touch", "mkdir", "truncate", "tee"])
const DESTINATION = new Set(["cp", "mv", "install"])

function stripQuotes(value) {
  return String(value || "").replace(/^['"]|['"]$/g, "")
}

function insideProject(rawPath, projectDir) {
  const raw = stripQuotes(rawPath)
  if (!raw) return false
  const project = String(projectDir || "").replace(/\/+$/, "")
  if (!project) return true // sin proyecto no se puede decidir: conservador
  let absolute = raw
  if (raw.startsWith("~")) {
    const home = process.env.HOME || ""
    absolute = home ? home + raw.slice(1) : raw
  }
  if (!isAbsolute(absolute)) absolute = resolve(project, absolute)
  const normalized = absolute.replace(/\/+$/, "")
  return normalized === project || normalized.startsWith(project + "/")
}

function cleanCommand(raw) {
  // 1) fuera lo que está entre comillas: mensajes/JSON no escriben nada.
  const unquoted = String(raw || "").replace(/'[^']*'|"[^"]*"/g, "''")
  // 2) fuera duplicaciones de fd y /dev/null.
  return unquoted
    .replace(/\d?>&\d/g, " ")
    .replace(/&?>>?\s*\/dev\/null/g, " ")
}

/**
 * @param {string} cmd
 * @param {string} projectDir
 * @returns {{mutates: boolean, target: string}}
 */
export function shellMutation(cmd, projectDir = "") {
  const cleaned = cleanCommand(cmd)

  // Redirects: el destino decide.
  for (const match of cleaned.matchAll(/>>?\s*([^\s;&|]+)/g)) {
    const target = stripQuotes(match[1])
    if (!target) continue
    if (!insideProject(target, projectDir)) continue
    return { mutates: true, target }
  }

  // Comandos por segmento (`a && b; c | d`).
  for (const segment of cleaned.split(/&&|\|\||[;|]/)) {
    const words = segment.trim().split(/\s+/).filter(Boolean)
    if (!words.length) continue
    const command = words[0].replace(/^.*\//, "")
    const args = words.slice(1)
    const isSedInPlace =
      command === "sed" && args.some((arg) => /^-[a-zA-Z]*i/.test(arg))

    if (command === "dd") return { mutates: true, target: "" }
    if (!IN_PLACE.has(command) && !DESTINATION.has(command) && !isSedInPlace) {
      continue
    }

    const paths = args.filter((arg) => !arg.startsWith("-") && !arg.includes("="))
    if (!paths.length) return { mutates: true, target: "" }

    if (DESTINATION.has(command)) {
      const destination = paths[paths.length - 1]
      if (insideProject(destination, projectDir)) {
        return { mutates: true, target: stripQuotes(destination) }
      }
      continue
    }

    if (paths.some((path) => insideProject(path, projectDir))) {
      return { mutates: true, target: stripQuotes(paths[paths.length - 1]) }
    }
  }

  return { mutates: false, target: "" }
}
