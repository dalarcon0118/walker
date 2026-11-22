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

// ── Clasificación por EFECTO (ADR-084 F3a / FC-INV-04) ─────────────────────
// El intake bloqueaba todo `shell` que no fuera exactamente `walker3`, así que
// `grep`, `pwd`, `git status` y `--help` pagaban el peaje de una escritura. La
// decisión es por efecto, no por nombre de tool:
//
//   read     → allow siempre (observar es la fase 1)
//   protocol → allow siempre (el remedio nunca puede quedar bloqueado)
//   verify   → allow (correr tests/predicados no muta el proyecto)
//   write    → deny (con remedio)
//   unknown  → deny/ask (nunca se degrada a read)
const READ_COMMANDS = new Set([
  "pwd", "ls", "cat", "head", "tail", "rg", "grep", "find", "wc", "which",
  "whoami", "stat", "file", "du", "df", "tree", "basename", "dirname",
  "realpath", "echo", "printf", "date", "env", "printenv", "sort", "uniq",
  "cut", "tr", "jq", "column", "diff", "comm", "test", "[", "readlink",
  "md5", "md5sum", "shasum", "sha256sum", "cmp", "less", "more", "cd",
])
const PIPE_READ = new Set([
  "head", "tail", "grep", "rg", "wc", "sort", "uniq", "cut", "tr", "jq",
  "column", "cat", "less", "more",
])
const GIT_READ = new Set([
  "status", "diff", "log", "show", "rev-parse", "describe", "blame",
  "ls-files", "ls-tree", "cat-file", "shortlog", "reflog",
])

function gitEffect(args) {
  const sub = args[0] || ""
  const flags = args.slice(1)
  if (sub === "--version" || sub === "version") return "read"
  if (GIT_READ.has(sub)) return "read"
  if (sub === "branch") {
    return flags.some((f) => /^-[dDmMcC]$/.test(f)) ? "write" : "read"
  }
  if (sub === "remote") return flags.length === 0 || flags.includes("-v") ? "read" : "write"
  if (sub === "config") {
    const reads = flags.length === 0 || flags.some((f) => ["-l", "--list", "--get"].includes(f))
    return reads ? "read" : "write"
  }
  if (sub === "stash") {
    return flags.length === 0 || ["list", "show"].includes(flags[0]) ? "read" : "write"
  }
  if (sub === "tag") return flags.some((f) => /^-[dD]$/.test(f)) ? "write" : "read"
  return "write" // checkout/reset/clean/add/commit/push/... tocan el árbol
}

function stripPrefixes(words) {
  let rest = words.slice()
  // `cd <dir> &&`, `env K=V`, `timeout 30`.
  for (;;) {
    const head = (rest[0] || "").replace(/^.*\//, "")
    if (head === "timeout" && rest.length > 1) {
      rest = rest.slice(2)
      continue
    }
    if (head === "env") {
      rest = rest.slice(1)
      while (rest.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(rest[0])) rest = rest.slice(1)
      continue
    }
    if (head === "cd" && rest.length > 1) {
      rest = rest.slice(2)
      continue
    }
    return rest
  }
}

function segmentEffect(segment) {
  const raw = segment.trim()
  if (!raw) return "read"
  const words = stripPrefixes(raw.split(/\s+/).filter(Boolean))
  if (!words.length) return "read"
  const base = (words[0] || "").replace(/^.*\//, "")
  const args = words.slice(1)

  // `--help` no ejecuta nada: es lectura aunque el comando sea desconocido.
  // (`-h` NO: en `rm -h` sería un flag real.)
  if (args.includes("--help")) return "read"
  if (base === "walker3") {
    const tool = (args[0] || "").split("/").pop() || ""
    return ["run_tests", "verify_predicates"].includes(tool) ? "verify" : "protocol"
  }
  if (base === "pytest") return "verify"
  if (["python", "python3"].includes(base) && args[0] === "-m" && args[1] === "pytest") {
    return "verify"
  }
  if (["npm", "pnpm", "yarn", "bun"].includes(base)) {
    return ["test", "run"].includes(args[0]) && (args[0] === "test" || args[1] === "test")
      ? "verify"
      : "unknown"
  }
  if (["go", "cargo"].includes(base) && args[0] === "test") return "verify"
  if (base === "git") return gitEffect(args)
  if (base === "sed" && !args.some((a) => /^-[a-zA-Z]*i/.test(a))) return "read"
  if (READ_COMMANDS.has(base)) return "read"
  return "unknown"
}

/**
 * @param {string} cmd
 * @param {string} projectDir
 * @returns {{effect: "read"|"protocol"|"verify"|"write"|"unknown", target: string}}
 */
export function shellEffect(cmd, projectDir = "") {
  const mutation = shellMutation(cmd, projectDir)
  if (mutation.mutates) return { effect: "write", target: mutation.target }

  const raw = String(cmd || "")
  // Un pipe sólo es read si el destino es un filtro read-only. `| sh`,
  // `| bash`, `| python`, `| xargs` ejecutan: unknown, nunca read.
  const pipeParts = raw.split("|")
  for (let i = 1; i < pipeParts.length; i++) {
    const words = stripPrefixes(pipeParts[i].trim().split(/\s+/).filter(Boolean))
    const base = (words[0] || "").replace(/^.*\//, "")
    if (!PIPE_READ.has(base)) return { effect: "unknown", target: "" }
  }

  const segments = raw
    .split(/&&|\|\||;|\|/)
    .map((s) => s.trim())
    .filter(Boolean)
  const effects = segments.map(segmentEffect)
  if (effects.includes("unknown")) return { effect: "unknown", target: "" }
  if (effects.includes("write")) return { effect: "write", target: "" }
  if (effects.includes("protocol")) return { effect: "protocol", target: "" }
  if (effects.includes("verify")) return { effect: "verify", target: "" }
  return { effect: "read", target: "" }
}

