// node --test contract.test.mjs
import assert from "node:assert/strict"
import { test } from "node:test"

import { budgetDeny, budgetUsage, contractBlock } from "./contract.mjs"

const withBudget = (max_files, max_loc = 200) =>
  contractBlock({
    present: true,
    spec_id: "SPEC-006",
    goal: "g",
    budget: { max_files, max_loc },
    criteria: { mapped: 2, unmapped: 1 },
  })

test("sin contrato no hay budget inventado", () => {
  const c = contractBlock(null)
  assert.equal(c.present, false)
  assert.equal(c.budget.max_files, null)
  assert.equal(budgetUsage(c, ["a"]).exceeded, false)
  assert.equal(budgetDeny(c, ["a"], "b"), "")
})

test("contrato normaliza budget y criterios", () => {
  const c = withBudget(3, 120)
  assert.equal(c.present, true)
  assert.equal(c.budget.max_files, 3)
  assert.equal(c.budget.max_loc, 120)
  assert.deepEqual(c.criteria, { mapped: 2, unmapped: 1 })
})

test("uso del budget cuenta archivos únicos", () => {
  const u = budgetUsage(withBudget(3), ["a", "b", "b"])
  assert.equal(u.used_files, 2)
  assert.equal(u.remaining, 1)
  assert.equal(u.exceeded, false)
})

test("no bloquea mientras quede lugar ni al reescribir un archivo conocido", () => {
  const c = withBudget(2)
  assert.equal(budgetDeny(c, ["a"], "b"), "")
  assert.equal(budgetDeny(c, ["a", "b"], "a"), "")
})

test("bloquea el archivo que excede y trae el comando de remedio", () => {
  const c = withBudget(2)
  const deny = budgetDeny(c, ["a", "b"], "c", { cli: "/repo/bin/walker3", project: "walker" })
  assert.match(deny, /2\/2 archivos/)
  assert.match(deny, /capture/)
  assert.match(deny, /max_files":3/)
})

test("budget excedido se declara aunque el uso venga de walker", () => {
  const u = budgetUsage(withBudget(1), ["a", "b"])
  assert.equal(u.exceeded, true)
  assert.equal(u.remaining, 0)
})
