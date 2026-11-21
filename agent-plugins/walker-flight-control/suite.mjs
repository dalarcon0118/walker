#!/usr/bin/env node
// Suite del harness del plugin walker-flight-control.
//
// Corre cada modo de `gate.test.mjs` en un proceso limpio y agrega el
// resultado. Es el único oráculo de punta a punta del plugin: los tests de
// pytest usan dobles y no ven lo que ve el agente (gates, plan, hidratación).
//
//   node suite.mjs [--project <id>] [--fast]
//
//   --project <id>  corre contra un proyecto YA registrado en walker.
//   (sin flag)      el harness crea su propio fixture en el temp dir y lo
//                   registra con `_/prepare`. Así no depende de ningún
//                   proyecto del usuario (antes estaba fijo a uno).
//   --fast          omite los modos que usan el CLI real (lentos, pero son la
//                   prueba de integración: gates de cierre/alcance reales).
//
// Exit code 1 si algún modo falla. Cada modo imprime su propia evidencia; en
// fallo se muestra la salida completa.
import { spawnSync } from "node:child_process"
import { mkdirSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { basename, dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const gate = join(here, "gate.test.mjs")
// El repo bajo prueba se deriva del layout del plugin, no de una ruta fija.
const walkerRoot = resolve(here, "..", "..")
const cliBin = join(walkerRoot, "bin", "walker3")
const argv = process.argv.slice(2)
const fast = argv.includes("--fast")

const flag = argv.indexOf("--project")
const explicit = flag >= 0 ? argv[flag + 1] : process.env.HARNESS_PROJECT || ""
const positional = argv.find((a, i) => !a.startsWith("--") && !(flag >= 0 && i === flag + 1))

const runCli = (args) => {
  const res = spawnSync(cliBin, args, { encoding: "utf8" })
  if (res.status !== 0) {
    throw new Error(`walker3 ${args[0]} falló: ${(res.stderr || res.stdout || "").trim()}`)
  }
  return JSON.parse(res.stdout || "{}")
}

const byId = (id) => {
  const res = runCli([`${id}/status`, "{}"])
  const dir = res?.project?.repo_path || res?.data?.project?.repo_path
  if (!dir) throw new Error(`proyecto ${id} sin repo_path: ¿está registrado?`)
  return { id, dir }
}

const fixture = (name = "walker-harness-fixture") => {
  // Path estable e idempotente: re-ejecutar no acumula entradas en el registro
  // global. Se BORRA antes de preparar para que el estado (sprints, tareas) no
  // se filtre entre corridas: el harness mide comportamiento, no historia.
  const dir = positional || join(tmpdir(), name)
  if (!positional) rmSync(dir, { recursive: true, force: true })
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, "pyproject.toml"), `[project]\nname='${name}'\n`)
  const res = runCli(["_/prepare", JSON.stringify({ path: dir })])
  const id = res?.project_id || res?.data?.project_id || basename(dir)
  return { id, dir }
}

const project = explicit ? byId(explicit) : fixture()

// Modos que necesitan un proyecto SIN trabajo previo: el plugin decide con el
// estado del proyecto (workflow_state/session_bind), no con la sesión. Si
// comparten fixture con los modos que declaran tareas, ven el plan de otro.
const ISOLATED = new Set(["bind", "autodeclare"])
const isolated = new Map()

process.env.HARNESS_PROJECT = project.id
process.env.HARNESS_PROJECT_DIR = project.dir
console.log(`proyecto del harness: ${project.id} (${project.dir})\n`)

// Modos con CLI real: gate.test.mjs los corre contra `bin/walker3` y un
// proyecto real. Son lentos (cada llamada es un proceso python) y son la
// prueba que atrapó la pérdida de escrituras del delta (2026-11-19).
const REAL_CLI = new Set([
  "closecli",
  "closeclipipe",
  "closeclimark",
  "closeclimarkpipe",
  "closeclipipefail",
  "closecliredirect",
  "extverify",
  "scope",
])

// `hang` queda afuera: duerme 30s a propósito (prueba de timeout) y su
// resultado no es pass/fail. Los demás modos salen 0/1.
const MODES = [
  "normal",
  "inject",
  "injectonce",
  "injectmessages",
  "injectsuggest",
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
  "autodeclare",
  "noproject",
  "scope",
  "closeok",
  "closefail",
  "closeshell",
  "closerounds",
  "closetai",
  "closetool",
  "verifycounts",
  "intakegoal",
  "handoff",
  "approval",
  "evidenceadvance",
  "stepgate",
  "closecli",
  "closeclipipe",
  "closeclimark",
  "closeclimarkpipe",
  "closeclipipefail",
  "extverify",
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
  let target = project
  if (ISOLATED.has(mode) && !explicit && !positional) {
    if (!isolated.has(mode)) isolated.set(mode, fixture(`${project.id}-${mode}`))
    target = isolated.get(mode)
  }
  const res = spawnSync(process.execPath, [gate, target.dir, mode], {
    encoding: "utf8",
    timeout: 300_000,
    env: {
      ...process.env,
      HARNESS_PROJECT: target.id,
      HARNESS_PROJECT_DIR: target.dir,
    },
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
