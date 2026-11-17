# Harness de evaluación (runner)

Corre tareas reales contra OpenCode y las gradúa con la suite del repo.
No es un framework: son ~250 líneas de stdlib que lanzan el agente, aíslan el
árbol de trabajo y deciden si pasó.

**Dónde vive cada cosa:** el runner acá (`walker/harness/`), porque walker es el
plano de control; las **tareas** viven en cada producto (`<repo>/eval/tasks.json`),
porque referencian commits, tests y prompts de ese repo.

## Qué mide

| Columna | Significado |
|---|---|
`pass` | 1 si el agente terminó **y** los tests pasan |
`agent_rc` | exit code de `opencode run` |
`test_rc` | exit code de `manage.py test` |
`duration_s` | agente + tests |
`tokens_in`, `tokens_out`, `cost_usd` | leídos de la sesión en `opencode.db` (best-effort) |

## Cómo agregar una tarea (en el producto)

La tarea tiene que ser **anterior al fix**, si no el agente ve el código ya
arreglado y el test pasa solo. Por eso se declara el commit del fix y el runner
usa su padre:

```json
{
  "id": "mi-bug",
  "fix_commit": "319bfed",
  "test_from": "978453c",
  "test_files": ["tests/incident/test_writes.py"],
  "test": "tests.incident.test_writes",
  "prompt": "Síntoma observable, sin nombrar la causa ni el fix. No modifiques los tests."
}
```

- `fix_commit` define el base (su padre).
- `test_from`/`test_files` inyectan el grader cuando el test no existía en el base.
- El prompt no debe filtrar la solución: describe el síntoma como lo reportaría
  un usuario.
- El `test` es el grader; si ya fallaba antes del fix, el baseline está mal.

## Correr

```bash
# desde el repo del producto
python ../walker/harness/runner.py \
  --repo . --tasks eval/tasks.json --dry-run

# una tarea, una vez
python ../walker/harness/runner.py --repo . --tasks eval/tasks.json --filter incident

# baseline completo (2 corridas por tarea por el ruido del ~13%)
python ../walker/harness/runner.py --repo . --tasks eval/tasks.json --k 2 --no-mcp

# publicar cada resultado en el TraceGraph de walker (por API MCP, sin tocar archivos)
python ../walker/harness/runner.py --repo . --tasks eval/tasks.json \
  --jobs 4 --no-mcp --walker-project backend-microservices

# publicar además el resumen de la sesión como conocimiento del proyecto
python ../walker/harness/runner.py --repo . --tasks eval/tasks.json \
  --summary ~/.cognitive/chat-summaries/summary-mi-sesion-20261117.md
```

**Detección de proyecto.** Sin `--walker-project`, el runner resuelve el
proyecto desde `--repo` con `ProjectService.resolve_from_registry` (manifest del
layout cognitivo o registro global por `repo_path`) y lo imprime. Si no hay
proyecto registrado, la corrida sigue pero no publica nada, avisando el motivo.
`--walker-project` sigue existiendo como override.

**Qué se publica.** Tres destinos distintos, que antes se confundían:

| Destino | Tool | Qué guarda |
|---|---|---|
TraceGraph | `trace_push` (`eval.result`) | historial completo, un evento por corrida |
Workflow | `capture` + `submit` | evidencia y estado del sprint |
CKG | `documenting_knowledge` | **último** resultado por tarea (`eval-<task>-k<k>`, `eval-suite-latest`) y la sesión (`--summary`) |

Los ids de conocimiento son determinísticos: re-correr actualiza el nodo en vez
de duplicarlo (el historial vive en trace). `--summary` publica un nodo por
decisión (`design-decision`), uno de estado de la sesión
(`implementation_state`) y uno de próximos pasos.

La ingesta usa `harness/walker_client.py`: levanta el MCP de walker por stdio,
hace `initialize` y una llamada por resultado (`event_type: "eval.result"`). Si
la ingesta falla, la corrida no se rompe: se avisa por stderr y el CSV queda
igual. Sin filas (por ejemplo, todas las tareas deshabilitadas) `--summary`
igual se publica.

`--keepdb` se pasa al grader automáticamente: la base de tests se reutiliza entre
corridas dentro del sandbox. Los prompts de las tareas piden correr solo el test
del módulo afectado: es la diferencia entre 8 corridas de suite y 2.

Resultados en `~/.local/share/walker/harness-runs/<timestamp>/` (`--out` para
cambiarlo): `results.csv`, un `.jsonl` por corrida, `.diff`, `.status` y el
resumen de la suite. Los árboles de trabajo viven en `$TMPDIR` y se borran salvo
`--keep`.

## Benchmark no saturado + ablación

Con la suite pasando 8/8, cualquier mejora es invisible. Para tener un número
que se pueda mover:

```bash
# 1. proponer tareas desde el historial (commits que tocan test + fuente)
python3 scripts/derive_eval_tasks.py --repo <repo> --since 2026-08-01 \
  --limit 40 --out /tmp/tasks.json

# 2. validarlas: el test TIENE que fallar en el base
python3 harness/runner.py --repo <repo> --tasks /tmp/tasks.json --check-tasks

# 3. ablación: mismo benchmark, una capa a la vez
python3 scripts/ablation.py --repo <repo> --tasks /tmp/tasks.json --k 3
```

La ablación corre tres brazos:

| brazo | MCP de walker | inyección de CKG/skills |
|---|---|---|
| `baseline` | no | no |
| `tools` | sí (el agente puede llamarlas) | no |
| `full` | sí | sí |

Toggles para experimentos propios: `WALKER_PROMPT_KNOWLEDGE=0`,
`WALKER_PROMPT_SKILLS=0`, `WALKER_FLIGHT_CONTROL=0` (los evals ya corren con
este último apagado).

Derivación y validación medidas (2026-11-17), backend:

```
40 candidatas (de 333 commits)
  → 29 válidas   (fallan en base Y pasan en fix)
  →  4 descartadas (el test PASA en base: no mide nada)
  →  7 descartadas (el test no importa en base: falta archivo/fixture)
```

Snapshot usable: `harness/benchmarks/backend-derived-validated.json` (29 tareas,
referencian commits de backend-microservices).

Dos lecciones del proceso:

- `--check-tasks` verifica **base y fix**: si el test no pasa en el commit del
  arreglo, la tarea no sirve (el test está roto o depende de otra cosa). La
  primera corrida dio 0/40 porque el worktree del fix no tenía `.env`
  (untracked): Django no arrancaba. Ahora se copia `.env` igual que en el base.
- El prompt NO se deriva del subject del commit: "expone bono_kind y
  count_scope en reward_rule" **es la solución**. El prompt usa el test que
  falla ("El test `X` falla en este checkout…"), que es el síntoma.

## Validar tareas antes de gastar tokens

```bash
python ../walker/harness/runner.py --repo . --tasks eval/tasks.json --check-tasks
```

Materializa el base, inyecta el test si corresponde y corre la suite **sin llamar
al agente**. Una tarea sirve solo si el resultado es `bug presente`:

| Salida | Significado |
|---|---|
`bug presente` | válida: los tests fallan en el base |
`PASA en base: tarea inválida` | el bug no está, o el test ya lo cubre |
`test no importa en base` | falta el archivo o un fixture: elegí otro test o inyectalo |

## Aislamiento (por qué NO se usa `git worktree`)

El primer intento usó `git worktree`, y el resultado fue un falso PASS. Dos fugas,
medidas:

1. **El worktree comparte el historial del repo.** El agente corrió
   `git log --oneline -- incident/views.py`, encontró el commit del fix y aplicó
   literalmente ese cambio (`initialize_request` en vez de `dispatch`).
2. **OpenCode resolvió la sesión al checkout canónico.** El proceso se lanzaba
   con `cwd` en el worktree, pero `subprocess` **no actualiza la variable `PWD`**:
   OpenCode resuelve la ubicación con `PWD`, así que la sesión apuntaba al repo
   principal, con el código ya arreglado y hasta logs de corridas anteriores.
   El runner ahora setea `PWD=<cwd>` en cada subproceso (`run()`), y eso se
   verificó mirando `session_v2.directory` en la DB.

Ahora cada tarea se materializa con `git archive <base> | tar -x` en un
directorio fuera del repo, con `git init` y **un solo commit** (`base`). El test
se inyecta antes de ese commit, así `git diff` muestra únicamente lo que hizo el
agente. `--no-mcp` desactiva los MCP por config, porque sus índices (walker,
CGC, graphify) apuntan al repo real.

Fuga que queda: el binario `walker3` está en el `PATH` y puede consultar el
proyecto real por id. Aislamiento total = contenedor con el repo copiado y sin
acceso al árbol original.

Y una fuga que **no** arregla el runner: si el test que usás de grader explica
la causa en su docstring o en sus comentarios, el agente lo lee y resuelve por
lectura. El grader tiene que afirmar comportamiento, no narrar el fix. Medido:
un `test_writes.py` que decía "antes de `initialize_request` el shim vivía en
`dispatch`" le entregó la respuesta al agente.

## Selftest de la integración (sin modelo)

```bash
python harness/selftest.py --project backend-microservices            # 14 checks, escribe
python harness/selftest.py --project backend-microservices --read-only  # 11 checks, no muta
```

Verifica por la API lo que el runner usa y lo que se rompió históricamente:
proyecto registrado, `repo_path` existe, schemas de `delta`/`ckg`/`trace`,
sprint, `verify_predicates` discriminando, y el ciclo
`implement_task → trace_push → capture → submit`. Sale con 1 si algo requerido
falla. El runner lo corre automáticamente como preflight cuando se pasa
`--walker-project` (y `--walker-strict` aborta la corrida si falla).

## Reglas de lectura

- Compará contra el CSV anterior; no contra la memoria.
- Una diferencia menor a ~5 puntos porcentuales con k=1 es ruido.
- Si `cost_usd` está vacío o en 0, el proveedor es flat-rate o la sesión no
  estaba en la DB: usá `tokens_in` como proxy de costo.
- `PREFLIGHT_OFF=1` se setea solo: el gate propio no debe influir en la medición.

## Próximo paso natural

Mover la ejecución adentro de walker como `walker run --project X --suite eval/tasks.json`,
reusando `verify_predicates`/`run_tests` como grader en vez de invocar el venv
directo.
