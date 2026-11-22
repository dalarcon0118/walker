// tests/test_shell_effect.test.mjs — ADR-084 F3a: clasificación por efecto.
//
// El intake bloqueaba todo `shell` que no fuera `walker3` exacto: observar
// (grep, pwd, git status, --help) pagaba el peaje de una escritura. Un gate que
// clasifica por nombre de tool produce ese falso positivo por construcción.
//
//   node --test tests/test_shell_effect.test.mjs

import assert from "node:assert/strict"
import { test } from "node:test"

import { shellEffect } from "../agent-plugins/walker-flight-control/shell_mutation.mjs"

const PROJECT = "/proj"

const expectEffect = (cmd, effect, projectDir = PROJECT) => {
  const got = shellEffect(cmd, projectDir)
  assert.equal(got.effect, effect, `${cmd} → ${got.effect} (esperado ${effect})`)
}

test("read: observar nunca se bloquea", () => {
  for (const cmd of [
    "pwd",
    "ls -la",
    "rg flight_control",
    "grep -rn walker .",
    "git status",
    "git diff --stat",
    "git log --oneline -5",
    "cat README.md",
    "cd agent-plugins && git status",
    "pytest --help",
    "echo probe",
  ]) {
    expectEffect(cmd, "read")
  }
})

test("protocol: el remedio es alcanzable en todas sus formas", () => {
  for (const cmd of [
    "./bin/walker3 walker/session_bind '{\"mode\":\"auto\"}'",
    "/Users/x/walker/bin/walker3 walker/session_bind '{\"mode\":\"auto\"}'",
    "cd /proj && walker3 walker/session_plan '{}'",
    "timeout 5 walker3 walker/submit '{}'",
    "walker3 walker/session_bind '{}' 2>&1",
    "walker3 walker/brief '{\"file\":\"a.py\"}' | tail -20",
    "walker3 walker/workflow_state '{}' | jq .current_position",
  ]) {
    expectEffect(cmd, "protocol")
  }
})

test("verify: correr la suite no muta el proyecto", () => {
  for (const cmd of [
    "pytest -q",
    "python3 -m pytest tests/test_x.py -q",
    "go test ./...",
    "walker3 walker/run_tests '{\"task_id\":\"T\"}'",
    "walker3 walker/verify_predicates '{}'",
  ]) {
    expectEffect(cmd, "verify")
  }
})

test("write: se detecta por destino dentro del proyecto", () => {
  for (const cmd of [
    "echo x > a.py",
    "printf x >> a.py",
    "rm a.py",
    "sed -i 's/a/b/' a.py",
    "cp x a.py",
    "git checkout main",
    "git reset --hard",
  ]) {
    expectEffect(cmd, "write")
  }
})

test("unknown no se degrada a read", () => {
  for (const cmd of [
    "python3 -c 'open(\"a.py\",\"w\")'",
    "make",
    "npm install",
    "walker3 walker/brief '{}' | sh",
    "cat a.py | xargs rm",
  ]) {
    expectEffect(cmd, "unknown")
  }
})

test("redirigir fuera del proyecto no lo convierte en write", () => {
  expectEffect("echo x > /tmp/log", "read")
  // Sigue siendo verificación: la redirección oculta la salida (FC-INV-12),
  // pero no muta el proyecto.
  expectEffect("pytest -q > /tmp/pytest.log", "verify", PROJECT)
})
