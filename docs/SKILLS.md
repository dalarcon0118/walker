# Skills en el workflow

Un skill es un documento de instrucciones (`SKILL.md`) que el agente puede
aplicar. **No es un hook**: un hook es un ejecutable que corre en un evento; un
skill es contexto que se carga en el prompt.

## El problema de fondo

El workflow no conoce los skills que el agente tiene instalados. Si un nodo
declara `skills: ["frontend-design"]`, el workflow queda acoplado a la máquina
de turno: funciona en una y se rompe en otra, en otro proyecto, o cuando el
agente corre sin esas skills.

La solución invierte quién sabe qué:

| Quién | Sabe |
|---|---|
| **Nodo del workflow** | qué **necesita** la fase (`needs` + `tags`) |
| **Walker** | qué skills **existen** en el entorno (los descubre) |
| **Agente** | cuál aplica, y lo **carga** con `load_skill` |

## Declaración en el nodo

```json
// data/workflow/nodes/implement.node.json → sub_state STATE_CONSTRUCTION
"skill_intent": {
  "needs": "criterio de diseño, calidad de código y arquitectura antes de comprometer el plan",
  "tags": ["design", "clean-code", "architecture"],
  "policy": "advisory"
}
```

No hay ids de skills. `policy` queda reservado para `required` (el gate exige
carga) cuando se implemente la verificación; hoy es advisory.

## Descubrimiento

`shared_kernel/skills` escanea, en orden de precedencia (el primero gana para el
mismo nombre):

1. `<repo>/.agents/skills/` — del proyecto, viaja con el repo
2. `<repo>/.opencode/skills/` — capa OpenCode del proyecto
3. `~/.config/opencode/skills/` — global del usuario
4. `<walker>/skills/` — del propio walker

Formato: `<skill>/SKILL.md` (o `SKILL.MD`) con frontmatter plano:

```markdown
---
name: frontend-design
description: Create distinctive, production-grade frontend interfaces...
tags: [ui, design]
---
cuerpo con las instrucciones
```

## Qué llega al prompt

Solo el **catálogo** (una línea por skill), nunca el contenido completo:

```
📚 SKILLS PARA ESTA FASE (descubiertos en tu entorno)
- `frontend-design` — Create distinctive, production-grade frontend interfaces...
- `ui-ux-pro-max` — UI/UX design intelligence for web, mobile, and desktop...

Si alguna aplica, cargala con `load_skill(<nombre>)` antes de producir la salida.
Si ninguna encaja, resolvé con criterio propio y decilo explícitamente.
```

Presupuesto: 6 skills máximo y 900 chars de catálogo (medido: inline cinco
skills completos rompe el `context.budget` del sub-state y distrae al modelo).

El matching es determinista: tags del frontmatter + palabras de `needs`, con
sinónimos español→inglés (`diseño`→`design`, `código`→`code`, `revisión`→
`review`) porque el workflow está en español y casi todos los skills en inglés.
Sin ese puente, `criterio de diseño` no matchea `frontend design` y el catálogo
sale vacío siempre.

## Herramientas

| Tool | Qué hace |
|---|---|
| `search_skills(project_id, query?, tags?, limit?)` | busca por intención; devuelve nombre, descripción, scope y path |
| `load_skill(project_id, name)` | devuelve el `SKILL.md` (cuerpo, cap 8 000 chars) |

`load_skill` con un nombre inexistente devuelve `error` + `available`, para que
el agente corrija sin adivinar.

## Si no hay match

El prompt lo dice explícitamente:

```
Sin skills locales que matcheen esta fase; resolvé con criterio propio y decilo en la salida.
```

Un silencio se lee como "aplicá cualquier cosa" o invita a que el agente invente
un skill que usó. El no-match es información, no un fallo.

## Límites (lo que NO hay todavía)

- **Gate de skills**: `required_tools` hoy valida `kg_query`; extenderlo a
  `load_skill` requiere la regla anti-deadlock ("sin candidatos → satisfecho").
- **Skills como paquetes**: `install` maneja `kind: hook`; `kind: skill` no
  existe todavía, así que un skill se distribuye copiando la carpeta.
- **Matching semántico**: es léxico (tokens + sinónimos). Si el agente reporta
  "ninguna aplica" seguido, ahí conviene medir y recién después evaluar
  embeddings.

## Probar

```bash
# renderiza el prompt del nodo con el catálogo real de este entorno
python3 - <<'PY'
from fast_mcp.features.workflow.application.prompts.manager import PromptManager
m = PromptManager(".")
r = m.resolve_for_sub_state("implement", "STATE_CONSTRUCTION", "llm_task", fields={
    "prompt_id": "implement/state_construction",
    "skill_intent": {"needs": "criterio de diseño y arquitectura",
                     "tags": ["design", "architecture"]}})
print(r["template"][-800:])
PY
```
