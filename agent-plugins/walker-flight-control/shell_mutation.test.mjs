// Tests del clasificador de mutaciones (sin OpenCode).
//   node --test shell_mutation.test.mjs
//
// Foco: el DESTINO decide. Copiar la sqlite del motor a /tmp no es escribir
// el proyecto (medido 2026-11-20: disparaba el nudge de cierre).

import assert from "node:assert/strict"
import test from "node:test"

import { shellEffect, shellMutation } from "./shell_mutation.mjs"

const PROJECT = "/Users/davidmartinez/develop/Active-projects/walker"

const CASES = [
  // [comando, muta?]
  ["cp .cognitive/projects/walker/data/cognitive.sqlite3 /tmp/cog.sqlite3", false],
  ["cp a.py b.py", true],
  ["cp /etc/hosts docs/x.md", true],
  ["mv build /tmp/build", false],
  ["mv /tmp/x.py src/x.py", true],
  ["rm -rf /tmp/scratch", false],
  ["rm -rf build/", true],
  ["rm a.py /tmp/b.txt", true],
  ["touch src/nuevo.py", true],
  ["mkdir /tmp/walker-x", false],
  ["install -m 644 a.sh /usr/local/bin/x", false],
  ["install -m 644 a.sh scripts/x", true],
  ["dd if=/dev/zero of=/tmp/x bs=1 count=1", true],
  ['git add . && git commit -q -m "mensaje con > y ; dentro"', false],
  ["git status --porcelain", false],
  ["echo hola > /tmp/log", false],
  ["echo hola > out.txt", true],
  ["cat x.py > /tmp/y.py", false],
  ["sed -i 's/a/b/' src/x.py", true],
  ['python3 -c "open(\'a\',\'w\')"', false],
  ["cd /tmp && make", false],
  ["cp -r docs /tmp/docs-copia", false],
]

for (const [command, expected] of CASES) {
  test(`${expected ? "MUTA" : "no muta"}: ${command}`, () => {
    assert.equal(shellMutation(command, PROJECT).mutates, expected)
  })
}

test("el target es el destino dentro del proyecto", () => {
  const result = shellMutation("cp /tmp/x.py src/x.py", PROJECT)
  assert.equal(result.mutates, true)
  assert.equal(result.target, "src/x.py")
})

test("sin proyecto resoluble es conservador", () => {
  assert.equal(shellMutation("cp /tmp/a /tmp/b", "").mutates, true)
})

test("duplicación de fd y /dev/null no cuentan", () => {
  assert.equal(shellMutation("pytest -q > /dev/null 2>&1", PROJECT).mutates, false)
})

test("grep con | escapado entre comillas es read, no un pipe", () => {
  assert.equal(
    shellEffect('grep -rl "mirror_acted_on\\|brief_injected" scripts', PROJECT).effect,
    "read"
  )
})

test("grep con alternancia dentro de comillas simples es read", () => {
  assert.equal(shellEffect("grep -E 'a|b' scripts", PROJECT).effect, "read")
})

test("un pipe real a un filtro read-only sigue siendo read", () => {
  assert.equal(shellEffect("cat x.py | head -5", PROJECT).effect, "read")
})

test("un pipe real a un shell sigue siendo unknown", () => {
  assert.equal(shellEffect("cat x.py | sh", PROJECT).effect, "unknown")
})

test("|| no se confunde con un pipe a comando desconocido", () => {
  assert.equal(shellEffect("git status || git diff", PROJECT).effect, "read")
})
