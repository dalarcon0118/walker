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
