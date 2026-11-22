---
alwaysApply: true
scene: git_message
---

# Reglas de commit de Walker

## Formato

```
<tipo>(<scope>): <resumen>

<cuerpo opcional>

<footer opcional>
```

## Tipo

Usar Conventional Commits en minúsculas:

- `feat`: funcionalidad nueva.
- `fix`: corrección de bug.
- `refactor`: cambio interno sin cambiar comportamiento.
- `test`: tests nuevos o ajustados.
- `docs`: documentación.
- `perf`: mejora de rendimiento.
- `build`: build, dependencias, tooling.
- `ci`: pipelines.
- `chore`: mantenimiento sin impacto funcional.
- `revert`: reversión.

## Scope

- Un solo scope, en minúsculas, estable y del dominio:
  `workflow`, `plugin`, `harness`, `memoria`, `delta`, `ckg`, `cpg`,
  `intake`, `nudge`, `sello`, `captura`, `session`, `captura`, `docs`, `agent`.
- Si el cambio cruza varios dominios, usar el dominante o `core`.
- No inventar scopes nuevos si ya existe uno para el área.

## Resumen

- En español, imperativo, minúscula inicial, sin punto final.
- Máximo 72 caracteres.
- Describir el efecto, no el archivo:
  - bien: `fix(nudge): la redireccion tambien oculta el resultado`
  - mal: `fix(index.js): cambio en index.js`
- Si aplica, incluir el identificador al final:
  `feat(workflow): implementar bucle metacognitivo (ADR-WALKER-084)`

## Cuerpo

- Opcional, pero recomendado si el cambio no es trivial.
- Explicar **por qué**, no repetir el diff.
- Usar viñetas y frases cortas.
- Mencionar tradeoffs, invariantes y migraciones.
- Referenciar ADR/SPEC/INV cuando corresponda:
  - `ADR-WALKER-084`
  - `SPEC-027`
  - `INV-016`
- No incluir “generado por IA”, ni firmas de modelo, ni relleno.

## Footer

- `Refs: SPEC-027, ADR-WALKER-084`
- `BREAKING CHANGE: <qué se rompe y cómo migrar>`

## Reglas de higiene

1. Un commit = un cambio coherente. Si hay dos concerns, separar.
2. No commitear secretos, `.env`, credenciales ni datos de sesión.
3. No commitear archivos generados salvo que sean SSOT explícito.
4. Si el commit toca tests, el mensaje debe decir qué verifica.
5. Si el commit es de cierre de sprint, referenciar el sprint y las specs.

## Ejemplos

```
feat(workflow): implementar bucle metacognitivo (ADR-WALKER-084)
fix(nudge): la redireccion tambien oculta el resultado de la verificacion
test(evidencia): el writer real del CKG inyecta la sesion (SPEC-021)
refactor(cpg): separar estructura de semantica en el review
docs(agent): alinear la superficie de 8 tools con AE-INV-01
```
