// Harness del plugin real, sin OpenCode.
//   node gate.test.mjs <directorio> [normal|missing|garbage|hang|badbind|baddeclare|inject|bind]
//
// El gate tiene DOS fases: (1) sesión ligada a un sprint (session_bind), y
// (2) tarea declarada (implement_task). Cada modo falla una pieza distinta.
import { execFileSync } from "node:child_process"
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"

// Proyecto del harness: NUNCA un proyecto del usuario hardcodeado. `suite.mjs`
// crea un fixture propio (o usa `--project <id>`) y lo pasa por entorno.
const dir = process.argv[2] || process.env.HARNESS_PROJECT_DIR || process.cwd()
const PROJECT = process.env.HARNESS_PROJECT || "fixture"
const mode = process.argv[3] || "normal"
const TAPPED = new Set(["persist", "shellmut", "idem", "evict"])
// Modos que comparten proyecto pero NO deben compartir sesión: el flight state
// persistido (wrote/verified) de un modo se filtraba al siguiente y `closeshell`
// pasaba sin nudgar por heredar el verde de `closeok`. Cada corrida usa su id.
const OWN_SESSION = new Set([
  ...TAPPED,
  "closegate",
  "closeok",
  "closefail",
  "closeshell",
  "closerounds",
  "gateoff",
  "extverify",
  "reloadverified",
  "reloadedit",
  "reloaddirty",
  "reloadclosedirty",
  "reloadcloseverified",
])
const SID =
  process.env.HARNESS_SESSION ||
  (mode === "normal"
    ? `harness-normal-${Date.now()}`
    : OWN_SESSION.has(mode)
      ? `harness-${mode}-${Date.now()}`
      : "harness-1")
// El repo bajo prueba se deriva de la ubicación de este archivo
// (agent-plugins/walker-flight-control/ → ../..), no de una ruta absoluta.
const WALKER = process.env.WALKER_ROOT || resolve(dirname(fileURLToPath(import.meta.url)), "..", "..")
const REAL_CLI = `${WALKER}/bin/walker3`

const fake = (path, lines) => {
  writeFileSync(path, lines.join("\n") + "\n")
  chmodSync(path, 0o755)
  process.env.WALKER_CLI = path
}

if (mode === "missing" || mode === "noproject") process.env.WALKER_CLI = "/nonexistent/walker3"
if (mode === "garbage") {
  fake("/tmp/fake-walker-garbage", ["#!/bin/sh", "echo esto-no-es-json"])
}
if (mode === "hang") {
  fake("/tmp/fake-walker-hang", ["#!/bin/sh", "sleep 30"])
}
if (mode === "badbind") {
  // session_bind siempre dice "no hay sprint": el gate nunca abre solo.
  fake("/tmp/fake-walker-badbind", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"no_work"}}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"task_id":"auto-x","current_position":"START.SELECT","step_satisfied":true}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"${PROJECT}"}'; exit 0;;`,
    `  *) echo '{"ok":false}'; exit 1;;`,
    "esac",
  ])
}
if (mode === "baddeclare") {
  // El binding anda, pero implement_task falla siempre.
  fake("/tmp/fake-walker-baddeclare", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-FAKE","tasks":[]}}}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"task_id":"auto-x","current_position":"START.SELECT","step_satisfied":true}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"${PROJECT}"}'; exit 0;;`,
    `  *) echo '{"ok":false,"error":"boom"}'; exit 1;;`,
    "esac",
  ])
}
// Modos del A/B de inyección (AE-INV-10). `injectsuggest` NO es uno de ellos:
// mide otra cosa (seguimiento del plan) y tiene su propio fixture.
const INJECT_MODES = new Set(["inject", "injectonce", "injectmessages"])

if (INJECT_MODES.has(mode) || mode === "verifycounts" || mode === "approval") {
  // Fixture determinista: el modo NO depende del CKG/CPG real del proyecto
  // (que estaba vacío y hacía fallar baseline/ADR). El scope apunta a un
  // archivo real para que `existsSync` pase; el resto lo responde el fake.
  const scopeFile = `${WALKER}/agent-plugins/walker-flight-control/index.js`
  const briefFile = `${WALKER}/agent-plugins/walker-flight-control/brief_block.mjs`
  // SPEC-011: en `approval` el plan es no trivial (2 archivos y 2 criterios) y
  // el usuario todavía no aprobó: el bloque tiene que decirlo.
  const plan = mode === "approval"
    ? {
        goal: "Probar la aprobacion del plan",
        task: "harness-task",
        next: "pedir aprobacion al usuario",
        scope: [scopeFile, briefFile],
        acceptance: ["Debe mostrar el plan", "Sin romper los tests"],
        approved_by_user: false,
      }
    : {
        goal: "Probar la inyeccion del nodo",
        task: "harness-task",
        next: "implementar y verificar",
        scope: [scopeFile],
        acceptance: ["Debe mostrar el plan", "Sin romper los tests"],
      }
  fake("/tmp/fake-walker-inject", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_plan) echo '{"ok":true,"data":{"plan":${JSON.stringify(plan)}}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"testproj"}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"current_position":"IMPLEMENT.WORK","execution_type":"llm_task","step_satisfied":true,"task_id":"harness-task","revision":1}'; exit 0;;`,
    `  */preflight) echo '{"ok":true,"files":[{"file":"index.js","callers":5,"heaviest":[{"cyclo":7}]}],"tests":["t1.py","t2.py"]}'; exit 0;;`,
    `  */agent_context) echo '{"ok":true,"ckg":{"results":[{"_id":"adr-055"}]}}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-TEST","tasks":[]}}}'; exit 0;;`,
    `  */implement_task) echo '{"ok":true,"data":{"task_id":"harness-task"}}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}
if (mode === "handoff") {
  // SPEC-006: reanudar muestra goal, tareas ABIERTAS y el `next` previo. El
  // plan viene vacío a propósito: sin plan no hay next que inventar.
  fake("/tmp/fake-walker-handoff", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-OLD","status":"active","goal":"login roto","tasks_count":3,"open_tasks_count":2,"open_tasks":[{"task_id":"T-1"},{"task_id":"T-2"}],"next":"correr la suite de login","sessions":["s0"]}}}'; exit 0;;`,
    `  */session_plan) echo '{"ok":true,"data":{"plan":{}}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"testproj"}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"current_position":"START.SELECT","execution_type":"llm_task","step_satisfied":true,"revision":1}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}
if (mode === "evidenceadvance") {
  // Poda de ceremonia: un paso `llm_task` del camino de implementación avanza
  // con TRABAJO + VERIFICACIÓN, sin que el agente llame `advance`/`submit`.
  // El gate `user_input` que sigue NO se cruza nunca solo.
  const stateFile = `/tmp/fake-walker-evid-${process.pid}.state`
  const logFile = `${stateFile}.log`
  writeFileSync(stateFile, "IMPLEMENT.WORK")
  writeFileSync(logFile, "")
  fake("/tmp/fake-walker-evid", [
    "#!/usr/bin/env node",
    'const fs = require("node:fs")',
    `const stateFile = ${JSON.stringify(stateFile)}`,
    `const logFile = ${JSON.stringify(logFile)}`,
    'const tool = String(process.argv[2] || "")',
    'fs.appendFileSync(logFile, process.argv.slice(2).join(" ") + "\\n")',
    'const pos = fs.readFileSync(stateFile, "utf8").trim()',
    'const out = (o) => { console.log(JSON.stringify(o)); process.exit(0) }',
    'if (tool.endsWith("/workflow_state")) {',
    '  if (pos === "IMPLEMENT.WORK") out({ ok: true, current_position: "IMPLEMENT.WORK", execution_type: "llm_task", step_satisfied: false, next_action: { tool: "advance" }, revision: 1 })',
    '  if (pos === "IMPLEMENT.GATE") out({ ok: true, current_position: "IMPLEMENT.GATE", execution_type: "user_input", step_satisfied: true, available_gates: ["approve"], next_action: { tool: "transition" }, revision: 2 })',
    '  out({ ok: true, current_position: "VERIFY.MONITOR", execution_type: "auto", step_satisfied: true, revision: 3 })',
    '}',
    'if (tool.endsWith("/advance")) {',
    '  if (pos === "IMPLEMENT.WORK") fs.writeFileSync(stateFile, "IMPLEMENT.GATE")',
    '  else if (pos === "IMPLEMENT.GATE") fs.writeFileSync(stateFile, "VERIFY.MONITOR")',
    '  out({ ok: true })',
    '}',
    'if (tool.endsWith("/session_bind")) out({ ok: true, data: { decision: "bound", bound: { spring_id: "SPR-EVID", status: "active", goal: "evidencia", tasks: [] } } })',
    'if (tool.endsWith("/implement_task")) out({ ok: true, data: { task_id: "evid-task" } })',
    'if (tool.endsWith("/project_for_path")) out({ ok: true, project_id: "evid_probe" })',
    'if (tool.endsWith("/session_plan")) out({ ok: true, data: { plan: { goal: "probar avance por evidencia", task: "evid-task", next: "verificar", scope: [], acceptance: ["no cruza un gate"] } } })',
    'if (tool.endsWith("/agent_context")) out({ ok: true })',
    'if (tool.endsWith("/preflight")) out({ ok: true, files: [], tests: [] })',
    'out({ ok: true })',
  ])
}
if (mode === "stepgate") {
  // FC-INV-17: el paso del motor se ve en el bloque y una decisión abierta no
  // se saltea escribiendo. `submit` la registra y la escritura vuelve a pasar.
  const stateFile = `/tmp/fake-walker-step-${process.pid}.state`
  const logFile = `${stateFile}.log`
  const binDir = `/tmp/wfc-step-${process.pid}`
  mkdirSync(binDir, { recursive: true })
  writeFileSync(stateFile, "IMPLEMENT.DECIDE")
  writeFileSync(logFile, "")
  // El binario se llama `walker3` a propósito: el wrapper de shell reconoce el
  // CLI por ese nombre y, en `submit`, invalida el estado cacheado.
  fake(`${binDir}/walker3`, [
    "#!/usr/bin/env node",
    'const fs = require("node:fs")',
    `const stateFile = ${JSON.stringify(stateFile)}`,
    `const logFile = ${JSON.stringify(logFile)}`,
    'const tool = String(process.argv[2] || "")',
    'fs.appendFileSync(logFile, process.argv.slice(2).join(" ") + "\\n")',
    'const pos = fs.readFileSync(stateFile, "utf8").trim()',
    'const out = (o) => { console.log(JSON.stringify(o)); process.exit(0) }',
    'if (tool.endsWith("/workflow_state")) {',
    '  if (pos === "IMPLEMENT.DECIDE") out({ ok: true, current_position: "IMPLEMENT.DECIDE", execution_type: "user_input", step_satisfied: false, required_fields: ["decision"], available_gates: ["approve"], next_action: { tool: "transition", detail: "Elegí un gate" }, revision: 1 })',
    '  out({ ok: true, current_position: "IMPLEMENT.DONE", execution_type: "llm_task", step_satisfied: true, next_action: { tool: "advance" }, revision: 2 })',
    '}',
    'if (tool.endsWith("/submit")) { fs.writeFileSync(stateFile, "IMPLEMENT.DONE"); out({ ok: true }) }',
    'if (tool.endsWith("/transition")) { fs.writeFileSync(stateFile, "IMPLEMENT.DONE"); out({ ok: true }) }',
    'if (tool.endsWith("/session_bind")) out({ ok: true, data: { decision: "bound", bound: { spring_id: "SPR-STEP", status: "active", goal: "gate", tasks: [] } } })',
    'if (tool.endsWith("/implement_task")) out({ ok: true, data: { task_id: "step-task" } })',
    'if (tool.endsWith("/project_for_path")) out({ ok: true, project_id: "step_probe" })',
    'if (tool.endsWith("/session_plan")) out({ ok: true, data: { plan: { goal: "probar el paso visible", task: "step-task", next: "decidir", scope: ["x.txt"], acceptance: ["la decision se registra"] } } })',
    'if (tool.endsWith("/agent_context")) out({ ok: true })',
    'if (tool.endsWith("/preflight")) out({ ok: true, files: [], tests: [] })',
    'out({ ok: true })',
  ])
}
if (mode === "intakegoal") {
  // El pedido del usuario tiene que llegar al registro (`session_open`) y al
  // brief del intake. Antes se capturaba después de abrir la sesión y quedaba
  // el placeholder `sesión <id>`: el engine nunca veía la request.
  const logFile = `/tmp/fake-walker-goal-${process.pid}.log`
  writeFileSync(logFile, "")
  fake("/tmp/fake-walker-goal", [
    "#!/usr/bin/env node",
    'const fs = require("node:fs")',
    `const log = ${JSON.stringify(logFile)}`,
    'const tool = String(process.argv[2] || "")',
    'fs.appendFileSync(log, process.argv.slice(2).join(" ") + "\\n")',
    'const out = (o) => { console.log(JSON.stringify(o)); process.exit(0) }',
    'if (tool.endsWith("/session_plan")) out({ ok: true, data: { plan: { goal: "arreglar el login", task: "goal-task", scope: [], acceptance: ["sin romper los tests"] } } })',
    'if (tool.endsWith("/workflow_state")) out({ ok: true, current_position: "START.SELECT", execution_type: "llm_task", step_satisfied: true, revision: 1 })',
    'if (tool.endsWith("/project_for_path")) out({ ok: true, project_id: "goal_probe" })',
    'out({ ok: true })',
  ])
}
if (mode === "normal") {
  // Fixture PROPIO: `normal` no puede depender del `workflow_state` de
  // el proyecto. Ese `task_id` es del PROYECTO y otra corrida lo dejaba en un
  // valor no-auto, con lo que el gate abría antes de declarar (medido: segunda
  // corrida consecutiva). El fake emula la transición real: arranca en `auto-x`
  // y solo `implement_task` lo cambia.
  const stateFile = `/tmp/fake-walker3-normal-${process.pid}.state`
  mkdirSync("/tmp/wfc-normal", { recursive: true })
  fake("/tmp/wfc-normal/walker3", [
    "#!/bin/sh",
    `STATE="${stateFile}"`,
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-NORMAL","status":"active","goal":"normal test","tasks":[]}}}'; exit 0;;`,
    `  */workflow_state) TID=$(cat "$STATE" 2>/dev/null || printf 'auto-x'); printf '{"ok":true,"task_id":"%s","current_position":"START.SELECT","execution_type":"","step_satisfied":true,"revision":1}\\n' "$TID"; exit 0;;`,
    `  */implement_task) TID=$(printf '%s' "$2" | sed -n 's/.*"task_id":"\\([^"]*\\)".*/\\1/p'); printf '%s' "\${TID:-harness-task}" > "$STATE"; printf '{"ok":true,"data":{"task_id":"%s"}}\\n' "\${TID:-harness-task}"; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"normal_probe"}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}
if (mode === "evict") process.env.WALKER_FLIGHT_CONTROL_MAX_SESSIONS = "3"
// Canal de inyección (AE-INV-10): el A/B del harness corre los TRES caminos
// contra el mismo fixture. `inject` es el default (stable).
if (mode === "injectonce") process.env.WALKER_FLIGHT_CONTROL_INJECTION = "once"
if (mode === "injectmessages") process.env.WALKER_FLIGHT_CONTROL_INJECTION = "messages"
if (mode === "gateprompt") {
  // Un gate `user_input` con su prompt real: el plugin debe poner la PREGUNTA
  // en el contexto del modelo, no solo recordar "complete el paso".
  fake("/tmp/fake-walker-gateprompt", [
    "#!/usr/bin/env node",
    'const args = process.argv.slice(2); const tool = String(args[0] || "")',
    'if (tool.endsWith("/session_bind")) { console.log(JSON.stringify({ ok: true, data: { decision: "bound", bound: { spring_id: "SPR-FAKE", status: "active", goal: "gate test", tasks: [] }, suggestion: "continuá" } })); process.exit(0) }',
    'if (tool.endsWith("/workflow_state")) { console.log(JSON.stringify({ ok: true, task_id: "SPEC-002", current_position: "IMPLEMENT.REPORT_GATE", execution_type: "user_input", required_fields: [], step_satisfied: true, revision: 7, prompt: { template_id: "implement/report-gate", template: "IMPLEMENT.REPORT_GATE - Report\\n\\nPresentá el reporte.\\n\\nOptions:\\n    . proceed -> accepted, continue\\n    . refine  -> needs refinement\\n    . cancel  -> cancel task\\n\\n  walker <p>/submit \'{\\"decision\\":\\"proceed\\"}\'" } })); process.exit(0) }',
    `if (tool.endsWith("/project_for_path")) { console.log(JSON.stringify({ ok: true, project_id: "${PROJECT}" })); process.exit(0) }`,
    'console.log(JSON.stringify({ ok: true }))',
  ])
}
if (mode === "reloadverified" || mode === "reloadedit" || mode === "reloaddirty") {
  const stateFile = `/tmp/fake-walker3-reload-${process.pid}.state`
  mkdirSync("/tmp/wfc-reload", { recursive: true })
  // `reloaddirty` prueba el estado SUCIO persistido: un reload tras editar
  // debe conservar wrote=true/verified=false y volver a nudgar.
  const flightState =
    mode === "reloaddirty"
      ? '{"verified":false,"verify_level":"","verified_at":"","dirty_since":"2026-11-19T00:00:00Z","wrote":true,"writes":1,"touched":["x.txt"]}'
      : '{"verified":true,"verify_level":"cli-marker","verified_at":"2026-11-19T00:00:00Z","wrote":true,"writes":1,"touched":["x.txt"]}'
  fake("/tmp/wfc-reload/walker3", [
    "#!/bin/sh",
    `STATE="${stateFile}"`,
    'case "$1" in',
    `  */session_context) echo '{"ok":true,"data":{"flight_state":${flightState}}}'; exit 0;;`,
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-RELOAD","status":"active","goal":"reload test","tasks":[]}}}'; exit 0;;`,
    `  */workflow_state) TID=$(cat "$STATE" 2>/dev/null || printf 'auto-x'); printf '{"ok":true,"task_id":"%s","current_position":"START.SELECT","execution_type":"","step_satisfied":true,"revision":1}\\n' "$TID"; exit 0;;`,
    `  */implement_task) TID=$(printf '%s' "$2" | sed -n 's/.*"task_id":"\\([^"]*\\)".*/\\1/p'); printf '%s' "\${TID:-close-task}" > "$STATE"; printf '{"ok":true,"data":{"task_id":"%s"}}\\n' "\${TID:-close-task}"; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"${PROJECT}"}'; exit 0;;`,
    `  */session_plan) echo '{"ok":true,"data":{"plan":{}}}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  */session_close) echo '{"ok":true}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}

if (mode === "reloadclosedirty" || mode === "reloadcloseverified") {
  // Recarga del plugin SIN hook de contexto: el estado persistido tiene que
  // llegar igual al gate de cierre (hidratación en el camino de cierre). Antes
  // el entry nacía vacío y una sesión sucia cerraba sin verificación.
  mkdirSync("/tmp/wfc-reloadclose", { recursive: true })
  const fs =
    mode === "reloadclosedirty"
      ? '{"verified":false,"verify_level":"","verified_at":"","dirty_since":"2026-11-19T00:00:00Z","wrote":true,"writes":3,"touched":["a.py"]}'
      : '{"verified":true,"verify_level":"cli-marker","verified_at":"2026-11-19T00:00:00Z","dirty_since":"","wrote":true,"writes":1,"touched":["a.py"]}'
  fake("/tmp/wfc-reloadclose/walker3", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_context) echo '{"ok":true,"data":{"flight_state":${fs}}}'; exit 0;;`,
    `  */session_plan) echo '{"ok":true,"data":{"plan":{"goal":"reload close","task":"t","scope":[],"acceptance":["Debe cerrar con verde"]}}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"${PROJECT}"}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}

if (mode === "degraded") {
  // CKG vacío + un `adr-999` de seed: el bloque NO debe citarlo como autoridad
  // (P1.2). Sí debe decir que la memoria está degradada.
  const scopeFile = `${WALKER}/agent-plugins/walker-flight-control/index.js`
  fake("/tmp/fake-walker-degraded", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_plan) echo '{"ok":true,"data":{"plan":{"goal":"Probar CKG degradado","task":"harness-task","next":"implementar y verificar","scope":["${scopeFile}"],"acceptance":["Debe no citar adr-999","Sin romper los tests"]}}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"testproj"}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"current_position":"IMPLEMENT.WORK","execution_type":"llm_task","step_satisfied":true,"task_id":"harness-task","revision":1}'; exit 0;;`,
    `  */preflight) echo '{"ok":true,"files":[{"file":"index.js","callers":5,"heaviest":[{"cyclo":7}]}],"tests":["t1.py"],"memory_health":{"ckg":{"nodes":0,"status":"empty"},"cpg":{"nodes":0,"status":"empty"},"cyclo":{"known":0,"total":0,"status":"unavailable"},"trusted":false}}'; exit 0;;`,
    `  */agent_context) echo '{"ok":true,"memory_health":{"ckg":{"nodes":0,"status":"empty"},"cpg":{"nodes":0,"status":"empty"},"cyclo":{"known":0,"total":0,"status":"unavailable"},"trusted":false},"ckg":{"results":[{"_id":"adr-999","statement":"seed"}]}}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-TEST","tasks":[]}}}'; exit 0;;`,
    `  */implement_task) echo '{"ok":true,"data":{"task_id":"harness-task"}}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}

const SUGGEST_TRACE = `/tmp/wfc-suggest-${process.pid}.jsonl`

if (mode === "injectsuggest") {
  // P1.14: la sugerencia se "sigue" con una ACCIÓN, no sólo con
  // submit/transition. Antes la métrica reportaba 63-81 ignoradas por mirar
  // una única tool. Este modo prueba las DOS direcciones: una escritura deja
  // `followed=true` (por `edit`) y una sesión que sólo recibe el plan y no
  // hace nada sigue emitiendo `workflow_suggestion_ignored` con `reason` — si
  // no, la métrica sería vacua. La 2ª ejecución usa OTRA sesión: el estado
  // del workflow se cachea 60s por plugin, no por sesión.
  const scopeFile = `${WALKER}/agent-plugins/walker-flight-control/index.js`
  fake("/tmp/fake-walker-suggest", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_plan) echo '{"ok":true,"data":{"plan":{"goal":"Probar el seguimiento","task":"harness-task","next":"implementar y verificar","scope":["${scopeFile}"],"acceptance":["Debe registrar seguimiento"]}}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"suggest_proj"}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"current_position":"IMPLEMENT.WORK","execution_type":"llm_task","step_satisfied":true,"task_id":"harness-task","revision":1}'; exit 0;;`,
    `  */preflight) echo '{"ok":true,"files":[],"tests":[]}'; exit 0;;`,
    `  */agent_context) echo '{"ok":true}'; exit 0;;`,
    `  */trace_push) printf '%s\\n' "$2" >> ${SUGGEST_TRACE}; echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-SUG","status":"active","goal":"suggest","tasks":[]}}}'; exit 0;;`,
    `  */implement_task) echo '{"ok":true,"data":{"task_id":"harness-task"}}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
}

const tapDir = `/tmp/wfc-tap-${process.pid}`
const tapFile = `${tapDir}/trace.jsonl`

if (TAPPED.has(mode)) {
  mkdirSync(tapDir, { recursive: true })
  const proxy = `${tapDir}/walker3-tap`
  writeFileSync(
    proxy,
    `#!/usr/bin/env node
const { execFileSync } = require("node:child_process")
const { appendFileSync } = require("node:fs")
const args = process.argv.slice(2)
let out = ""
try {
  out = execFileSync(${JSON.stringify(REAL_CLI)}, args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] })
} catch (e) {
  out = String(e.stdout || "")
  process.exitCode = e.status || 1
}
appendFileSync(${JSON.stringify(tapFile)}, JSON.stringify({ args, out: out.slice(0, 3000) }) + String.fromCharCode(10))
process.stdout.write(out)
`
  )
  chmodSync(proxy, 0o755)
  process.env.WALKER_CLI = proxy
}

// Todos los modos que usan el CLI real o el proxy verifican contra el MISMO
// proyecto con el que invocan (el del fixture o `--project`). Sin esto,
// `normal`/`shellmut`/`persist` dejaban `rt.projectId` vacío y el plugin
// chequeaba el cwd: `isBound` daba falso y bloqueaba escrituras legítimas.
if (mode !== "missing" && !mode.startsWith("inject") && mode !== "bind" && mode !== "noproject") {
  process.env.WALKER_PROJECT_ID = PROJECT
}

if (mode === "gateoff") process.env.WALKER_FLIGHT_CONTROL_INTAKE = "0"

const MOD = new URL("./index.js", import.meta.url).href
const plugin = (await import(MOD)).default

const tools = new Map()
for (const n of ["edit", "write", "read", "implement_task", "walker_implement_task"]) {
  tools.set(n, { name: n, execute: async (input) => ({ ok: true, tool: n, input }) })
}
// `shell` ejecuta de verdad: el desbloqueo pasa por el CLI. Se corre en /tmp.
tools.set("shell", {
  name: "shell",
  execute: async (input) => {
    const command = String(input.command)
    // `closecli` prueba la certificación por el RESULTADO del CLI. El comando
    // es el real; la salida se canjea por un verde determinista para no
    // depender de que el proyecto del harness tenga predicates ejecutables.
    if (mode === "closecli" && /verify_predicates/.test(command)) {
      return {
        ok: true,
        output: JSON.stringify({
          ok: true,
          data: { all_satisfied: true, results: [], failed_count: 0, passed_count: 0, total: 0 },
        }),
        exitCode: 0,
      }
    }
    if (mode === "closeclipipe" && /verify_predicates/.test(command)) {
      // Salida recortada por un pipe: pierde `all_satisfied` y el exitCode.
      // El parser tiene que certificar por `failed_count: 0`.
      return {
        ok: true,
        output:
          '{"data":{"results":[{"passed":true}],"failed_count":0,"passed_count":2,"total":2}}',
      }
    }
    if (
      (mode === "closeclimark" || mode === "closeclimarkpipe") &&
      /verify_predicates/.test(command)
    ) {
      // El cuerpo viene cortado a propósito: el marcador final es la evidencia.
      return {
        ok: true,
        output:
          '{"data":{"results":[{"passed":true}],"failed_count":0,"passed_c\n' +
          "WALKER_RESULT tool=verify_predicates ok=1 passed=2 failed=0 errors=0 total=2 exit=0",
        exitCode: 0,
      }
    }
    if (mode === "closecliredirect" && /verify_predicates/.test(command)) {
      // El comando redirigió su salida a un archivo: para el wrapper el
      // resultado es vacío, aunque el proceso haya salido 0. No es evidencia.
      return { ok: true, output: "", exitCode: 0 }
    }
    if (mode === "closeclipipefail" && /verify_predicates/.test(command)) {
      // Pipe con exit 0 (tail) y SIN marcador: no es certificable.
      return {
        ok: true,
        output: '{"data":{"results":[{"passed":true}],"failed_count":0',
        exitCode: 0,
      }
    }
    const { execSync } = await import("node:child_process")
    const output = execSync(command, {
      encoding: "utf8",
      cwd: "/tmp",
      stdio: ["ignore", "pipe", "ignore"],
    })
    return { ok: true, output }
  },
})
// Verificación por TOOLS (no shell): simula verde/rojo para el gate de cierre
// sin correr pytest real.
tools.set("run_tests", {
  name: "run_tests",
  execute: async () => {
    if (mode === "closetool") {
      // Rojo por la TOOL (no por verify_predicates): la evidencia viaja en
      // `data.output_tail`, igual que en el camino real de `run_tests`.
      return {
        ok: false,
        error: "run_tests: SUITE ROJA (pytest: rc=1, passed=2, failed=1, errors=0)",
        data: {
          passed: false,
          returncode: 1,
          total: 3,
          failures: 1,
          output_tail: "E   assert 3 == 4\nFAILED tests/test_suma.py::test_suma",
        },
      }
    }
    return { ok: true, data: { passed: mode !== "closefail" } }
  },
})
// SPEC-013/014: `verify_predicates` devuelve el conteo (verde o rojo). En
// `closetai` el rojo trae su `output_tail`, que el nudge debe mostrar.
tools.set("verify_predicates", {
  name: "verify_predicates",
  execute: async () =>
    mode === "closetai"
      ? {
          ok: true,
          data: {
            all_satisfied: false,
            passed_count: 1,
            failed_count: 1,
            total: 2,
            output_tail: "E   assert 3 == 4\nFAILED tests/test_suma.py::test_suma",
          },
        }
      : {
          ok: true,
          data: { all_satisfied: true, passed_count: 2, failed_count: 0, total: 2, results: [] },
        },
})

const makeEditor = () => ({
  get: (n) => tools.get(n),
  update: (n, fn) => {
    const t = tools.get(n)
    if (!t) return
    const draft = { ...t }
    fn(draft)
    tools.set(n, draft)
  },
  list: () => [...tools.values()],
  add: () => {},
  remove: () => {},
  namespace: () => {},
})

const captured = {}
const nudges = []
const eventQueue = []
let eventWaiter = null
const pushEvent = (e) => {
  if (eventWaiter) {
    const w = eventWaiter
    eventWaiter = null
    w({ value: e, done: false })
  } else eventQueue.push(e)
}
const eventIterator = {
  [Symbol.asyncIterator]: () => ({
    next: () =>
      eventQueue.length
        ? Promise.resolve({ value: eventQueue.shift(), done: false })
        : new Promise((resolve) => {
            eventWaiter = resolve
          }),
  }),
}
const closeMode =
  mode === "closegate" ||
  mode === "closeok" ||
  mode === "closefail" ||
  mode === "closeshell" ||
  mode === "closerounds" ||
  mode === "closetool" ||
  mode === "closetai" ||
  mode === "closecli" ||
  mode === "reloadverified" ||
  mode === "reloadedit" ||
  mode === "reloaddirty" ||
  mode === "closeclipipe" ||
  mode === "closeclimark" ||
  mode === "closeclimarkpipe" ||
  mode === "closeclipipefail" ||
  mode === "closecliredirect" ||
  mode === "extverify" ||
  mode === "gateoff"
const ctx = {
  location: { directory: dir },
  tool: { transform: async (cb) => { cb(makeEditor()); return { dispose: async () => {} } } },
  session: {
    hook: async (name, cb) => { captured[name] = cb; return { dispose: async () => {} } },
    synthetic: async ({ sessionID, text }) => { nudges.push({ sessionID, text }); return {} },
  },
  event: { subscribe: () => eventIterator },
}

const t0 = Date.now()
const dispose = await plugin.setup(ctx)
const setupMs = Date.now() - t0

const call = async (name, input) => {
  try {
    await tools.get(name).execute(input, { sessionID: SID })
    return "PERMITE"
  } catch (e) {
    if (process.env.HARNESS_DEBUG) console.error("[DENY]", name, e.message)
    return "BLOQUEA"
  }
}

// `normal` corre contra su propio fake para no leer el estado del proyecto;
// el resto usa el CLI real porque prueba la integración de punta a punta.
const CLI_BIN = (mode === "normal" || mode.startsWith("reload") || mode === "stepgate")
  ? process.env.WALKER_CLI || REAL_CLI
  : REAL_CLI
const cli = (task, payload) =>
  `cd ${WALKER} && ${CLI_BIN} ${PROJECT}/${task} '${JSON.stringify({ session_id: SID, ...payload })}'`

if (INJECT_MODES.has(mode)) {
  await call("edit", { path: "x.txt" }) // fuerza el estado de sesión
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "probar la inyeccion del nodo" }] }] }
  await captured.context(event)
  // El texto puede venir por `system` (stable/once) o por el último turno del
  // usuario (messages). Se leen los dos: el modo no cambia lo que se afirma,
  // sólo dónde viaja.
  const eventText = (ev) =>
    [
      ...(ev.system || []).map((b) => b.text || ""),
      ...(ev.messages || []).flatMap((m) =>
        (m.content || m.parts || []).map((p) => p.text || "")
      ),
    ].join("\n---\n")
  const text = eventText(event)
  const okPlan = /\[WALKER\] Plan:/.test(text)
  const okTask = /Tarea:/.test(text) && /Próximo:/.test(text)
  const noTemplate = !/Sprint Monitoring|PHASE 12/.test(text)
  const okBaseline = /Baseline:/.test(text) && /llamadores/.test(text)
  const okDecision = /·\s*adr-\d+/.test(text)
  // ADR-084 F0.2: el espejo del agente aparece tras la primera escritura.
  const okMirror = /Espejo: \d+ writes/.test(text)
  // El tope de 400 es del bloque de PLAN (contrato de atención). El gate de
  // decisión es otro bloque, con su propio tope (1200): medir el total
  // confundía los dos contratos.
  const blocks = (event.system || []).map((b) => b.text || "")
  // El tope de 400 es del bloque de PLAN (contrato de atención), sin importar
  // el canal por el que viaje.
  const planBlocks = text.split("\n---\n").filter((t) => /\[WALKER\] Plan:/.test(t))
  const okCap = planBlocks.length > 0 && planBlocks.every((t) => t.length <= 400)
  const okTotal = text.length <= 1600
  const event2 = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "seguimiento" }] }],
  }
  await captured.context(event2)
  const again = (event2.system || []).map((b) => b.text || "")
  // Tercer request: con el contenido ya estabilizado, el prefijo DEBE repetirse
  // byte-idéntico. Es el contrato real: un cambio de nodo cuesta un miss; los
  // requests siguientes del mismo nodo no.
  const event3 = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "otra vez" }] }],
  }
  await captured.context(event3)
  const again2 = (event3.system || []).map((b) => b.text || "")
  // A/B de los tres canales (AE-INV-10):
  //   stable   -> el prefijo se repite byte-idéntico (cache hit posible)
  //   once     -> los requests siguientes no mandan nada (previo)
  //   messages -> el bloque va al último turno del usuario, system vacío
  let okChannel = false
  let channelNote = ""
  if (mode === "injectonce") {
    okChannel = again.length === 0 && again2.length === 0
    channelNote = `once: system=${again.length}/${again2.length} (esperado 0/0)`
  } else if (mode === "injectmessages") {
    const parts = event3.messages[0].content
    // El contrato es "los bloques viajan en el último turno del usuario", no
    // "en una sola parte": el plan y el paso son bloques separados a propósito
    // (contrato de atención). Se exige que TODAS las partes anexadas sean
    // WALKER y que el system quede vacío.
    const injected = parts.slice(1)
    okChannel =
      again2.length === 0 &&
      injected.length >= 1 &&
      injected.every((p) => /\[WALKER\]/.test(p.text || ""))
    channelNote = `messages: system=${again2.length} partes=${parts.length}`
  } else {
    okChannel =
      again2.length > 0 && again2.length === again.length && again2.every((t, i) => t === again[i])
    channelNote = `stable: mismo prefijo (${again2.length} bloques)`
    if (!okChannel) {
      console.log("dbg-2:", JSON.stringify(again))
      console.log("dbg-3:", JSON.stringify(again2))
    }
  }
  console.log(`bloque de plan               -> ${okPlan}`)
  console.log(`tarea y proximo paso         -> ${okTask}`)
  console.log(`sin template del nodo        -> ${noTemplate}`)
  console.log(`baseline desde walker        -> ${okBaseline}`)
  console.log(`decision del CKG (L3)        -> ${okDecision}`)
  console.log(`espejo del agente (F0.2)     -> ${okMirror}`)
  console.log(`tope 400 del plan            -> ${okCap} (${planBlocks.map((t) => t.length).join(",")})`)
  console.log(`total plan+gate <= 1600      -> ${okTotal} (${text.length})`)
  console.log(`canal ${mode.padEnd(14)}     -> ${okChannel} (${channelNote})`)
  console.log(text.split("\n").slice(0, 4).join(" | "))
  if (typeof dispose === "function") dispose()
  process.exit(
    okPlan && okTask && noTemplate && okBaseline && okDecision && okCap && okTotal && okChannel
      ? 0
      : 1
  )
}

if (mode === "intakegoal") {
  const logFile = `/tmp/fake-walker-goal-${process.pid}.log`
  const pedido = "arreglá el login sin romper los tests"
  // Primer mensaje = bloque inyectado por el entorno: NO es el pedido. El
  // plugin tiene que saltearlo y registrar el mensaje real.
  const event = {
    sessionID: SID,
    system: [],
    messages: [
      { role: "user", content: [{ type: "text", text: "<conversation-checkpoint> resumen previo" }] },
      { role: "user", content: [{ type: "text", text: pedido }] },
    ],
  }
  await captured.context(event)
  const log = readFileSync(logFile, "utf8")
  const okOpen = new RegExp(`/session_open .*"goal":"${pedido}"`).test(log)
  const okBrief = new RegExp(`/brief .*"text":"${pedido}"`).test(log)
  console.log(`${okOpen ? "ok  " : "FAIL"}  el pedido llega al registro (session_open.goal)`)
  console.log(`${okBrief ? "ok  " : "FAIL"}  el pedido llega al brief del intake`)
  if (!(okOpen && okBrief)) console.log(log.slice(0, 600))
  if (typeof dispose === "function") dispose()
  process.exit(okOpen && okBrief ? 0 : 1)
}

if (mode === "verifycounts") {
  // SPEC-014: el bloque del plan dice CUÁNTOS predicados se verificaron.
  // `Verif: 2/2` es el dato; sin él sólo se puede afirmar "verificado".
  await call("edit", { path: "x.txt" })
  await call("verify_predicates", {})
  const event = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "verificar el trabajo" }] }],
  }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okCount = /Verif: 2\/2/.test(text)
  const okPlan = /\[WALKER\] Plan:/.test(text)
  console.log(`${okCount ? "ok  " : "FAIL"}  el bloque muestra 2/2 predicados`)
  console.log(`${okPlan ? "ok  " : "FAIL"}  el bloque sigue siendo el del plan`)
  if (!okCount) console.log(text.slice(0, 320))
  if (typeof dispose === "function") dispose()
  process.exit(okCount && okPlan ? 0 : 1)
}

if (mode === "evidenceadvance") {
  const stateFile = `/tmp/fake-walker-evid-${process.pid}.state`
  const logFile = `${stateFile}.log`
  const ev = (text) => ({
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text }] }],
  })
  const position = () => readFileSync(stateFile, "utf8").trim()

  await captured.context(ev("empezamos"))
  // Declaración por la TOOL (no por shell): es el camino del agente en el
  // perfil `agent` y el que setea `activeTask` + invalida el estado.
  await call("implement_task", {
    task_id: "evid-task",
    objective: "x",
    files: ["x.txt"],
    acceptance: ["y"],
  })
  await captured.context(ev("sin verificar todavía"))
  const sinEvidencia = position()

  await call("edit", { path: "x.txt" })
  await call("run_tests", {})
  await captured.context(ev("ya verifiqué"))
  const conEvidencia = position()

  await captured.context(ev("sigo"))
  const trasGate = position()

  const log = readFileSync(logFile, "utf8")
  const advances = (log.match(/\/advance /g) || []).length
  const checks = [
    ["sin verificación el paso no avanza", sinEvidencia === "IMPLEMENT.WORK"],
    ["con trabajo verificado avanza solo", conEvidencia === "IMPLEMENT.GATE"],
    ["no cruza el gate de decisión", trasGate === "IMPLEMENT.GATE"],
    ["el avance por evidencia queda trazado", /workflow_evidence_advance/.test(log)],
    ["un solo advance (el del plugin)", advances === 1],
  ]
  let failed = 0
  for (const [name, ok] of checks) {
    if (!ok) failed++
    console.log(`${ok ? "ok  " : "FAIL"}  ${name}`)
  }
  console.log(`estado=${sinEvidencia} -> ${conEvidencia} -> ${trasGate} · advances=${advances}`)
  if (typeof dispose === "function") dispose()
  process.exit(failed ? 1 : 0)
}

if (mode === "stepgate") {
  const stateFile = `/tmp/fake-walker-step-${process.pid}.state`
  const logFile = `${stateFile}.log`
  const ev = (text) => ({
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text }] }],
  })

  await captured.context(ev("empezamos"))
  await call("implement_task", {
    task_id: "step-task",
    objective: "x",
    files: ["x.txt"],
    acceptance: ["y"],
  })
  const event = ev("hay que decidir")
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")

  const okStep =
    /paso IMPLEMENT\.DECIDE \(user_input\)/.test(text) &&
    /motor pide: transition/.test(text)
  const denied = await call("edit", { path: "x.txt" })
  const logDenied = readFileSync(logFile, "utf8")
  const okDeny = denied === "BLOQUEA"
  const okRemedy = /"gate":"decision"/.test(logDenied) && /submit/.test(logDenied)

  await call("shell", {
    command: cli("submit", { decision: "approve" }),
  })
  // Un turno después: el contexto relee el paso ya satisfecho.
  await captured.context(ev("ya decidí"))
  const allowed = await call("edit", { path: "x.txt" })
  const okAfter = allowed === "PERMITE"

  const checks = [
    ["el bloque muestra el paso y lo que pide el motor", okStep],
    ["una decisión abierta bloquea la escritura", okDeny],
    ["el deny trae el remedio (submit) y se traza", okRemedy],
    ["registrada la decisión, la escritura pasa", okAfter],
  ]
  let failed = 0
  for (const [name, ok] of checks) {
    if (!ok) failed++
    console.log(`${ok ? "ok  " : "FAIL"}  ${name}`)
  }
  if (!okStep) console.log(text.slice(0, 300))
  if (typeof dispose === "function") dispose()
  process.exit(failed ? 1 : 0)
}

if (mode === "handoff") {
  // SPEC-006: continuidad = goal + abiertas + next, en el bloque, sin releer
  // el repo.
  const event = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "seguimos con el login" }] }],
  }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okOpen = /abiertas: 2 \(T-1, T-2\)/.test(text)
  const okNext = /next previo: correr la suite de login/.test(text)
  const okGoal = /goal: login roto/.test(text)
  console.log(`${okOpen ? "ok  " : "FAIL"}  tareas abiertas en el handoff`)
  console.log(`${okNext ? "ok  " : "FAIL"}  next previo en el handoff`)
  console.log(`${okGoal ? "ok  " : "FAIL"}  goal en el handoff`)
  if (!(okOpen && okNext && okGoal)) console.log(text.slice(0, 400))
  if (typeof dispose === "function") dispose()
  process.exit(okOpen && okNext && okGoal ? 0 : 1)
}

if (mode === "approval") {
  // SPEC-011: un plan no trivial sin aprobación se muestra PENDIENTE.
  await call("edit", { path: "x.txt" })
  const event = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "empezamos" }] }],
  }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okPending = /Aprobado: PENDIENTE/.test(text)
  const okPlan = /\[WALKER\] Plan:/.test(text)
  console.log(`${okPending ? "ok  " : "FAIL"}  el bloque muestra la aprobacion pendiente`)
  console.log(`${okPlan ? "ok  " : "FAIL"}  el bloque sigue siendo el del plan`)
  if (!okPending) console.log(text.slice(0, 400))
  if (typeof dispose === "function") dispose()
  process.exit(okPending && okPlan ? 0 : 1)
}

if (mode === "degraded") {
  await call("edit", { path: "x.txt" })
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "probar ckg degradado" }] }] }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okPlan = /\[WALKER\] Plan:/.test(text)
  const okBaseline = /Baseline:/.test(text)
  const noAdr = !/adr-\d+/.test(text)
  const okDegraded = /CKG degradado/.test(text)
  console.log(`bloque de plan               -> ${okPlan}`)
  console.log(`baseline presente            -> ${okBaseline}`)
  console.log(`no cita adr-999              -> ${noAdr}`)
  console.log(`dice CKG degradado           -> ${okDegraded}`)
  console.log(text.split("\n").slice(0, 3).join(" | "))
  if (typeof dispose === "function") dispose()
  process.exit(okPlan && okBaseline && noAdr && okDegraded ? 0 : 1)
}

if (mode === "autodeclare") {
  const event = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "arreglar" }] }],
  }
  await captured.context(event)
  await call("shell", {
    command: cli("session_bind", {
      mode: "new",
      objective: "Auto-declare. Debe declarar desde el plan. Sin romper los tests.",
    }),
  })
  // El plan declara la tarea (lo normal cuando el agente usa session_plan).
  await call("shell", {
    command: cli("session_plan", { mode: "set", task: "declared-by-plan" }),
  })
  const out = {}
  out["edit sin implement_task"] = await call("edit", { path: "a.txt" })
  const st = JSON.parse(
    execFileSync(REAL_CLI, [`${PROJECT}/workflow_state`, "{}"], { encoding: "utf8" })
  )
  out["task en workflow"] = String(st.task_id || "") === "declared-by-plan" ? "SI" : `NO(${st.task_id})`
  const expected = { "edit sin implement_task": "PERMITE", "task en workflow": "SI" }
  let failed = 0
  for (const [k, v] of Object.entries(out)) {
    const ok = v === expected[k]
    if (!ok) failed++
    console.log(`${ok ? "ok  " : "FAIL"}  ${k.padEnd(22)} -> ${v} (esperado ${expected[k]})`)
  }
  console.log(`mode=autodeclare fail=${failed}`)
  if (typeof dispose === "function") dispose()
  process.exit(failed ? 1 : 0)
}

if (mode === "bind") {
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "quiero arreglar el login" }] }] }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n")
  const okDecision = /session_bind=(bound|resume_available|no_work)/.test(text)
  const okPrev = /Sprint: SPR-/.test(text) || /No hay trabajo previo/.test(text)
  const okCmd = /\/session_bind /.test(text)
  console.log(`decision en el bloque -> ${okDecision}`)
  console.log(`contexto previo       -> ${okPrev}`)
  console.log(`comando para decidir  -> ${okCmd}`)
  console.log(text.slice(0, 420))
  if (typeof dispose === "function") dispose()
  process.exit(okDecision && okPrev && okCmd ? 0 : 1)
}

if (mode === "gateprompt") {
  const event = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "mostrame el gate" }] }],
  }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n---\n")
  const okPos = /Gate de decisión: IMPLEMENT\.REPORT_GATE/.test(text)
  const okNoDecide = /No decidas por el usuario/.test(text)
  const okOptions = /proceed/.test(text) && /refine/.test(text) && /cancel/.test(text)
  const okSubmit = /\/submit/.test(text)
  const event2 = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "sigo esperando" }] }],
  }
  await captured.context(event2)
  // AE-INV-10: el gate es pregunta abierta, no evento de una vez. En `stable`
  // se repite byte-idéntico (mismos bytes = prefijo cacheable); en `once` se
  // manda una sola vez. Este modo corre el default (stable).
  const repeat = (event2.system || []).map((b) => b.text || "")
  const stable = repeat.length === 1 && /Gate de decisión: IMPLEMENT\.REPORT_GATE/.test(repeat[0])
  console.log(`posición del gate            -> ${okPos}`)
  console.log(`no decidir por el usuario    -> ${okNoDecide}`)
  console.log(`opciones del template        -> ${okOptions}`)
  console.log(`cómo cerrar el gate          -> ${okSubmit}`)
  console.log(`gate repetido idéntico       -> ${stable} (${repeat.length} bloque/s)`)
  console.log(text.split("\n").slice(-6).join(" | "))
  if (typeof dispose === "function") dispose()
  process.exit(okPos && okNoDecide && okOptions && okSubmit && stable ? 0 : 1)
}

if (mode === "injectsuggest") {
  const SID2 = `${SID}-silent`
  const read = () => {
    try {
      return readFileSync(SUGGEST_TRACE, "utf8")
        .trim()
        .split("\n")
        .filter(Boolean)
        .map((l) => JSON.parse(l))
    } catch {
      return []
    }
  }

  // Ejecución 1: el plan se inyecta y el agente ESCRIBE.
  const event = {
    sessionID: SID,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "arreglar el bug" }] }],
  }
  await captured.context(event)
  const injectedText = (event.system || []).map((b) => b.text || "").join("\n")
  const wrote = await call("edit", { path: "index.js" })
  pushEvent({ type: "session.execution.succeeded", sessionID: SID })
  await new Promise((r) => setTimeout(r, 1500))

  // Ejecución 2 (otra sesión): recibe el plan y NO hace nada.
  const event2 = {
    sessionID: SID2,
    system: [],
    messages: [{ role: "user", content: [{ type: "text", text: "sólo mirá" }] }],
  }
  await captured.context(event2)
  const silentText = (event2.system || []).map((b) => b.text || "").join("\n")
  pushEvent({ type: "session.execution.succeeded", sessionID: SID2 })
  await new Promise((r) => setTimeout(r, 1500))

  const pushes = read()
  const injected = pushes.filter((e) => e.event_type === "workflow_state_injected")
  const mine = injected.filter((e) => e.session_id === SID)
  const silent = injected.filter((e) => e.session_id === SID2)
  const ignored = pushes.filter((e) => e.event_type === "workflow_suggestion_ignored")
  const ignoredMine = ignored.filter((e) => e.session_id === SID)
  const ignoredSilent = ignored.filter((e) => e.session_id === SID2)

  const checks = [
    ["plan inyectado", /\[WALKER\] Plan:/.test(injectedText)],
    ["escritura permitida", wrote === "PERMITE"],
    ["seguimiento registrado", mine.length === 1 && mine[0].data.followed === true],
    ["por acción (edit)", mine[0]?.data.followedBy === "edit"],
    ["sesión activa sin ignoradas", ignoredMine.length === 0],
    ["2ª sesión recibió el plan", /\[WALKER\] Plan:/.test(silentText)],
    ["sin acción queda en falso", silent.length === 1 && silent[0].data.followed === false],
    ["ignorada con motivo", ignoredSilent.length === 1 && Boolean(ignoredSilent[0]?.data?.reason)],
  ]
  let failed = 0
  for (const [name, ok] of checks) {
    if (!ok) failed++
    console.log(`${ok ? "ok  " : "FAIL"}  ${name}`)
  }
  if (mine[0]) console.log("injected:", JSON.stringify(mine[0].data).slice(0, 220))
  if (ignoredSilent[0]) console.log("ignored :", JSON.stringify(ignoredSilent[0].data).slice(0, 220))
  console.log(`mode=injectsuggest fail=${failed}`)
  if (typeof dispose === "function") dispose()
  process.exit(failed ? 1 : 0)
}

if (mode === "noproject") {
  // Sin proyecto resoluble: el plugin no debe callarse; avisa una vez.
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "hola" }] }] }
  await captured.context(event)
  const text = (event.system || []).map((b) => b.text || "").join("\n")
  const okNotice = /Sin proyecto/.test(text) && /_\/prepare/.test(text)
  console.log(`aviso de proyecto ausente -> ${okNotice}`)
  console.log(text.slice(0, 300))
  if (typeof dispose === "function") dispose()
  process.exit(okNotice ? 0 : 1)
}

if (closeMode) {
  // 1) abrir la sesión (hook), 2) vincular, 3) declarar, 4) escribir.
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "arreglar" }] }] }
  await captured.context(event)
  await call("shell", {
    command: cli("session_bind", { mode: "new", objective: "Gate de cierre: sesión de prueba." }),
  })
  await call("shell", {
    command: cli("implement_task", {
      task_id: "close-task",
      objective: "Tarea del gate de cierre. Debe exigir verificación. Sin romper los tests.",
      files: ["x.txt"],
      acceptance: ["la tarea cierra con verificación verde"],
    }),
  })
  if (mode !== "reloadverified" && mode !== "reloaddirty") {
    await call("edit", { path: "x.txt" })
  }
  if (mode === "closeok" || mode === "closefail") {
    await call("run_tests", {})
  }
  if (mode === "closeshell") {
    // Con acceptance declarada, el shell NO debe certificar: se espera nudge.
    await call("shell", { command: "echo pytest" })
  }
  if (mode === "closetool") {
    // SPEC-013 por el camino de `run_tests`: el rojo de la tool también trae
    // su cola y el nudge tiene que mostrarla.
    await call("run_tests", {})
  }
  if (mode === "closetai") {
    // SPEC-013: un rojo con salida. El nudge debe traer la cola del fallo y el
    // archivo tocado, y no certificar el cierre.
    await call("verify_predicates", {})
  }
  if (mode === "closerounds") {
    // AE-INV-12: hasta CLOSE_ROUNDS-1 insiste; en la última escala al humano
    // y después calla. Antes era un solo nudge y luego "verde" sin evidencia.
    const counts = []
    for (let i = 1; i <= 4; i++) {
      pushEvent({ type: "session.execution.succeeded", sessionID: SID })
      await new Promise((r) => setTimeout(r, 900))
      counts.push(nudges.length)
    }
    const first = (nudges[0] && nudges[0].text) || ""
    const third = (nudges[2] && nudges[2].text) || ""
    const checks = [
      ["insiste en la ronda 1", counts[0] === 1],
      ["insiste en la ronda 2", counts[1] === 2],
      ["escala en la ronda 3", counts[2] === 3 && /Tercera ronda/.test(third)],
      ["calla en la ronda 4", counts[3] === 3],
      ["la primera no escalaba", /No cierres/.test(first) && !/Tercera ronda/.test(first)],
    ]
    let failed = 0
    for (const [name, ok] of checks) {
      if (!ok) failed++
      console.log(`${ok ? "ok  " : "FAIL"}  ${name}`)
    }
    console.log(`rondas=${counts.join(",")}`)
    if (typeof dispose === "function") dispose()
    process.exit(failed ? 1 : 0)
  }
  if (mode === "extverify") {
    // P1.11: la verificación ocurre en la TOOL real, fuera del wrapper del
    // plugin (como en Code Mode / shell del host). El plugin no la ve; antes
    // de nudgar debe hidratarla del TRACE (`session_context.flight_state`).
    writeFileSync(`${dir}/x.txt`, "x")
    execFileSync(REAL_CLI, [
      `${PROJECT}/verify_predicates`,
      JSON.stringify({
        session_id: SID,
        task_id: "close-task",
        project_root: dir,
        predicates: [{ type: "file_exists", path: "x.txt" }],
      }),
    ], { encoding: "utf8" })
  }
  if (
    mode === "closecli" ||
    mode === "closeclipipe" ||
    mode === "closeclimark" ||
    mode === "closeclimarkpipe" ||
    mode === "closeclipipefail" ||
    mode === "closecliredirect"
  ) {
    // CLI real con `acceptance` declarada: el plugin debe certificar por el
    // RESULTADO (Code Mode invoca las tools por shell, no por el wrapper).
    // Los modos "pipe" agregan el pipe real: el exit code no es del CLI.
    // `closecliredirect`: exit 0 pero la salida va a un archivo. El plugin no
    // ve el resultado, así que no puede certificar (el nudge debe explicarlo).
    const pipe =
      mode === "closeclipipefail" || mode === "closeclimarkpipe"
        ? " | tail -c 400"
        : mode === "closecliredirect"
          ? " > /tmp/wfc-redirect.log"
          : ""
    await call("shell", {
      command:
        `cd ${WALKER} && ./bin/walker3 ${PROJECT}/verify_predicates '${JSON.stringify({
          session_id: SID,
          task_id: "close-task",
        })}'` + pipe,
    })
  }
  // El evento de fin se empuja DESPUÉS de los pasos: si llega antes del edit,
  // el gate de cierre no ve `wrote` y no avisa.
  pushEvent({ type: "session.execution.succeeded", sessionID: SID })
  await new Promise((r) => setTimeout(r, 1500))
  const nudged = nudges.length
  const okText = nudged > 0 && /No cierres/.test(nudges[0].text)
  const certified = new Set([
    "closeok",
    "closecli",
    "closeclipipe",
    "closeclimark",
    "closeclimarkpipe",
    "extverify",
    "reloadverified",
  ])
  const expectedNudge = !certified.has(mode)
  // Un verde no certificado por pipe debe explicar el motivo, no sólo nudgar.
  const hintModes = ["closeclipipefail", "closecliredirect"]
  const okHint =
    !hintModes.includes(mode) ||
    (nudged > 0 && /pipe|redirecci|WALKER_RESULT/i.test(nudges[0].text))
  const ok = (expectedNudge ? okText : nudged === 0) && okHint
  if (mode === "closetool") {
    const text = (nudges[0] && nudges[0].text) || ""
    const checks = [
      ["la cola del fallo de run_tests viaja en el nudge", /assert 3 == 4/.test(text)],
      ["nombra el archivo tocado", /x\.txt/.test(text)],
      ["el ultimo intento es run_tests", /run_tests/.test(text)],
      ["rojo de la tool no certifica", ok && /No cierres/.test(text)],
    ]
    let failed = 0
    for (const [name, pass] of checks) {
      if (!pass) failed++
      console.log(`${pass ? "ok  " : "FAIL"}  ${name}`)
    }
    if (failed) console.log(text.slice(0, 400))
    if (typeof dispose === "function") dispose()
    process.exit(failed ? 1 : 0)
  }
  if (mode === "closetai") {
    const text = (nudges[0] && nudges[0].text) || ""
    const checks = [
      ["la cola del fallo viaja en el nudge", /assert 3 == 4/.test(text)],
      ["el archivo tocado se nombra", /x\.txt/.test(text)],
      ["el rojo no certifica", ok && /No cierres/.test(text)],
    ]
    let failed = 0
    for (const [name, pass] of checks) {
      if (!pass) failed++
      console.log(`${pass ? "ok  " : "FAIL"}  ${name}`)
    }
    if (typeof dispose === "function") dispose()
    process.exit(failed ? 1 : 0)
  }
  console.log(`${ok ? "ok  " : "FAIL"} nudges=${nudged} (esperado ${expectedNudge ? 1 : 0})`)
  if (mode === "closeclipipefail") {
    console.log(`${okHint ? "ok  " : "FAIL"}  hint de pipe en el nudge`)
  }
  if (mode === "closecliredirect") {
    console.log(`${okHint ? "ok  " : "FAIL"}  hint de salida oculta en el nudge`)
  }
  if (nudged) console.log(nudges[0].text.split("\n")[0])
  if (typeof dispose === "function") dispose()
  process.exit(ok ? 0 : 1)
}

if (mode === "reloadclosedirty" || mode === "reloadcloseverified") {
  // NO se llama captured.context: el plugin arranca "en frío" (recarga) y el
  // agente cierra. El estado persistido debe decidir el gate.
  pushEvent({ type: "session.execution.succeeded", sessionID: SID })
  await new Promise((r) => setTimeout(r, 1500))
  const expected = mode === "reloadclosedirty" ? 1 : 0
  const ok = nudges.length === expected
  console.log(
    `${ok ? "ok  " : "FAIL"} nudges=${nudges.length} (esperado ${expected}) [sin hook de contexto]`
  )
  if (nudges.length) console.log(nudges[0].text.split("\n")[0])
  if (typeof dispose === "function") dispose()
  process.exit(ok ? 0 : 1)
}

if (mode === "shelltmp") {
  // Un redirect a /tmp (con 2>&1) NO es una mutación del proyecto: no puede
  // invalidar el verde recién obtenido. Medido en sesión real: el agente
  // captura la salida de la verificación a /tmp y el nudge volvía a aparecer.
  fake("/tmp/fake-walker-shelltmp", [
    "#!/bin/sh",
    'case "$1" in',
    `  */session_bind) echo '{"ok":true,"data":{"decision":"bound","bound":{"spring_id":"SPR-TMP","status":"active","goal":"tmp test","tasks":[]}}}'; exit 0;;`,
    `  */workflow_state) echo '{"ok":true,"task_id":"auto-x","current_position":"START.SELECT","execution_type":"","step_satisfied":true,"revision":1}'; exit 0;;`,
    `  */implement_task) echo '{"ok":true,"data":{"task_id":"tmp-task"}}'; exit 0;;`,
    `  */project_for_path) echo '{"ok":true,"project_id":"tmp_probe"}'; exit 0;;`,
    `  */session_plan) echo '{"ok":true,"data":{"plan":{"goal":"tmp","task":"t","scope":[],"acceptance":["Debe cerrar con verde"]}}}'; exit 0;;`,
    `  */trace_push) echo '{"ok":true}'; exit 0;;`,
    `  */session_open) echo '{"ok":true}'; exit 0;;`,
    `  *) echo '{"ok":true}'; exit 0;;`,
    "esac",
  ])
  const WCLI = process.env.WALKER_CLI
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "arreglar" }] }] }
  await captured.context(event)
  await call("shell", {
    command: `${WCLI} tmp_probe/session_bind '${JSON.stringify({ session_id: SID, mode: "new", objective: "tmp" })}'`,
  })
  await call("shell", {
    command: `${WCLI} tmp_probe/implement_task '${JSON.stringify({
      session_id: SID,
      task_id: "tmp-task",
      objective: "Debe cerrar con verde. Sin romper los tests.",
      files: ["x.txt"],
      acceptance: ["cierra con verde"],
    })}'`,
  })
  await call("edit", { path: "x.txt" })
  await call("run_tests", {}) // verde por tools
  await call("shell", { command: "pytest -q 2>&1 | tail -n 1 > /tmp/wfc-shelltmp.log" })
  pushEvent({ type: "session.execution.succeeded", sessionID: SID })
  await new Promise((r) => setTimeout(r, 1500))
  const ok = nudges.length === 0
  console.log(
    `${ok ? "ok  " : "FAIL"} nudges=${nudges.length} (esperado 0; redirect a /tmp no es mutación)`
  )
  if (nudges.length) console.log(nudges[0].text.split("\n")[0])
  if (typeof dispose === "function") dispose()
  process.exit(ok ? 0 : 1)
}

if (mode === "scope") {
  const event = { sessionID: SID, system: [], messages: [{ role: "user", content: [{ type: "text", text: "arreglar" }] }] }
  await captured.context(event)
  await call("shell", {
    command: cli("session_bind", { mode: "new", objective: "Probar el gate de desvío. Debe acotar el alcance." }),
  })
  await call("shell", {
    command: cli("implement_task", {
      task_id: "scope-task",
      objective: "Tarea con alcance acotado. Debe permitir solo allowed.py. Sin romper los tests.",
      files: ["allowed.py"],
    }),
  })
  const out = {}
  out["edit en alcance"] = await call("edit", { path: "allowed.py" })
  // P1.10: el gate NEGOCIA (session_plan mode=add) en vez de bloquear y
  // esperar que el agente reconstruya el scope completo. El desvío queda
  // trazado (`flight_control_scope_expanded`).
  out["edit fuera"] = await call("edit", { path: "otro/x.txt" })
  out["edit fuera otra vez"] = await call("edit", { path: "otro/x.txt" })
  const plan = JSON.parse(
    execFileSync(REAL_CLI, [
      `${PROJECT}/session_plan`,
      JSON.stringify({ session_id: SID, mode: "get" }),
    ], { encoding: "utf8" })
  )
  const scope = (plan?.data?.plan?.scope) || []
  out["plan ampliado"] = scope.some((f) => String(f).endsWith("otro/x.txt")) ? "SI" : "NO"
  const expected = {
    "edit en alcance": "PERMITE",
    "edit fuera": "PERMITE",
    "edit fuera otra vez": "PERMITE",
    "plan ampliado": "SI",
  }
  let failed = 0
  for (const [k, v] of Object.entries(out)) {
    const ok = v === expected[k]
    if (!ok) failed++
    console.log(`${ok ? "ok  " : "FAIL"}  ${k.padEnd(20)} -> ${v} (esperado ${expected[k]})`)
  }
  console.log(`mode=scope fail=${failed}`)
  if (typeof dispose === "function") dispose()
  process.exit(failed ? 1 : 0)
}

if (TAPPED.has(mode)) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms))
  const hookSid = async (sid) => {
    const ev = {
      sessionID: sid,
      system: [],
      messages: [{ role: "user", content: [{ type: "text", text: "tarea" }] }],
    }
    await captured.context(ev)
    return (ev.system || []).map((b) => b.text || "").join("\n")
  }
  const hook = () => hookSid(SID)

  if (mode === "evict") {
    for (let i = 1; i <= 3; i++) {
      await hookSid(`evict-${i}`)
      pushEvent({ type: "session.execution.succeeded", sessionID: `evict-${i}` })
      await wait(800)
    }
    await hookSid("evict-4")
    pushEvent({ type: "session.execution.succeeded", sessionID: "evict-4" })
    await wait(1500)

    // Reapertura: la sesión liberada vuelve a usarse y debe recrearse. Este es
    // el caso que faltaba: la evicción no puede dejar la sesión "muerta".
    await hookSid("evict-1")
    pushEvent({ type: "session.execution.succeeded", sessionID: "evict-1" })
    await wait(1500)

    const tap = readFileSync(tapFile, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l))
    const pushes = tap
      .filter((e) => /\/trace_push$/.test(String(e.args[0] || "")))
      .map((e) => JSON.parse(e.args[1]))
    const lastInj = (sid) => {
      const hits = pushes.filter((p) => p.event_type === "workflow_state_injected" && p.session_id === sid)
      return hits.length ? hits[hits.length - 1].data.sessionsState || [] : []
    }
    const ss4 = lastInj("evict-4")
    const keys4 = ss4.map((x) => x.k)
    const evictEvents = pushes.filter((p) => p.event_type === "session_evicted" && p.session_id === "evict-4")
    const evictEvent = evictEvents[evictEvents.length - 1]
    const ss1 = lastInj("evict-1")
    const reopened = ss1.find((x) => x.k === "evict-1")
    const evictedKeys = (evictEvent?.data?.evicted_keys || [])

    // Contrato: nunca supera MAX_SESSIONS, conserva la actual y libera las
    // cerradas más viejas (con 3 cerradas + 1 nueva, caen las dos primeras).
    const evictionOk =
      ss4.length <= 3 &&
      keys4.includes("evict-4") &&
      !keys4.includes("evict-1") &&
      !keys4.includes("evict-2")
    // Traza: la evicción deja rastro y cuenta cuántas liberó.
    const traceOk =
      Boolean(evictEvent) &&
      evictEvent.data.evicted === 2 &&
      evictedKeys.includes("evict-1") &&
      evictedKeys.includes("evict-2") &&
      ss4.some((x) => x.k === "evict-4" && x.e === 2)
    // Reapertura: vuelve al Map como entrada nueva (evicted=0), con actividad.
    const reopenOk =
      Boolean(reopened) && reopened.i >= 1 && reopened.e === 0 && ss1.length <= 3
    const ok = evictionOk && traceOk && reopenOk
    console.log(`${ok ? "ok  " : "FAIL"}  evicción libera cerradas: ${JSON.stringify(keys4)} (size=${ss4.length})`)
    console.log(`${traceOk ? "ok  " : "FAIL"}  traza de evicción: evicted=${evictEvent?.data?.evicted} keys=${JSON.stringify(evictedKeys)} e=${JSON.stringify(ss4.map((x) => x.e))}`)
    console.log(`${reopenOk ? "ok  " : "FAIL"}  reapertura: ${JSON.stringify(reopened)} (size=${ss1.length})`)
    if (typeof dispose === "function") dispose()
    process.exit(ok ? 0 : 1)
  }

  await hook() // abre la sesión
  await call("shell", { command: cli("session_bind", { mode: "new", objective: `${mode} test.` }) })

  let planInjections = []
  if (mode === "idem") {
    planInjections = [await hook(), await hook()]
  } else {
    await call("shell", {
      command: cli("implement_task", {
        task_id: `${mode}-task`,
        objective: "Tarea persistente. Sin romper los tests.",
        files: mode === "shellmut" ? ["out.txt"] : ["a.txt", "b.txt"],
      }),
    })
    await hook()
    if (mode === "shellmut") {
      await call("shell", { command: "printf x >> out.txt" })
      pushEvent({ type: "session.execution.succeeded", sessionID: SID })
      await wait(1500)
    } else {
      await call("edit", { path: "a.txt" })
      pushEvent({ type: "session.execution.succeeded", sessionID: SID })
      await wait(1500)
      await hook()
      await call("edit", { path: "b.txt" })
      pushEvent({ type: "session.execution.succeeded", sessionID: SID })
      await wait(1500)
    }
  }
  if (mode === "idem") {
    pushEvent({ type: "session.execution.succeeded", sessionID: SID })
    await wait(1500)
    planInjections.push(await hook()) // nueva ejecución: reabre sin reinyectar
    pushEvent({ type: "session.execution.succeeded", sessionID: SID })
    await wait(1500)
  }

  const tap = readFileSync(tapFile, "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l))
  const pushes = tap
    .filter((e) => /\/trace_push$/.test(String(e.args[0] || "")))
    .map((e) => JSON.parse(e.args[1]))
  const metrics = pushes.filter((p) => p.event_type === "session_metrics")
  const opens = tap.filter((e) => /\/session_open$/.test(String(e.args[0] || ""))).length
  const closes = tap.filter((e) => /\/session_close$/.test(String(e.args[0] || ""))).length
  const last = metrics.length ? metrics[metrics.length - 1].data : null

  const cycles = mode === "idem" || mode === "persist" ? 2 : 1
  const checks = []
  checks.push([`session_open ${cycles} vez/veces`, opens === cycles])
  checks.push([`session_close ${cycles} vez/veces`, closes === cycles])
  if (mode === "persist") {
    checks.push(["metrics emitidas", metrics.length >= 2])
    checks.push(["writes acumuladas", last && last.writes === 2])
    checks.push(["primera mutación = a.txt", last && String(last.first_mutation?.file || "").endsWith("a.txt")])
    checks.push(["touched a.txt+b.txt", last && ["a.txt", "b.txt"].every((f) => (last.touched || []).some((t) => String(t).endsWith(f)))])
  } else if (mode === "shellmut") {
    checks.push(["writes=1 por shell", last && last.writes === 1])
    checks.push(["firstMutation via shell", last && last.first_mutation?.via === "shell"])
    checks.push(["file del redirect", last && String(last.first_mutation?.file || "").endsWith("out.txt")])
  } else {
    // AE-INV-10: el plan se REPITE con los mismos bytes en cada request del
    // mismo nodo (prefijo cacheable). Lo que no puede pasar es que cambie el
    // texto entre requests: eso sí es reinyectar.
    const planLines = planInjections
      .map((t) => String(t || "").split("\n").find((l) => /\[WALKER\] Plan:/.test(l)) || "")
      .filter(Boolean)
    checks.push(["plan estable", planLines.length >= 1 && planLines.every((t) => t === planLines[0])])
    if (planLines.length && !planLines.every((t) => t === planLines[0])) {
      console.log("dbg-plan:", JSON.stringify(planLines))
    }
    checks.push(["sin mutaciones", last && last.writes === 0])
  }

  let failed = 0
  for (const [name, ok] of checks) {
    if (!ok) failed++
    console.log(`${ok ? "ok  " : "FAIL"}  ${name}`)
  }
  if (last) console.log("last metrics:", JSON.stringify({ writes: last.writes, firstMutation: last.first_mutation, touched: last.touched }).slice(0, 220))
  console.log(`mode=${mode} fail=${failed}`)
  try { if (mode === "shellmut") (await import("node:fs")).rmSync("/tmp/wfc-h-out", { force: true }) } catch {}
  if (typeof dispose === "function") dispose()
  process.exit(failed ? 1 : 0)
}

const res = {}
const bindCmd = cli("session_bind", { mode: "new", objective: "Probar el binding en el harness. Debe ligar la sesion al sprint." })
const taskCmd = cli("implement_task", {
  task_id: "harness-task",
  objective: "Tarea del harness. Debe liberar el gate. Sin romper los tests.",
  files: ["x.txt"],
})

if (mode === "readonly") {
  // ADR-084 F3a / FC-INV-04: sin sprint, observar no se bloquea. El protocolo
  // (remedio) es alcanzable incluso con pipe. Escribir y lo no demostrable
  // siguen bloqueados (FC-INV-06).
  res["pwd"] = await call("shell", { command: "pwd" })
  res["git version"] = await call("shell", { command: "git --version" })
  res["ls"] = await call("shell", { command: "ls -la" })
  res["help"] = await call("shell", { command: "walker3 --help" })
  res["pipe"] = await call("shell", {
    command: `walker3 ${PROJECT}/workflow_state '{}' | head -1`,
  })
  res["write"] = await call("shell", { command: "printf x > z.txt" })
  res["unknown"] = await call("shell", {
    command: "python3 -c 'print(1)'",
  })
} else if (mode === "normal" || mode === "badbind" || mode === "baddeclare") {
  res["edit sin nada"] = await call("edit", { path: "x.txt" })
  if (mode === "badbind") {
    res["bind intento 1"] = await call("shell", { command: bindCmd })
    res["edit tras bind 1"] = await call("edit", { path: "x.txt" })
    res["bind intento 2"] = await call("shell", { command: bindCmd })
    res["edit tras bind 2"] = await call("edit", { path: "x.txt" })
  } else if (mode === "baddeclare") {
    res["bind intento 1"] = await call("shell", { command: bindCmd })
    res["edit sin tarea"] = await call("edit", { path: "x.txt" })
    res["task intento 1"] = await call("shell", { command: taskCmd })
    res["edit tras task 1"] = await call("edit", { path: "x.txt" })
    res["task intento 2"] = await call("shell", { command: taskCmd })
    res["edit tras task 2"] = await call("edit", { path: "x.txt" })
  } else {
    res["bind"] = await call("shell", { command: bindCmd })
    res["edit sin tarea"] = await call("edit", { path: "x.txt" })
    res["implement_task"] = await call("shell", { command: taskCmd })
    res["edit final"] = await call("edit", { path: "x.txt" })
  }
} else {
  // walker caído / colgado / basura: fail-open en TODO.
  res["edit"] = await call("edit", { path: "x.txt" })
  res["write"] = await call("write", { path: "x.txt", content: "x" })
  res["shell no-walker"] = await call("shell", { command: "echo hola" })
  res["edit 2"] = await call("edit", { path: "x.txt" })
}

const expected = {
  readonly: { pwd: "PERMITE", "git version": "PERMITE", ls: "PERMITE", help: "PERMITE", pipe: "PERMITE", write: "BLOQUEA", unknown: "BLOQUEA" },
  normal: { "edit sin nada": "BLOQUEA", bind: "PERMITE", "edit sin tarea": "BLOQUEA", implement_task: "PERMITE", "edit final": "PERMITE" },
  badbind: { "edit sin nada": "BLOQUEA", "bind intento 1": "PERMITE", "edit tras bind 1": "BLOQUEA", "bind intento 2": "PERMITE", "edit tras bind 2": "PERMITE" },
  baddeclare: { "edit sin nada": "BLOQUEA", "bind intento 1": "PERMITE", "edit sin tarea": "BLOQUEA", "task intento 1": "PERMITE", "edit tras task 1": "BLOQUEA", "task intento 2": "PERMITE", "edit tras task 2": "PERMITE" },
}[mode] || { edit: "PERMITE", write: "PERMITE", "shell no-walker": "PERMITE", "edit 2": "PERMITE" }

let failed = 0
for (const [k, v] of Object.entries(res)) {
  const ok = v === expected[k]
  if (!ok) failed++
  console.log(`${ok ? "ok  " : "FAIL"}  ${k.padEnd(20)} -> ${v} (esperado ${expected[k]})`)
}
console.log(`mode=${mode} setup=${setupMs}ms fail=${failed}`)

if (typeof dispose === "function") dispose()
process.exit(failed ? 1 : 0)
