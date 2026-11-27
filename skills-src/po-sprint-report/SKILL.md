---
name: po-sprint-report
description: "Informe de sprint para el PO: resumen en lenguaje llano, métricas duras (specs, commits, tests, predicates) y deuda declarada, antes de la retro y del cierre."
allowed-tools: Bash, Read, Grep, Glob
version: 1.0.0
priority: HIGH
tags: [sprint, report, metrics, po, summary, close, review]
when_to_use: >
  En SPRINT.REPORT, después de que el PO aceptó el review del sprint y antes de
  la retrospectiva. También cuando alguien pide "resumen del sprint" o "métricas
  del sprint".
---

# Informe de sprint para el PO

El informe se **presenta**, no se declara. Si un número no lo mediste, no va.

## De dónde salen los números

- Specs y su estado: `walker3 <pid>/status '{"session_id":"<ses>"}'` (sprint y specs)
  o `plan_diff '{"task_id":"<spec>"}'` para el detalle por spec.
- Commits y diff: `git log --oneline <primer>^..<ultimo>` y
  `git diff --stat <primer>^ <ultimo>`.
- Tests: el `run_tests` de cada spec y el total actual. El baseline del sprint
  se toma al planificar: el gate es **no agregar FAIL/ERROR**, no "todo verde".
- Predicates: `verify_predicates` de cada spec y el `WALKER_RESULT`
  (`passed=N failed=0`). Un `weak: true` no es evidencia.
- Hallazgos: el summary de `review_code` de cada spec (archivos y hallazgos).

## Reglas

1. **Nada de verdes falsos.** Un gate se cita con el test o el query que lo
   demuestra. "Debería funcionar" no es evidencia.
2. **La deuda se declara con su estado** (abierta / mitigada / aceptada). No se
   esconde ni se maquilla.
3. **Lenguaje del PO**: qué se entrega y por qué importa. El detalle técnico va
   después, no antes.
4. **Sin métricas inventadas.** Si falta un dato, se dice "no medido" y por qué.

## Forma del informe

- **Resumen** (3-6 líneas): qué cambió para el usuario y por qué importa.
- **Métricas** (objeto): specs completed/total, commits, tests antes→después,
  predicates, hallazgos, presupuesto de queries si aplica.
- **Riesgos/deuda** (lista): cada ítem con estado y próximo dueño.
- **Próximos pasos** (lista): concretos y ordenados.

## Salida

```bash
walker3 <pid>/submit '{"summary":"...","metrics":{...},"risks":[...],"next":[...]}'
```

Después: `transition report_delivered` → RETRO. El cierre no se hace desde acá.
