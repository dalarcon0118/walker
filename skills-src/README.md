# Skills empaquetados de walker

Skills que el propio walker necesita para que su gate de `VERIFY.QA`
(`skill_intent.policy: "required"`) tenga candidatos reales. Sin ellos, el gate
pasaba como "sin candidatos" y el único match era `frontend-design` (score 2,
por la palabra "quality").

Cada directorio es un paquete `kind: skill`:

```
<id>/
  hook.json    # id, kind, version, walker, requires
  SKILL.md     # contenido
```

Se instalan una vez (global, `~/.walker/skills`):

```bash
for s in code-review-checklist testing-patterns clean-code; do
  python3 scripts/hooks_cli.py install "skills-src/$s"
done
```

Verificación: `python3 scripts/skills_gate_report.py` debe mostrar
`VERIFY.QA → bloquearía_sin_carga` con `code-review-checklist` primero.

## Origen

Contenido copiado de `game_reset/.agents/skills/<id>/` (skills propios del
usuario, ya en uso en ese repo). `testing-patterns` trae su `scripts/` porque el
skill lo referencia. Si alguno de estos skills viene de un marketplace, revisá
su licencia antes de redistribuirlo: acá se versiona para que walker sea
reproducible, no como canon.


## Vendored: ponytail / ponytail-review

- Origen: https://github.com/DietrichGebert/ponytail (MIT, ver
  `ponytail/LICENSE`).
- `ponytail`: minimalismo (YAGNI ladder). Medido upstream: −15% LOC y −10,3%
  costo en mediana, sin cambio de calidad. **No se auto-activa como skill
  común** (0/10 sesiones en el test de JetBrains): si se quiere el efecto, va
  como `policy: required` en un nodo o inyectado por el plugin.
- `ponytail-review`: review enfocado en sobre-ingeniería. Se declara como
  candidato en `REFINE.DIAGNOSE` y aparece en `VERIFY.QA` por el tag `review`.
- Tags agregados localmente al frontmatter para que el índice los encuentre
  (upstream no los trae).

## walker-intake (router de sesión)

No es candidato de gate: es el router que usa el agente **al empezar cada
pedido**. Verifica proyecto y estado (`project_for_path`, `status`,
`session_bind`), investiga con memorias antes que código (`brief`,
`search_memories`), clasifica impacto y decide entre fix directo o sprint con
workflow.

```bash
# OpenCode lo lee nativo (recomendado para este skill):
cp -R skills-src/walker-intake ~/.config/opencode/skills/

# Alternativa: instalador de walker (queda en ~/.walker/skills; OpenCode solo lo
# ve si `~/.walker/skills` está en el array `skills` de opencode.json).
python3 scripts/hooks_cli.py install skills-src/walker-intake
```

El índice de walker escanea `~/.config/opencode/skills` (scope `global`) y
`~/.walker/skills` (scope `walker`), así que la copia nativa sirve a ambos. No
mantengas las dos: si coexisten, la entrada de `skills` config gana en OpenCode y
la copia nativa queda shadowed.

`session-intake` cubre el subconjunto arranque/cierre; `walker-intake` lo
reemplaza cuando el pedido exige decidir impacto y ruta.

