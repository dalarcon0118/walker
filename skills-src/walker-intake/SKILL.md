---
name: walker-intake
description: "Router de intake de walker: verifica proyecto y estado, investiga con memorias antes que código, clasifica impacto y decide fix directo o sprint con workflow."
allowed-tools: Bash, Read, Grep, Glob, Edit, Write
version: 1.0.2
priority: HIGH
tags: [walker, intake, triage, workflow, project, memories, impact]
when_to_use: >
  Al inicio de cada pedido del usuario, cuando hay que decidir si el trabajo es
  un fix local o una tarea con sprint, o cuando no está claro en qué punto del
  workflow está la sesión.
---

# Walker intake — del mensaje a la tarea verificada

Este skill es un **router**, no un manual: decide en qué momento estás y usa
solo la capa de walker que corresponde. El nodo (lo que devuelve `brief`)
**manda sobre este texto**.

Regla de oro: memorias antes que código; sin estrategia no se edita; sin
`run_tests`/`verify_predicates` verdes no hay cobertura.

## Dos carriles

El MCP corre con `WALKER_MCP_PROFILE=agent`: `brief`, `search_memories`,
`submit`, `advance`, `transition`, `implement_task`, `run_tests`, `capture`.
Con Bash, el CLI agrega `project_for_path`, `status`, `session_bind`,
`verify_predicates` y `documenting_knowledge`: usalo para el intake, porque esas
tools no están en el perfil `agent`. Sin Bash, hacé el intake con
`brief {"text": "<mensaje>"}` y declarí qué no pudiste verificar.

En OpenCode, `<ses_id>` es `$OPENCODE_SESSION_ID`.

## Fase 0 · ¿Este path es un proyecto de walker y en qué estado está?

```bash
walker3 _/project_for_path '{"path":"<cwd>"}'
```

- `ok: true` → ese es el `<pid>`. `ok: false` → **no lo inventes**: decí que no
  está registrado y ofrecé `walker3 _/prepare '{"path":"<cwd>"}'` (lo corre una
  persona, no el agente).

```bash
walker3 <pid>/status '{"session_id":"<ses_id>"}'
```

Leé `warnings`, `watcher.stale`, `sprint` (goal, status, `specs_count`) y
`last_session`. `pending` o `sprint: null` son estado real: se reporta.

Si `status` o `brief.gates.scope` traen archivos de otra tarea u otro repo, el
plan está viejo: corregilo con `session_plan` (`add`/`remove`) antes de
clasificar.

```bash
walker3 <pid>/session_bind '{"session_id":"<ses_id>","mode":"auto"}'
```

- `resume_available` → preguntá al usuario si continúa el sprint anterior o
  empieza uno nuevo. No decidas por él.
- `bound`/`continued` → seguí **solo si el `goal` cubre el pedido**; si es otro
  trabajo u otro repo, proponé `mode=new` con el pedido como objetivo.
- `mode=new` exige `objective`: usá el pedido real del usuario, no un resumen.

Sin sprint no hay trabajo de código: se crea uno con `session_bind mode=new`.

## Fase 1 · ¿Qué pide el mensaje, según la memoria?

```bash
walker3 <pid>/brief '{"session_id":"<ses_id>","text":"<mensaje>"}'
```

Devuelve el paso (`node`/`sub_state`), decisiones que aplican (con estado y
`supersedes`), impacto (callers, `risk`), gates y skills candidatos. Si trae
`missing` o `degraded` no vacíos, decilo y completá el ancla (archivo o
`task_id`) antes de clasificar.

`brief` con solo `text` suele volver `degraded: true` e `impact.files: []`
(medido con `AGENTS.md`). Repetilo con `file` o `task_id`: el ancla concreta
deja `missing: []` y `degraded: false`. Si con `file`+`task_id` el `missing` es
de decisiones, probá solo `file`: con `task_id` la query es el objetivo y puede
no matchear el CKG.

```bash
walker3 <pid>/search_memories '{"ckg":{"query":"<tema>"},"cpg":{"query":"<simbolo>"}}'
walker3 <pid>/decisions_view '{"file":"<archivo>"}'
```

`decisions_view` matchea el archivo exacto; `brief` amplía por área y puede
devolver `task-ledger` (no es una decisión). Reportá lo que aplica y descartá lo
que no. En `search_memories`, un `metadata.status: superseded` no es una
decisión vigente.

Recién después, código con `code_context` / `code_search`; `grep`/`read` solo
para verificar líneas exactas.

**Cortá acá si no podés decir**: cuál es el problema, qué área toca y qué
decisión previa aplica. Sin eso no hay estrategia, y sin estrategia no se edita.

## Fase 2 · ¿Hay estrategia de solución?

- Si no hay estrategia: analizá o preguntá. Si el nodo es `ANALYZE`/`DESIGN`,
  completá sus campos (`q1..q3`, precondiciones, `design_brief`) en vez de
  improvisar código.
- Si hay estrategia: pasá a clasificar impacto. No la implementes todavía.

## Fase 3 · Clasificación por impacto (evidencia, no corazonada)

Usá `brief.impact` (callers y `risk`), la cantidad de archivos y **el tipo de
ancla**. `callers: 0` no significa “impacto bajo”: en un documento significa
“no es código”. La rúbrica es heurística; ante duda, preguntá.

| Ancla | Señal | Ruta |
|---|---|---|
| Código | ≤ 2 archivos, `risk: bajo`, sin contrato | Tarea directa en el sprint activo: `implement_task` → `run_tests` → `verify_predicates` |
| Código | ≥ 3 archivos, o `risk: medio\|alto`, o contrato de producto (`DOMAIN.md`, ADR, wire, migración) | Sprint + workflow: `session_bind`/`sprint_planning`, `implement_task`, y los nodos ANALYZE/DESIGN si el brief los pide |
| Doc/config operativa (`AGENTS.md`, `README`, `project.md`) | 1 archivo, sin cambio de contrato de producto | Tarea directa de documentación (misma verificación) |
| Cualquiera | `brief` degradado, plan con alcance ajeno o pedido ambiguo | No clasificar: completar contexto o preguntar |

“Contrato” es el de producto/código (dominio, wire, migraciones, ADR), no la
documentación operativa del agente. Y un sprint activo no es un cajón: si su
`goal` no cubre el pedido, el trabajo va a un sprint nuevo.

**Antes de `implement_task`, fijá el plan** con `session_plan mode=set`
(goal/acceptance/task/scope): `implement_task` hereda la aceptación del plan
cuando el objetivo no deriva criterios, y un plan viejo produce una spec con los
criterios de la tarea anterior (medido: SPEC-002 nació con los de SPEC-001).

Un typo no justifica un sprint; un cambio de contrato no se hace sin workflow.

## Fase 4 · Ejecutar con el nodo como autoridad

```bash
walker3 <pid>/implement_task '{"task_id":"<slug>","session_id":"<ses_id>","objective":"<pedido literal>","files":["<archivo>"]}'
walker3 <pid>/brief '{"task_id":"<slug>","session_id":"<ses_id>"}'
```

`implement_task` deriva criterios y predicados: revisá `spec.validation` y los
`unmapped` antes de seguir. Si la spec ya nació con criterios ajenos, reejecutá
`implement_task` con `acceptance: [{criterion, predicate}]`: actualiza la misma
spec (mismo `task_id`) y evita `unmapped`. Luego:

- `step.next_action` es la orden: trae `tool` y `gates`. En `SPRINT.ACTIVE` el
  siguiente paso es `transition` con uno de esos gates (`start_task`,
  `back_to_start`, `edit_plan`, …); no inventes el gate.
- `advance {"fields":{...}}` para sub-estados `auto`/`llm_task`; `submit` cuando
  el nodo pide datos; `transition {"gate":"...","reason":"..."}` solo si el nodo
  lo indica.
- No saltees gates: `ANALYZE`/`DESIGN` antes de `IMPLEMENT` si el brief los pide.

Verificación:

```bash
walker3 <pid>/run_tests '{"task_id":"<slug>","session_id":"<ses_id>"}'
walker3 <pid>/verify_predicates '{"task_id":"<slug>","session_id":"<ses_id>"}'
```

La autoridad es `returncode: 0` (con spago, `passed=0/total=0` es normal porque
no imprime “N passed”). Un `unmapped` o un predicado `heuristic` no prueban el
comportamiento nuevo: decilo.

## Fase 5 · Cerrar con evidencia y conocimiento

```bash
walker3 <pid>/capture '{"task_id":"<slug>","evidence":"<qué se probó>","artifacts":["<archivo>"]}'
walker3 <pid>/documenting_knowledge '{"session_id":"<ses_id>","task_id":"<slug>","nodes":[{"type":"decision","label":"<qué se decidió>","description":"<por qué>"}]}'
walker3 <pid>/session_context '{"session_id":"<ses_id>"}'
```

`empty_layers` vacío; una capa vacía es un hueco real y se reporta.
`spec_status completed` exige `commit_hash`, `review_verdict` y `evidence`: sin
commit no se cierra “en verde”, se dice que falta.

## Anti-patrones (rompen la sesión)

- `grep`/`read` antes de `brief`/`search_memories`: se investiga con memoria
  primero; el código confirma.
- Inventar proyecto, criterios o predicados. Si falta, va a `unmapped` y se
  reporta.
- Crear sprint para un cambio trivial, o hacer un cambio de contrato sin sprint.
- Pasar `project_id` en el payload: el prefijo `<pid>/` es el SSOT (el CLI lo
  descarta).
- Declarar la tarea terminada con `run_tests` en rojo, `missing` no vacío o
  `quality_report.degraded`.
- Presentar un brief parcial como completo: `missing`/`degraded` son contrato.
