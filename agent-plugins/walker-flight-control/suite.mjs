#!/usr/bin/env node
// Suite del harness del plugin walker-flight-control.
//
// Corre cada modo de `gate.test.mjs` en un proceso limpio y agrega el
// resultado. Es el único oráculo de punta a punta del plugin: los tests de
// pytest usan dobles y no ven lo que ve el agente (gates, plan, hidratación).
//
//   node suite.mjs [directorio] [--fast]
//
//   --fast   omite los modos que usan el CLI real (lentos, pero son la prueba
//            de integración: gates de cierre/alcance contra walker de verdad).
//
// Exit code 1 si algún modo falla. Cada modo imprime su propia evidencia; en
// fallo se muestra la salida completa.
import { spawnSync } from "node:child_process"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const gate = join(here, "gate.test.mjs")
const argv = process.argv.slice(2)
const fast = argv.includes("--fast")
const dir = argv.find((a) => !a.startsWith("--")) || process.cwd()

// Modos con CLI real: gate.test.mjs los corre contra `bin/walker3` y un
// proyecto real. Son lentos (cada llamada es un proceso python) y son la
// prueba que atrapó la pérdida de escrituras del delta (2026-11-19).
const REAL_CLI = new Set([
  "closecli",
  "closeclipipe",
  "closeclimark",
  "closeclimarkpipe",
  "closeclipipefail",
  "scope",
])

// `hang` queda afuera: duerme 30s a propósito (prueba de timeout) y su
// resultado no es pass/fail. Los demás modos salen 0/1.
const MODES = [
  "normal",
  "inject",
  "degraded",
  "badbind",
  "baddeclare",
  "missing",
  "garbage",
  "gateprompt",
  "persist",
  "idem",
  "shellmut",
  "shelltmp",
  "evict",
  "bind",
  "noproject",
  "scope",
  "closeok",
  "closefail",
  "closeshell",
  "closecli",
  "closeclipipe",
  "closeclimark",
  "closeclimarkpipe",
  "closeclipipefail",
  "reloadverified",
  "reloadedit",
  "reloaddirty",
  "reloadclosedirty",
  "reloadcloseverified",
]

const selected = MODES.filter((m) => !(fast && REAL_CLI.has(m)))
const failed = []
const startedAll = Date.now()

for (const mode of selected) {
  const t0 = Date.now()
  const res = spawnSync(process.execPath, [gate, dir, mode], {
    encoding: "utf8",
    timeout: 300_000,
  })
  const secs = ((Date.now() - t0) / 1000).toFixed(1)
  const ok = res.status === 0
  const first = (res.stdout || res.stderr || "")
    .split("\n")
    .find((l) => /^(ok|FAIL)|^\[WALKER\]/.test(l))
  console.log(`${ok ? "PASS" : "FAIL"}  ${mode.padEnd(20)} ${secs}s  ${first || ""}`)
  if (!ok) {
    failed.push(mode)
    console.log(
      [res.stdout, res.stderr]
        .filter(Boolean)
        .join("\n")
        .split("\n")
        .map((l) => `      ${l}`)
        .join("\n")
    )
  }
}

const total = ((Date.now() - startedAll) / 1000).toFixed(1)
console.log(`\n${selected.length - failed.length}/${selected.length} modos OK en ${total}s`)
if (failed.length) {
  console.log(`fallaron: ${failed.join(", ")}`)
  process.exit(1)
}
