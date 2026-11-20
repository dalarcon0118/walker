# SPEC-WALKER-044: Robustez del flight control — certificación, persistencia y contratos

> **Status:** P0.1 implementado (2026-11-19); P0.2 pendiente
> **Fecha:** 2026-11-19
> **Autor:** Usuario (PO) + diseño del agente
> **Complementa:** ADR-WALKER-016 (CLI), ADR-WALKER-062 (puertos/DI), ADR-WALKER-078 (closed terminal), ADR-WALKER-080 (flight plan), ADR-WALKER-082 (contrato de atención)
> **Insumo:** `docs/walker copy.md` (feedback 6/10 del agente sobre walker)

---

## 1. Objetivo

Cerrar las tres causas de fricción que el feedback identifica como bloqueantes
para confiar el volante:

1. **P0.1 Certificación robusta:** que un corte de salida (pipe, preview,
   truncado) no pueda producir un verde falso ni un rojo fantasma, y que el
   `exit code` de un pipe no se use como evidencia.
2. **P0.2 Persistencia del flight state:** que un reload del plugin no borre el
   verde, y que una escritura posterior **invalide** el verde (hoy no lo hace).
3. **P0.3 Contratos declarativos:** tests que crucen prompt ↔ input_schema ↔
   `side_effects` ↔ spec, para que el drift se detecte en build y no en el
   cierre.

P0.3 se especifica acá como fase posterior, pero **no se implementa en este
ciclo**: el foco de ejecución es P0.1 y, después, P0.2.

Fuera de alcance: reindexar game_reset, cambiar el modelo de sesión/sprint,
reabrir sprints cerrados (ADR-WALKER-078) y la cobertura de cyclo de los
adapters de lenguaje (se reporta como `partial`; walker no depende de ninguno).

---

## 2. Evidencia (medida con la API de walker, 2026-11-19)

| Dato | Valor | Fuente |
|---|---|---|
| CPG walker | 8.923 nodos | `inspect_codebase action=health` |
| CKG walker | 87 nodos | `CkgReadService.check_health` |
| CPG/CKG game_reset | 0 / 0 (not scanned) | idem |
| cyclo en el scope del plugin | `unavailable` | `preflight` |
| cobertura de cyclo | 3.561 / 5.931 funciones (40% sin cyclo) | CPG v2 (repo) |
| eventos de traza | 1.607 (806 `ToolRegistered`, 217 `smoke_session_probe`) | `session_context`/trace |
| sesión de análisis | 21 eventos, 5 `session_metrics`, 1 `close_blocked` | `session_context` |
| refs `NODE.X` no declaradas | 3 (`TASK_SPEC`: evidence, impacted_artifacts, risks) | auditor de `data/workflow/nodes` |
| nodos de prueba en CKG | 4 (`adr-999`, `adr-files-probe`, `adr-files-probe-2`, `project-001`) | CKG query |
| dead code | 1.014 → 22 → 0 (gate 5) | commits `03a87b3`, `1cad272` |

Lectura: parte del 6/10 ya está pagada (dead code, review legacy). Lo que queda
es contrato, no concepto. `adr-999` matchea `^adr-\d+$`, el filtro que usa el
plugin para L3: el harness contaminó la decisión que el agente ve.

---

## 3. Diseño P0.1 — Certificación por contrato

### 3.1 Marcador estable

`bin/walker3 <p>/run_tests` y `<p>/verify_predicates` agregan, después del JSON,
una línea final con la forma:

```
WALKER_RESULT tool=<run_tests|verify_predicates> ok=<0|1> passed=<n> failed=<n> errors=<n> exit=<n>
```

Reglas:
- `ok=1` sólo si el resultado del handler es exitoso y no hay fallos reportados.
- `exit` es el `returncode` real del runner (pytest) o `1` si el handler falló.
- El marcador va **al final** del stdout: sobrevive a un preview/truncado que
  conserva el final; si el consumidor corta el final, no hay marcador y **no se
  certifica**.
- Es aditivo: el JSON sigue siendo la primera línea y la fuente de detalle.

### 3.2 Regla de pipe

El plugin (`agent-plugins/walker-flight-control/index.js`) no certifica por
`exitCode` cuando el comando de verificación contiene un pipe: el exit code es
el del último comando (`tail` devuelve 0 aunque la suite esté roja).

- Con pipe: sólo el marcador puede certificar.
- Sin pipe: marcador primero; luego el parser actual (contadores/`exitCode`)
  como compatibilidad hacia atrás.
- Si hay pipe y no hay marcador: no se certifica y el nudge de cierre explica
  que la verificación fue canalizada y debe repetirse sin pipe.

### 3.3 Parseo del plugin

El bloque `cliVerify` de `index.js` busca `WALKER_RESULT` y parsea
`clave=valor`. El marcador gana sobre los contadores. Se registra
`verifyLevel = "cli-marker"` (o `"cli-marker-pipe"`) para que la traza
distinga la fuente de la certificación.

---

## 4. Diseño P0.2 — Flight state persistente

### 4.1 Estado mínimo

```
flight_state = {
  verified: bool,
  verified_at: iso,
  verify_level: "cli" | "cli-marker" | "tools" | "shell" | "",
  dirty_since: iso | "",
  wrote: bool,
  touched: [paths]   # acotado a 20
}
```

### 4.2 Persistencia y proyección

- La traza ya persiste `session_metrics` con `verified`, `writes`, `touched` y
  `verify_level`. Se agrega un evento `session_state` **sólo en transiciones**
  (`verified → dirty` y `dirty → verified`), no en cada edit.
- `session_context` agrega `flight_state` (último estado conocido) además de
  `trace`. La fuente es la traza (append-only); el `Map` del plugin queda como
  caché.
- El plugin hidrata `verified`/`wrote`/`touched` en el primer hook de la
  sesión si el `Map` está vacío (reload).

### 4.3 Invalidación (bug actual)

`index.js:1393-1398` marca `wrote` pero no resetea `verified`. Regla nueva:
toda mutación (`edit`, `write`, `shell` con redirección) pone `verified=false` y
`safe_dirty=true`; el cierre sólo acepta `verified=true` con `dirty_since` vacío
o posterior a `verified_at`.

---

## 5. Fases, checklists y métricas

### Fase 1 — Certificación (P0.1)

- [ ] Contrato `WALKER_RESULT` emitido por el CLI para `run_tests` y
      `verify_predicates`, con `ok/passed/failed/errors/exit`.
- [ ] El plugin parsea el marcador y lo prioriza sobre contadores.
- [ ] Con pipe: no se usa `exitCode`; sin marcador no se certifica y el nudge lo
      explica.
- [ ] Harness: modos `closeclimark` (corte + marcador), `closeclipipefail`
      (pipe + exit 0 sin marcador) y `closeclimarkpipe` (pipe + marcador).
- [ ] Test del CLI: el marcador aparece y refleja el resultado.
- [ ] M-01 Integridad de escritura: el marcador se emite una vez, después del
      JSON, y no se mezcla con logs — anti-patrón: eventos dentro de la
      transacción/JSON mutable.
- [ ] M-04 Eventos/acoplamiento: el marcador es un contrato estable y el plugin
      no depende del orden de claves — anti-patrón: parser acoplado a prosa.
- [ ] M-06 Extensibilidad: la regla de pipe vive en un único lugar (wrapper) y
      el parser legacy queda como fallback explícito — anti-patrón: lógica de
      certificación duplicada en el nudge/close.

### Fase 2 — Persistencia (P0.2)

- [ ] Evento `session_state` en transiciones y proyección `flight_state` en
      `session_context`.
- [ ] Hidratación del plugin en el primer hook de la sesión y también en el
      camino de cierre (recarga sin hook de contexto).
- [ ] Invalidación de `verified` en toda mutación, con `dirty_since`.
- [ ] Nudge de cierre basado en `flight_state`, no sólo en el `Map`.
- [ ] Harness: reload después de verificar (no debe nudgar) y reload después de
      editar (debe nudgar).
- [ ] M-01 Integridad: la transición verified→dirty y dirty→verified se escribe
      atómicamente, con auditoría — anti-patrón: escritura parcial de estado.
- [ ] M-03 Modelo: `flight_state` es un value object con campos acotados e
      idempotente — anti-patrón: bolsa de campos sin dueño/Data Clump.
- [ ] M-05 Concurrencia: dos hooks concurrentes no pueden dejar `verified=true`
      con `dirty_since` no vacío — anti-patrón: last-write-wins sin validación.

### Fase 3 — Integridad de escritura (P0.4)

Contexto medido: el CAS de generación de `SqliteIndex` rechazaba el `store`
cuando otro proceso había escrito (`{"ok": False, "error": "… cambió bajo el
cache …"}`) y `CognitiveDeltaRepository.save_session`/`save_spec` descartaban el
resultado. `implement_task` devolvía `ok: true` **sin spec y sin plan**, el gate
de desvío no tenía alcance y permitía escrituras fuera de alcance (harness
`mode=scope` en rojo).

- [ ] El conflicto de generación se resuelve como manda su error: refrescar el
      diff-cache (conservando sólo los docs de la operación) y reintentar una
      vez, con warning visible.
- [ ] `save_*` del delta falla (`DeltaWriteError`) si el backend sigue
      rechazando: nunca más un "ok" que no persistió (INV-016).
- [ ] `implement_task` reporta `spec_error`/`plan_error` cuando la persistencia
      falla, en vez de devolver un resultado vacío.
- [ ] Harness `mode=scope` verde: alcance declarado ⇒ escritura fuera BLOQUEA.
- [ ] Tests: conflicto real (dos servicios sobre el mismo data_dir) y
      fail-fast con un `store` que devuelve `ok: False`.
- [ ] La mutación por `shell` no cuenta redirects a `/tmp`, `2>&1` ni `>`
      dentro de comillas: capturar la salida de una verificación no invalida el
      verde (medido: el nudge volvía a aparecer justo después del verde).
- [ ] M-01 Integridad de escritura: el write reporta lo que el backend hizo
      (ok explícito) — anti-patrón: descartar el Result del `store`.
- [ ] M-05 Concurrencia: dos procesos sobre la misma base no pierden la
      escritura del segundo (refresh + reintento) — anti-patrón: CAS sin
      remediación.
- [ ] M-09 Fallback visible: el reintento se loguea y cuenta — anti-patrón:
      reintentar en silencio y llamarlo éxito.

---

## 6. P1.1 — Token de skill por sub-estado

El gate usaba `since=state.updated_at`: cualquier submit cambia `updated_at` y
obliga a recargar el skill. El token es `(task_id, node, sub_state)`:
`load_skill` firma la entrada donde se cargó y el gate acepta esa carga aunque
haya submits posteriores. No elimina el gate; elimina el paso artificial.

- [ ] `load_skill` traza `skill_loaded` con `node`/`sub_state` (fail-open si el
      estado no se puede leer).
- [ ] `has_skill_load` acepta el token de la entrada actual; ignora eventos de
      otra entrada; mantiene `since` como fallback para eventos legacy.
- [ ] El gate pasa `current_node`/`current_sub_state` (no sólo `updated_at`).
- [ ] M-04 Eventos: el token viaja en el evento, no en estado del plugin —
      anti-patrón: firmar en memoria del hook.
- [ ] M-06 Extensibilidad: un solo lugar decide (tracer); el orquestador no
      reimplementa matching — anti-patrón: dos reglas de aceptación.
- [ ] M-01 Integridad: un token de otra tarea/entrada NO satisface el gate —
      anti-patrón: aceptar cualquier load reciente.

## 7. P1.2 — Salud de memoria como precondición

Los health checks existían pero ninguna decisión los consumía. `preflight` y
`agent_context` exponen `memory_health` con `{ckg, cpg, cyclo, trusted}`:

- [ ] `memory_health` en `preflight` y `agent_context` (cache TTL corto: el CPG
      se carga completo para contar cyclo).
- [ ] `trusted=false` ⇒ el plugin NO cita `adr-###`; dice "CKG degradado".
- [ ] Nodos de prueba excluidos del L3 por `origin:test`/tag, no por lista
      negra de ids; los seeds viejos se borran con revisión.
- [ ] `cyclo.status` distingue `partial` (el adapter del lenguaje del proyecto
      no produce cyclo) de `unavailable` (sin funciones); el desglose
      `by_language` dice QUÉ adapter falta y el mensaje no nombra lenguajes.
- [ ] M-09 Fallback visible: el health declara status y conteos, nunca un 0
      silencioso — anti-patrón: "ok" sin nodos.
- [ ] M-03 Modelo: `memory_health` es un value object estable y compartido por
      las dos tools — anti-patrón: dos shapes distintos del mismo health.
- [ ] M-02 Performance: el health se cachea (TTL) y no se recalcula en cada
      `agent_context` — anti-patrón: cargar 8.8k nodos por llamada.

## 8. P1.3 — Captura de conocimiento acoplada a las tools

Medido en la traza real: 227 `kg_query` (lecturas) y 0 `capture_knowledge`
(escrituras). La obligación existía, pero colgaba del sub-estado
`KNOWLEDGE_UPDATE` del motor, que solo corre con `submit`; el agente trabaja con
tools y nunca llega. El cable va donde el agente sí actúa.

- [ ] `session_close`, `run_tests` verde y `spec_status` → `completed` capturan
      el hecho de la sesión (idempotente por `session_id` + `task_id`).
- [ ] La captura viaja en `data.knowledge`; una captura fallida NO convierte
      una tool verde en roja (se reporta el error).
- [ ] M-04 Eventos: la captura se dispara desde la tool, no desde un grafo que
      el cliente puede no recorrer — anti-patrón: obligación en un camino
      paralelo.
- [ ] M-01 Integridad: el hecho se deriva de la spec/traza, no del texto del
      agente — anti-patrón: pedirle al modelo que resuma.
- [ ] M-06 Extensibilidad: un solo helper (`capture_session_fact`) para los tres
      puntos — anti-patrón: tres copias del try/except.

## 9. P1.4 — `project.json` desde evidencia

`prepare` creaba el manifest con `design`/`coding`/`quality_preferences` vacíos y
`build_project_config` no escribía ("no writes"): cada proyecto nacía sin
constitución. Ahora BUILD_PROJECT propone desde evidencia y REVIEW aplica.

- [ ] `build_project_config` propone `test_framework`/`formatter`/`linter` desde
      `pyproject.toml`/`package.json` y devuelve `manifest_patch`.
- [ ] `apply_project_config` completa solo campos vacíos, nunca pisa lo curado,
      es idempotente y corre en REVIEW con `intent=project_ready`.
- [ ] M-01 Integridad: `design`/`quality_preferences` no se inventan —
      anti-patrón: defaults silenciosos.
- [ ] M-06 Extensibilidad: la detección vive en un helper por lenguaje/adapter —
      anti-patrón: ifs de lenguaje repartidos.
- [ ] M-09 Fallback visible: sin evidencia no se propone nada (campo vacío), no
      se escribe "unknown" — anti-patrón: rellenar con placeholders.

### P1.4b — `prepare` siembra la constitución

La propuesta de BUILD_PROJECT llegaba tarde: un proyecto recién preparado
quedaba con `coding` vacío hasta recorrer el workflow. La detección se movió a
`shared_kernel/project_tooling.detect_tooling` (puro, stdlib) para que la usen
`prepare` y la acción sin que una feature importe a la otra (R1).

- [ ] `prepare` escribe `coding.test_framework/formatter/linter` desde evidencia
      al crear `project.json`.
- [ ] Sin evidencia, los campos quedan vacíos (no `"unknown"`) y `design` sigue
      sin inventarse.
- [ ] M-06 Extensibilidad: un solo helper compartido; `prepare` y
      `build_project_config` no pueden divergir — anti-patrón: dos detectores.
- [ ] M-01 Integridad: `prepare` no pisa lo curado (segunda corrida devuelve
      `already_exists` y respeta el valor manual).

## 10. P1.5 — Retención de nodos `session`

Cada cierre agrega un nodo `type: session`; sin política, el CKG se llena de
ruido de bajo valor y cita verdades viejas. El mecanismo ya existía
(`supersedes` → `metadata.status=superseded`, y las queries excluyen
superseded); nadie lo usaba para sesiones.

- [ ] Misma tarea: el hecho nuevo supersede los viejos de esa tarea (una sola
      verdad vigente); los viejos no se borran (historia).
- [ ] Techo: se conservan los `MAX_SESSION_FACTS - 1` más recientes y se
      supersede el resto.
- [ ] `gather_context` no cita nodos superseded (el `search` por texto no
      aplicaba el filtro del puerto).
- [ ] M-01 Integridad: el supersede se resuelve con lectura del CKG, no con una
      lista en memoria — anti-patrón: id hardcodeado.
- [ ] M-06 Extensibilidad: una sola política (`_supersedes_for`) y el lector
      inyectado por `_di` — anti-patrón: cada tool con su borrado.
- [ ] M-09 Fallback visible: la captura reporta `superseded` (lo aplicado) y
      `supersede_missing` (lo pedido que no existe) — anti-patrón: reportar lo
      pedido como si se hubiera aplicado (medido: `superseded: 13` con los 13
      nodos todavía activos, porque `query({})` devuelve 50 de 97).
- [ ] El supersede se resuelve POR ID contra el índice (`get("ckg", id)`), no
      recorriendo la página default de `query`.
- [ ] La captura solo toma la sesión que DECLARA la tarea en su plan, o la de la
      spec; nunca "la única sesión activa" — anti-patrón: adivinar la sesión
      (medido: el harness y esta sesión a la vez dejaron 13 nodos con sesión
      ajena).

## 11. P1.6 — La tarea activa viaja con su spec

Medido 2026-11-19: `session_plan` puso `task=prepare-seeds-manifest` y la
captura guardó el hecho como `skill-token-memory-health`. El plan de vuelo y la
spec eran dos verdades: la captura resolvía la spec por `in_progress` y heredaba
la identidad de la tarea anterior del sprint.

- [ ] `session_plan mode=set task=X` ancla `task_context` de la spec de X y
      avanza el estado del workflow (`record_task`).
- [ ] Si X no existe en el sprint, el plan se guarda y la respuesta trae
      `task_link.reason` (visible, no se inventa una spec).
- [ ] `spec_status → in_progress` mueve el plan de la sesión y el estado del
      workflow a esa spec; `completed` no deja una tarea cerrada como activa.
- [ ] El nudge de cierre corre `run_tests` con la tarea del plan y el
      `session_id`, para que la captura tenga (task, session) con qué anclarse.
- [ ] La captura resuelve por id exacto o por la tarea del plan; la heurística
      `in_progress` solo corre cuando el caller no nombró tarea.
- [ ] M-01 Integridad: la identidad del hecho viene de la spec o del plan, no
      del rótulo suelto del caller — anti-patrón: heredar la última `in_progress`.
- [ ] M-04 Eventos: el cambio de tarea se refleja en el mismo `set` que lo
      declara — anti-patrón: sincronizar en un job aparte.
- [ ] M-09 Fallback visible: sin spec y sin traza la captura reporta el fallo en
      `data.knowledge` y la tool sigue verde — anti-patrón: fabricar un hecho.

## 12. P1.7 — El harness no se ancla a un proyecto

El harness tenía `game_reset` escrito en los comandos y una ruta absoluta al
repo de walker. Correr la suite desde otro directorio hacía que el plugin
resolviera un proyecto (walker) mientras los comandos apuntaban a otro
(`game_reset`): 7 modos fallaban por el entorno, no por el código. Y dependía de
un proyecto del usuario que el harness no controla.

- [ ] `suite.mjs` crea su propio fixture (`tmp/walker-harness-fixture`), lo
      registra con `_/prepare` y pasa `HARNESS_PROJECT`/`HARNESS_PROJECT_DIR`;
      `--project <id>` reusa uno registrado y nada más.
- [ ] El repo bajo prueba se deriva del layout del plugin (`../..`), no de una
      ruta absoluta; `gate.test.mjs` reemplaza cada `game_reset` por el proyecto
      del harness.
- [ ] El CLI despacha las tools de la Fase 1 (`prepare`/`list`/`status`): el
      plugin ya recomendaba `walker3 _/prepare …` y respondía "tool desconocida".
- [ ] El fixture se borra antes de prepararse (estado limpio entre corridas) y
      los modos que dependen de "no hay trabajo previo" (`bind`) reciben su
      propio fixture aislado: el plugin decide con el estado del PROYECTO, así
      que compartir fixture con modos que declaran tareas los contaminaba.
- [ ] M-06 Extensibilidad: un solo origen para el proyecto (env) — anti-patrón:
      un id por modo.
- [ ] M-09 Fallback visible: sin `--project`, se ve en la primera línea cuál es
      el proyecto del harness — anti-patrón: usar el del cwd en silencio.

## 13. P1.8 — El runner de tests sale del proyecto

Medido: `run_tests` ejecutaba `pytest -q` a mano y `verify_engine` buscaba el
manifiesto en `<repo>/data/project.json`, ruta que dejó de existir con el
layout `.cognitive/`. Un proyecto que declaraba `vitest` corría
`python3 -m pytest -k …`; un proyecto JS/Go no podía certificar nunca y el
gate de cierre quedaba en nudge infinito.

- [ ] El framework es `coding.test_framework` del manifiesto, resuelto por
      `ProjectService.resolve_paths` (data_dir canónico), con legacy
      `<root>/project.json` y `<root>/data/project.json`.
- [ ] `build_test_command` / `resolve_test_framework` son API pública (el
      handler las usa); `run_tests` reporta el comando realmente ejecutado.
- [ ] Sin declaración o con framework desconocido → `TestRunnerUnavailable`
      con mensaje accionable y lista de runners soportados. NO se asume pytest.
- [ ] `test_pass` convierte ese error en `(False, {"error": ...})`, no en una
      excepción que rompa el batch de predicates.
- [ ] `identity.framework`/`language` no cuentan como runner (django/python no
      dicen cómo se corren los tests).
- [ ] La regla de 0 tests (SPEC-014) sigue vigente para pytest; en go/cargo el
      exit code es la autoridad.
- [ ] M-09 Fallback visible: el default silencioso a pytest se elimina —
      anti-patrón: inventar el runner cuando falta el dato.
- [ ] M-06 Extensibilidad: una sola tabla (`_TEST_RUNNERS`) para los dos
      caminos de verificación — anti-patrón: otro `if language == "python"`.

## 14. P1.9 — Imports y coupling por lenguaje, con `not_checked` visible

`hallucination.py` resolvía imports solo como Python: omitía TODO import
relativo de JS (un `./components/Foo` inexistente nunca se detectaba) y trataba
cualquier bare specifier como paquete npm (los alias `@/…` eran alucinaciones).
`coupling.py` tenía una lista de prefijos de proyecto hardcodeada
(`cognitive_v2`, `mcp`, …) y su Ca solo miraba `*.py` con módulos punteados: en
un repo JS, Ce contaba todo como externo y Ca devolvía 0 como si nadie
importara el archivo.

- [ ] Un adapter (`lang_imports`) decide "local vs externo" por lenguaje;
      `None` = no sé (sin adapter/alias) → `not_checked`, nunca `False`.
- [ ] JS/TS: relativos resuelven a archivo real (si no, alucinación); alias
      (`@/`, `~/`, `#…`) → NOT_CHECKED; bare declarado sin instalar → WARNING;
      ni declarado ni instalado → HALLUCINATION.
- [ ] `scan_file` devuelve `status: checked|not_checked` y el adapter agrega
      `checked_files`/`not_checked_files`/`not_checked_reasons`.
- [ ] `coupling` sin listas de prefijos: Ce sale del adapter y Ca es por
      lenguaje (`checked` para JS por resolución, `approximate` para Python/Go,
      `not_checked` cuando no hay resolver).
- [ ] M-06 Extensibilidad: una sola tabla extensión→lenguaje (`lang_map`), antes
      duplicada en `codereview/config.py` y `ast_grep_adapter.py`.
- [ ] M-09 Fallback visible: un lenguaje sin adapter no suma "0 alucinaciones";
      se reporta como no verificado con el motivo — anti-patrón: cero verde.

## 15. P1.10 — El protocolo se negocia, no se adivina

Medido en la traza: 7 escrituras bloqueadas por `bind` antes de que el agente
entendiera el protocolo, y 20 bloqueos de `scope` (5 archivos distintos) en la
misma sesión; cada bloqueo es un turno perdido y el agente tiene que
reconstruir un `scope` de 55 entradas a mano.

- [ ] `session_plan mode=set task=X` registra X como tarea activa del workflow
      aunque no exista spec: la tarea del plan ES la tarea, no una etiqueta.
- [ ] En la primera escritura, si el plan ya declara tarea y la sesión está
      ligada, el plugin la declara solo (una llamada a `session_plan`),
      verificando contra `workflow_state`; si no hay plan, el bloqueo accionable
      sigue igual.
- [ ] Una escritura fuera del `scope` se negocia UNA vez por archivo: el plugin
      llama `session_plan mode=add`, invalida el cache del plan y deja pasar la
      escritura. Si walker no responde, se bloquea con el comando manual.
- [ ] La ampliación queda visible: evento `flight_control_scope_expanded` y
      `scope_expanded` en `session_metrics` — anti-patrón: crecer en silencio.
- [ ] M-04 Eventos: la negociación ocurre en el mismo hook que la escritura, no
      en un job aparte — anti-patrón: sincronizar después.
- [ ] M-09 Fallback visible: sin plan no hay auto-declare; sin respuesta no hay
      auto-expand — el bloqueo explica qué hacer.
- [ ] M-06 Extensibilidad: `session_plan` es el único punto que cambia el plan
      (CLI y plugin usan el mismo), sin caminos paralelos.

## 16. P1.11 — La verificación existe aunque el hook no la vea

El plugin certifica con lo que observa de los comandos. Cuando la tool se
invoca por fuera de su wrapper (Code Mode, shell del host, subagente), el
`run_tests` verde no llega al hook y el gate sigue pidiendo verificación sin
que haya cambiado una línea (medido: nudge con `git status` limpio).

- [ ] `run_tests` y `verify_predicates` escriben un `session_state` en el trace
      (`source=walker_task`, `recorded_by=task_tool`, `verified`, `verified_at`,
      `verify_level`, `runner`, `returncode`).
- [ ] Fail-closed: sin `session_id` real (`""`/`mcp`) no se escribe; una
      verificación anónima marcaría como verde a cualquier sesión.
- [ ] El plugin hidrata ese estado antes de nudgar (`force`) y adopta el verde
      solo si `verified_at` es posterior a la última escritura conocida: no
      resucita un verde viejo tras editar.
- [ ] `session_plan` expone `add`/`remove` (y `path`) en el schema MCP: el
      handler ya los soportaba y el schema validaba antes de llegar.
- [ ] `scripts/flight_control_metrics.py` agrega el trace en métricas
      comparables (denials por gate, auto-declaradas, scope ampliado, nudges,
      verificaciones por tool, sugerencias) y excluye el harness.
- [ ] M-09 Fallback visible: si el trace no responde, la tool sigue verde y el
      cierre vuelve al comportamiento anterior (observación del hook).
- [ ] M-04 Eventos: la verificación se registra en la MISMA tool que la ejecuta,
      no en un job que puede no correr.

## 17. P1.12 — La escritura que el hook no vio también cuenta

Con P1.11 el plugin ya no depende de ver la VERIFICACIÓN. Quedaba el otro lado:
si la escritura entra por un camino que el hook no clasifica (subagente, shell
del host, `sed -i`, `git apply`), `wrote` queda en falso y el cierre pasa
limpio sin deuda. Medido: el mismo entorno que disparó el nudge de verificación
mostraba `wrote` solo por lo que el hook alcanzaba a ver.

- [ ] `changed_since_last_scan` (shared_kernel) compara el árbol con el último
      snapshot: evidencia de escritura sin depender de hooks.
- [ ] `run_tests` y `verify_predicates` toman el diff ANTES de ejecutar (para no
      contar `__pycache__`/caches) y lo adjuntan al `session_state`
      (`wrote`, `writes`, `touched`, `touched_source=watcher`).
- [ ] `session_context` completa `flight_state.wrote` desde ese diff SOLO si el
      trace no lo trae y la sesión YA tiene estado de vuelo: lo observado por el
      plugin es más preciso, y una sesión nueva no hereda cambios previos del
      repo.
- [ ] Sin cambios no se marca `wrote` (anti-patrón: inventar deuda/limpieza).
- [ ] M-09 Fallback visible: `touched_source=watcher` distingue evidencia
      indirecta de una observación del hook.
- [ ] M-06 Extensibilidad: un solo helper (`changed_since_last_scan`) para las
      tres superficies; no hay un scan por tool.

## 18. P1.13 — El baseline de escritura es de la sesión, no del proyecto

P1.12 hizo visible la escritura que el hook no ve, pero el diff se hacía contra
el snapshot GLOBAL del proyecto: una sesión nueva heredaba las escrituras de la
anterior y el cierre quedaba en deuda por trabajo ajeno. Medido: el primer
`session_context` de una sesión recién abierta ya veía `wrote` sin que hubiera
escrito nada.

- [ ] `FileWatcher.changes_since(session_id=...)` diffea contra
      `.file_snapshot.session-<sid>.json`: la primera lectura establece el
      baseline de esa sesión y devuelve `[]`.
- [ ] Sin `session_id` se mantiene el snapshot global (es el que alimenta el
      estado del CPG/indexado; su semántica no cambia).
- [ ] `session_context` completa `flight_state.wrote` aunque el trace no tenga
      estado de vuelo (antes no escaneaba justamente para no heredar).
- [ ] `run_tests` y `verify_predicates` piden la evidencia con su sesión.
- [ ] Lecturas que no cambian nada no reescriben el snapshot (churn de ~50KB
      por llamada), y los snapshots de sesión se podan a 50.
- [ ] El footprint del motor (`.cognitive/`, `*.json.gz`, `.file_snapshot*`,
      `data/*.sqlite3`) nunca cuenta como escritura del agente.
- [ ] M-09 Fallback visible: `touched_source=watcher` marca la evidencia
      indirecta — anti-patrón: presentarla como observación del hook.
- [ ] M-02 SSOT de estrategias: un solo helper (`changed_since_last_scan`) para
      las tres superficies — anti-patrón: un scan por tool con reglas propias.
- [ ] M-05 Concurrencia: dos sesiones sobre el mismo repo no se atribuyen
      escrituras anteriores al arranque de la otra — anti-patrón: last-write-wins
      sobre un snapshot compartido. Límite conocido: una escritura POSTERIOR a
      la última lectura de otra sesión sí se ve (el snapshot se refresca).

## 19. P1.14 — "Siguió la sugerencia" es una acción, no una tool

La métrica de adherencia sólo acreditaba `submit`/`transition`: el agente
escribía, verificaba o cambiaba el plan y el trace lo contaba igual como
`workflow_suggestion_ignored` (medido: 63-81 ignoradas por sesión, `followed`
1/5). El número describía el instrumento, no al agente.

- [ ] Escribir (`edit`/`write`) y mutar por `shell` marcan `followed` con
      `followedBy=edit|shell` — sólo si el gate permitió la escritura.
- [ ] `run_tests`/`verify_predicates` (tool o CLI) marcan `followedBy=verify`,
      verde o rojo: llamar la verificación ES seguimiento.
- [ ] `implement_task`, `session_bind` y `session_plan` (tool o CLI) marcan
      `followedBy=task|bind|plan`; `submit`/`transition` mantienen `tool`.
- [ ] `session_plan` por tool invalida la caché del plan (antes sólo por shell).
- [ ] `workflow_suggestion_ignored` sólo se emite cuando la ejecución no tuvo
      NINGUNA acción, y lleva `wrote`/`verified`/`reason` para leer el caso.
- [ ] El reporte agrupa `seguidas` por `followedBy` y lista las ignoradas con
      su detalle; si una ignorada trae `wrote`/`verified`, el reporte la marca
      como INCONSISTENTE (el agente actuó y la marca falló).
- [ ] Medido en la primera ventana con el plugin recargado: de 3 ejecuciones
      con sugerencia, la métrica vieja contaba **0/3** seguidas; la nueva
      cuenta **2/3** (`edit`) y la restante es una ejecución de 2 s sin
      ninguna acción. La primera ignorada que quedó en el trace es el caso
      exacto del sesgo: en esa ejecución el agente escribió 4 archivos
      (`writes` 190 → 194).
- [ ] M-04 Eventos como contrato: `followed`/`followedBy` son el contrato de la
      métrica — anti-patrón: inferir adherencia desde el nombre de la tool.
- [ ] M-03 Audit trail: el evento dice QUÉ acción siguió — anti-patrón: un
      booleano sin procedencia.
- [ ] M-06 Extensibilidad: una acción nueva se marca en el punto donde ocurre —
      anti-patrón: una allowlist de tools en el agregador.

## 20. P1.15 — Auditoría #6/#7: sin éxito falso ni layout a mano

Dos deudas de la auditoría, ambas baratas y de la misma familia (un camino que
"hace algo por defecto" en vez de decidir o fallar):

- [ ] `initialize_knowledge_base` es FAIL-FAST: sin CKG (`saved: false`), sin
      nodo guardado, excepción o resultado no-dict → `TaskResult.failure` con el
      motivo. Antes devolvía `ok: True, skipped: True` y el workflow creía tener
      memoria sembrada.
- [ ] Reporta lo que guardó (`nodes_saved`, `node_id`), no un `ok` vacío.
- [ ] `prepare` compone el layout con `paths.compose_project_layout`
      (`project.cognitive_dir`/`project.data_dir`) en vez de
      `<repo>/.cognitive/projects/<pid>/data` a mano: con layout configurado,
      el join hardcodeado creaba un segundo layout que `resolve_paths` no
      encontraba.
- [ ] M-01 Integridad de escritura: una acción que no pudo cumplir su contrato
      no devuelve éxito — anti-patrón: `ok: true` con `error` adentro.
- [ ] M-06 Configuración externa: una sola fuente para el layout (config) —
      anti-patrón: `.cognitive`/`data` repetidos como literales.
- [ ] M-09 Fallback visible: sin `skipped` silencioso; el fallo se ve en el
      resultado de la task — anti-patrón: degradar a "no hizo nada" con ok.

## 21. P1.16 — El baseline arranca con la sesión, no con la primera lectura

P1.13 corrigió la herencia entre sesiones, pero el baseline se creaba en el
primer `session_context`. Si el agente escribía ANTES (subagente, shell del
host, `sed -i`, `git apply`), esa escritura caía dentro del baseline y no
dejaba deuda: el cierre pasaba limpio.

- [ ] `seed_session_baseline(root, session_id)` crea el snapshot si NO existe y
      nunca lo refresca (refrescarlo absorbería las escrituras que debe ver).
- [ ] `session_open` y `session_bind` lo fijan: son el arranque real de la
      sesión (`write_baseline` en el payload de `session_open`).
- [ ] Idempotente: abrir/vincular dos veces no crea ni pisa el baseline.
- [ ] El canal anónimo (`session_id="mcp"`) no tiene baseline propio: un
      snapshot compartido contaminaría todas las sesiones.
- [ ] M-05 Concurrencia: el baseline se fija una vez por sesión y no se
      re-escribe en cada lectura — anti-patrón: last-write-wins sobre el diff.
- [ ] M-04 Eventos: `write_baseline` hace visible si el baseline se creó o ya
      existía — anti-patrón: asumir que la evidencia está armada.

## 22. P1.17 — Los side effects que no se aplican, se ven

`transform_spec` devolvía `{"ok": True, "skipped": True}` **sin motivo** en
cuatro caminos: servicio no resuelto, servicio sin `update_spec`, sin sprint,
sin `spec_id`, y `update_spec -> None`. El resultado era exactamente el bug
medido el 2026-11-18: la spec no se movía y el submit seguía como si nada.
Los motivos tampoco llegaban al agente: `apply_side_effects.results` se
descartaba cuando `ok` era verdadero.

- [ ] Sin `sprint_service` o sin `update_spec` → `ok: False` (composición rota,
      aborta el submit como cualquier efecto no opcional).
- [ ] Sin sprint resuelto / sin `spec_id` → skip CON `reason` y `missing`.
- [ ] `update_spec -> None` con sprint y spec resueltos → `ok: False`: la
      transición pedida no ocurrió (antes: "skip" y estado viejo).
- [ ] `sprint.py`: los seis `sin sprint service` dejan de ser éxito.
- [ ] `emit_event`: el skip de tracing dice `persisted: False` + `reason`.
- [ ] El submit devuelve `side_effects: [{type, skipped|error}]` cuando hay
      efectos que no se aplicaron (máx 5); los idempotentes y los no
      registrados no se reportan.
- [ ] M-01 Integridad de escritura: un efecto que no cumplió su contrato no
      devuelve éxito — anti-patrón: `ok: true` con `skipped` sin motivo.
- [ ] M-09 Fallback visible: el motivo viaja al resultado del submit, no solo
      al log — anti-patrón: degradar en silencio y esperar que nadie pregunte.
- [ ] M-06 Extensibilidad: la clasificación vive en un helper
      (`_side_effect_notes`) — anti-patrón: cada efecto inventa su forma.

## 23. P1.18 — El efecto que no se aplicó queda en el TRACE

P1.17 hizo visible el skip en la respuesta del submit, pero seguía dependiendo
de que el agente la mire: el reporte de flight control no podía contar cuántas
veces el motor no aplicó un efecto, ni en qué nodo, ni por qué.

- [ ] Cada nota de `_side_effect_notes` se traza como evento propio:
      `side_effect_skipped` (skip con motivo) o `side_effect_failed` (error de
      un efecto opcional), con `type`, `node`, `sub_state` y `reason`/`error`.
- [ ] El evento lleva `task_id`, `session_id` y `chain_dir`: sin ellos no se
      puede correlacionar con la sesión que lo sufrió.
- [ ] `WorkflowEvent.to_dict` publica el payload como `data` (la clave que
      persiste el TraceGraph) y `source=workflow` (el default `walker_task` lo
      haría pasar por una tool).
- [ ] Trazar nunca frena el workflow: el error del tracer se ignora con debug.
- [ ] `scripts/flight_control_metrics.py` cuenta skips/fallos y los agrupa por
      tipo de efecto; el reporte los imprime.
- [ ] M-04 Eventos como contrato: el nombre y el payload del evento son el
      contrato de la métrica — anti-patrón: parsear logs.
- [ ] M-09 Fallback visible: un efecto que no se aplicó no puede existir solo
      en la respuesta de un submit que nadie leyó — anti-patrón: log y olvido.
- [ ] M-01 Integridad: el evento se emite después de PERSISTIR el submit, no
      dentro de la ejecución del efecto — anti-patrón: trazar un submit que no
      guardó estado.

## 24. P0.3b — Contrato prompt ↔ schema (bloqueante)

- [ ] Todo campo que un prompt manda en `submit` está declarado en el
      `input_schema` de su sub-estado (40 → 0 violaciones).
- [ ] El chequeo es bloqueante en `test_workflow_contract_bdd.py`.
- [ ] M-01 Integridad: el schema es la única fuente de campos aceptados —
      anti-patrón: prosa que declara campos que el schema no.
- [ ] M-06 Extensibilidad: agregar un campo al prompt sin declararlo rompe el
      test en CI — anti-patrón: allowlist de excepciones.
- [ ] M-04 Acoplamiento: prompts y nodos se validan juntos, sin defaults
      ocultos — anti-patrón: aceptar cualquier campo en runtime.

---

## 25. failure_scenarios

```json
"failure_scenarios": [
  "Un pipe devuelve exit 0 con la suite roja y el plugin certifica por exitCode → causa: el exit code es del último comando del pipe → mitigación: con pipe sólo certifica WALKER_RESULT; sin marcador no se certifica → detección: modo closeclipipefail del harness + assert de nudge",
  "Un preview/truncado corta el JSON y no hay marcador → el plugin queda verified=false y el nudge se repite → causa: certificación depende de un payload mutable → mitigación: marcador final estable y regla 'sin marcador no hay verde' con mensaje accionable → detección: modo closeclimark (corte + marcador) y un test que verifique que un corte sin marcador no certifica",
  "Un reload del plugin pierde el Map y borra verified → el agente recibe el nudge aunque la suite esté verde → causa: estado de vuelo en memoria → mitigación: session_state en trace + flight_state en session_context + hidratación en setup → detección: harness con re-setup después de verificar",
  "Una escritura después de verificar deja verified=true → el cierre acepta cambios sin probar → causa: la mutación no invalida el verde → mitigación: verified=false + dirty_since en toda mutación → detección: test de escritura posterior a la certificación",
  "Dos sesiones o dos hooks concurrentes escriben flight_state y el último gana con un estado incoherente → causa: last-write-wins sin validación → mitigación: transiciones atómicas y `verified_at`/`dirty_since` consistentes → detección: test de dos transiciones intercaladas",
  "Otro proceso avanza la generación del delta entre la lectura y la escritura → el CAS rechaza el store y el repo descarta el ok:False → la spec/plan no queda en el delta y el gate de desvío opera con scope vacío (medido: implement_task ok sin spec, harness mode=scope en rojo) → mitigación: refrescar el diff-cache y reintentar una vez; si sigue fallando, DeltaWriteError visible → detección: test de dos servicios sobre el mismo data_dir + harness mode=scope",
  "El agente captura la salida de la verificación a /tmp (o usa 2>&1) y el detector de mutaciones lo cuenta como escritura → el verde recién obtenido se invalida y el cierre vuelve a nudgar (medido: `run_tests … > /tmp/out 2>&1`) → causa: el detector busca '>' sin mirar destino ni comillas → mitigación: shellMutation ignora /dev/null, fd dups, /tmp y `>` citados; sólo cuenta redirects dentro del proyecto → detección: harness mode=shelltmp (verde + redirect a /tmp ⇒ 0 nudges) y mode=shellmut (redirect al proyecto ⇒ mutación)",
  "Cada submit cambia state.updated_at y el gate de skills exige recargar el skill en cada paso → pasos artificiales y fricción (feedback 6/10) → causa: since=updated_at como única evidencia → mitigación: load_skill firma (task, node, sub_state) y el gate acepta la carga de la entrada actual; since queda como fallback legacy → detección: test_skill_token_bdd + harness con dos submits",
  "El CKG está vacío o contaminado y el plugin cita adr-999 como si fuera una decisión del proyecto → el agente decide sobre memoria falsa → causa: los health checks existían pero ninguna decisión los consumía → mitigación: memory_health.trusted en preflight/agent_context, exclusión por origin:test y borrado de seeds con revisión → detección: harness mode=degraded (no cita adr-, dice 'CKG degradado')",
  "Un prompt envía un campo que su input_schema no declara → el contrato de accept cruza con campos fantasma y el drift sólo se ve en runtime → causa: prosa y schema versionados por separado → mitigación: check bloqueante prompt→schema y declaración de los campos (40 → 0) → detección: test_workflow_contract_bdd",
  "La obligación de capturar conocimiento vive en un sub-estado del motor que solo corre con submit; el agente trabaja con tools → 227 kg_query (lecturas) y 0 capture_knowledge (escrituras) → causa: el camino de tools no ejecuta acciones auto ni gates → mitigación: captura acoplada a session_close/run_tests verde/spec_status completed, visible en data.knowledge → detección: tests/test_capture_side_effects_bdd.py y CKG creciendo en una sesión real",
  "project.json nace con design/coding/quality vacíos y build_project_config no escribe → cada proyecto sin constitución y el agente re-deriva convenciones de la prosa → causa: la propuesta existía pero nada la aplicaba → mitigación: propuesta desde evidencia + apply en REVIEW completando solo lo vacío → detección: tests/test_project_init_config_bdd.py",
  "Cada cierre agrega un nodo session y el CKG acumula verdades viejas de la misma tarea → el contexto cita historia como si fuera vigente y el índice crece sin cota → causa: el mecanismo de supersede existía pero nadie lo usaba → mitigación: supersede por tarea + techo de 25 + gather_context ignora superseded → detección: tests/test_session_retention_bdd.py",
  "El plan de vuelo dice una tarea y la spec del sprint dice otra → la captura guarda el hecho con la identidad de la tarea anterior → causa: captura resolvía la spec por in_progress y no había puente plan→task_context → mitigación: session_plan/spec_status anclan task_context + captura exacta con fallback al plan → detección: tests/test_task_context_advance_bdd.py",
  "El supersede de nodos session recorre `query({})` (50 de 97 docs) → reporta `superseded: N` y no degrada ninguno; el contexto sigue citando historia como vigente → causa: resolver por página en vez de por id → mitigación: `get(\"ckg\", id)` + reportar aplicado/faltante → detección: test_supersede_atraviesa_la_pagina_default_del_ckg",
  "Dos agentes/sesiones a la vez y la captura toma 'la única sesión activa' → 13 nodos con la sesión del harness y la tarea de acá → causa: adivinar la sesión cuando la tarea no la declara → mitigación: solo la sesión que declara la tarea en su plan o la de la spec; si no, captura fallida visible → detección: test_no_usa_una_sesion_que_no_declara_la_tarea",
  "El harness apunta a un proyecto fijo (game_reset) y el plugin resuelve otro desde el cwd → 7 de 29 modos fallan por el entorno y la suite depende de datos del usuario → causa: id y ruta absoluta hardcodeados → mitigación: fixture propio registrado con _/prepare + HARNESS_PROJECT, y rutas derivadas del layout → detección: suite.mjs sin argumentos (29/29) y test de dispatch de Fase 1 en el CLI",
  "run_tests y verify_engine buscan project.json en <repo>/data y caen a pytest → un proyecto con vitest ejecuta python3 -m pytest y nunca certifica (gate de cierre infinito) → causa: ruta del manifiesto desactualizada respecto del layout .cognitive + default silencioso → mitigación: resolver por ProjectService.resolve_paths, runner declarado y TestRunnerUnavailable sin declaración → detección: tests/test_run_tests_framework_bdd.py (vitest/go/pytest y sin declaración)",
  "El detector de imports solo entiende Python y coupling usa prefijos hardcodeados → en un repo JS los alias son alucinaciones falsas y Ca=0 aunque haya importadores; en Go/Rust el resultado es un cero que parece dato → causa: resolución de imports sin adapter por lenguaje → mitigación: lang_imports + status/not_checked y agregado que separa verificados de no verificados → detección: tests/test_language_imports_bdd.py (JS relativos/alias, Go/Rust not_checked, Ca por resolución)",
  "El gate de intake repite el bloqueo aunque el plan ya declare la tarea, y el de scope bloquea cada intento hasta que el agente reescriba el scope completo → 7 + 20 bloqueos en una sesión, cada uno un turno perdido → causa: la declaración y la ampliación vivían solo del lado del agente → mitigación: auto-declare desde el plan y una negociación por archivo con session_plan mode=add, verificada contra workflow_state → detección: harness modes autodeclare y scope",
  "La verificación corre por fuera del wrapper del plugin (Code Mode / shell del host / subagente) → el verde no llega al hook y el gate vuelve a nudgar sin cambios de código → causa: el plugin infiere la verificación del comando que ve, no del hecho persistido → mitigación: run_tests/verify_predicates escriben session_state en el trace y el plugin lo hidrata antes de nudgar (fail-closed sin session_id) → detección: harness mode extverify y tests/test_verification_trace_bdd.py",
  "La escritura entra por un camino que el hook no clasifica (subagente, sed -i, git apply) → wrote queda en falso y el cierre pasa limpio sin verificación → causa: el estado de escritura dependía 100% de observar edit/write/shell → mitigación: diff del FileWatcher adjunto al session_state y completado en session_context solo si el trace no lo trae → detección: tests de changed_since_last_scan y merge_write_evidence",
  "El diff de escritura se hace contra el snapshot GLOBAL del proyecto → una sesión nueva hereda las escrituras de la anterior y el cierre queda en deuda por trabajo ajeno → causa: un único snapshot compartido entre sesiones → mitigación: snapshot por sesión (.file_snapshot.session-<sid>.json), baseline en la primera lectura y poda a 50 → detección: test_el_snapshot_es_por_sesion_no_del_proyecto y test_session_context_sin_flight_state_igual_detecta_escritura",
  "La adherencia sólo acredita submit/transition → 63-81 sugerencias 'ignoradas' aunque el agente escribió, verificó o cambió el plan → causa: followed se marcaba en una única tool → mitigación: toda acción real (edit/write/shell/verify/task/plan/bind/cli) marca followed con su origen; ignored sólo cuando no hubo ninguna → detección: harness mode injectsuggest y tests/test_flight_control_metrics_bdd.py",
  "initialize_knowledge_base devuelve ok:true con el error (o skipped) adentro → el workflow cree que la memoria está sembrada y la falta aparece en las consultas o nunca → causa: el bootstrap se declaró fail-open sin distinguir best-effort de contrato incumplido → mitigación: TaskResult.failure con el motivo (sin CKG, sin nodo, excepción) → detección: tests de initialize_knowledge_base (caído, sin guardar, éxito)",
  "prepare arma <repo>/.cognitive/projects/<pid>/data a mano → con project.cognitive_dir/data_dir configurados crea un layout que resolve_paths no encuentra y el proyecto queda inaccesible por API → causa: literales en vez del SSOT de rutas → mitigación: paths.compose_project_layout → detección: test_prepare_usa_el_layout_configurado_y_no_lo_hardcodea",
  "El baseline de escritura se crea en el primer session_context y no al abrir la sesión → lo escrito antes (subagente, sed -i, git apply) cae dentro del baseline y el cierre pasa limpio → causa: el arranque de la sesión no fijaba evidencia → mitigación: seed_session_baseline en session_open/session_bind, idempotente y sin refresco → detección: test_session_open_fija_el_baseline_antes_del_primer_context",
  "transform_spec devuelve ok:true+skipped sin motivo (sin servicio, sin sprint, sin spec_id, update_spec→None) y results se descarta cuando ok es verdadero → la spec no se mueve y el submit sigue como si nada (medido 2026-11-18) → causa: skip indistinguible de 'nada que hacer' y motivo no propagado → mitigación: wiring como error, skips con reason/missing, update_spec→None como error y side_effects en el resultado del submit → detección: tests de transform_spec y del orquestador (_side_effect_notes)",
  "El motivo del efecto no aplicado vive solo en la respuesta del submit → si el agente no la lee, nadie sabe que el motor no movió la spec, y el reporte no puede contarlo → causa: la visibilidad dependía de un consumidor humano/LLM → mitigación: un evento por nota (side_effect_skipped/failed) con type/node/reason, task y session, y agregado en el reporte → detección: test del tracer contra el TraceGraph y test del reporte"
]
```

---

## 26. Criterios de aceptación

1. Un comando de verificación con pipe y sin marcador **nunca** produce verde,
   aunque el `exitCode` sea 0.
2. Un corte de salida que conserva el marcador **sí** certifica; uno que lo
   pierde, no.
3. `run_tests` y `verify_predicates` emiten el marcador con los conteos reales.
4. El harness cubre los tres casos (marcador, pipe sin marcador, pipe con
   marcador) y la suite completa sigue verde.
5. P0.2: un reload después de verificar conserva el verde; una edición posterior
   lo invalida y el nudge vuelve con el motivo correcto.
6. P0.4: un conflicto de generación no pierde la escritura (refresh + reintento)
   y, si persiste, el error es visible; el harness `mode=scope` bloquea la
   escritura fuera de alcance.
7. P1.1: la carga del skill de la entrada actual vale aunque haya submits
   posteriores; un token de otra entrada o tarea no satisface el gate.
8. P1.2: `preflight` y `agent_context` reportan `memory_health`; con
   `trusted=false` el plugin no cita `adr-###` y `cyclo` se declara `partial`
   cuando el extractor no cubre el scope.
9. P0.3b: cero campos de prompt sin declarar y el chequeo es bloqueante en CI.
10. P1.3: cerrar la sesión, un `run_tests` verde o completar la spec deja el
    hecho en el CKG (idempotente) y una captura fallida no rompe la tool.
11. P1.4: el proyecto recién preparado tiene `test_framework`/`formatter`/`linter`
    desde evidencia; volver a aplicar no cambia nada y no pisa lo curado.
12. P1.5: una tarea tiene un solo nodo `session` vigente y el CKG no supera el
    techo; los viejos quedan como historia (`status=superseded`) y el contexto
    no los cita.
13. P1.6: cambiar la tarea en el plan (o iniciar una spec) deja el
    `task_context` y el estado del workflow en esa tarea; una tarea inexistente
    se reporta y la captura nunca hereda la identidad de otra spec.
14. P1.5 (retención real): con más nodos que una página, el supersede degrada
    por id; `superseded` refleja lo aplicado y el CKG queda con una sola
    `session` activa por tarea.
15. P1.7: `node suite.mjs` sin argumentos pasa los 29 modos contra su propio
    fixture, sin tocar ningún proyecto del usuario; y `walker3 _/prepare`
    responde (antes: "tool desconocida").
16. P1.8: un proyecto que declara `vitest`/`go`/`pytest` ejecuta SU runner;
    sin declaración, `run_tests` falla con un mensaje accionable y no ejecuta
    nada (no hay default a pytest).
17. P1.9: un import relativo JS inexistente es alucinación y un alias es
    `not_checked`; un lenguaje sin resolver nunca aparece como "0
    alucinaciones", y `coupling` reporta `ca_status` en vez de un cero mudo.
18. P1.10: una sesión con plan declara y escribe sin `implement_task`; una
    escritura fuera de alcance se agrega al plan en la misma llamada (una sola
    vez por archivo) y queda trazada.
19. P1.11: un `run_tests` verde ejecutado por fuera del wrapper deja el cierre
    sin nudge (el plugin hidrata el trace) y el reporte de métricas lo cuenta
    como verificación de la tool; `session_plan` acepta `add`/`remove`.
20. P1.12: una escritura que el hook no vio deja `wrote=true` en el flight
    state (con `touched_source=watcher`); sin cambios reales no se marca nada.
21. P1.13: una sesión nueva no hereda escrituras previas (baseline propio) y una
    escritura que el hook no vio en ESA sesión sí queda como deuda.
22. P1.14: una ejecución con escritura/verificación/plan queda como seguimiento
    (`followedBy`); `ignored` sólo cuando no hubo ninguna acción.
23. P1.15: `initialize_knowledge_base` falla visible si el CKG no se sembró y
    `prepare` usa el layout configurado (no un join hardcodeado).
24. P1.16: una escritura anterior al primer `session_context` deja deuda; abrir
    o vincular la sesión fija el baseline y no lo refresca.
25. P1.17: un side effect que no se aplicó (spec que no se movió, sprint sin
    servicio) aparece en el resultado del submit con su motivo, o falla.
26. P1.18: ese skip es además un evento del trace (`side_effect_skipped`) y el
    reporte lo cuenta por tipo de efecto, sin depender de la respuesta.

---

## 27. Riesgos y compatibilidad

- **Consumidores del CLI:** el marcador es una segunda línea de stdout. Los
  consumidores que parsean `json.loads(stdout)` completo deben leer la primera
  línea; se documenta en el contrato CLI y se agrega un test.
- **CLI viejo:** sin marcador, el parser legacy sigue funcionando sin pipe. No
  se rompe el camino actual.
- **Pipes legítimos:** el agente puede seguir usando `| tail` para leer; lo que
  no puede es certificar por el exit code del pipe.
- **`session_state`:** es append-only en la traza; no reemplaza `session_metrics`
  ni el delta. El costo es una escritura por transición, no por edit.

---

## 28. Tests

- `agent-plugins/walker-flight-control/gate.test.mjs`: `closeclimark`,
  `closeclipipefail`, `closeclimarkpipe`.
- `tests/test_cli_task_bdd.py`: marcador de `run_tests`/`verify_predicates` y
  contrato de salida (JSON + línea final).
- `tests/test_session_context_flight_state_bdd.py`: proyección `flight_state`.
- `tests/test_workflow_contract_bdd.py`: refs `NODE.*` y acciones con handler.
- `tests/test_delta_write_retry_bdd.py`: conflicto de generación, reintento y
  `DeltaWriteError` (P0.4).
- `agent-plugins/walker-flight-control/suite.mjs`: suite del harness (todos los
  modos en procesos limpios, `--fast` omite los de CLI real).
- `gate.test.mjs` modos nuevos: `reloadclosedirty`/`reloadcloseverified`
  (hidratación en el cierre) y `shelltmp` (redirect a /tmp no invalida).
- `tests/test_skill_token_bdd.py`: token por entrada y fallback legacy.
- `tests/test_memory_health_bdd.py`: health en preflight/agent_context y
  `cyclo.partial`.
- `gate.test.mjs` modo `degraded`: CKG vacío ⇒ no cita `adr-999`.
- `tests/test_workflow_contract_bdd.py`: prompt→schema bloqueante (P0.3b).
- `tests/test_skill_token_bdd.py` y `tests/test_capture_side_effects_bdd.py`:
  token por entrada y captura acoplada a las tools (P1.1/P1.3).
- `tests/test_project_init_config_bdd.py`: propuesta/apply de `project.json`
  sin pisar lo curado (P1.4).
- `tests/test_session_retention_bdd.py`: supersede por tarea, techo y
  `gather_context` sin superseded (P1.5).
- `tests/test_task_context_advance_bdd.py`: plan→spec, spec_status→plan, tarea
  inexistente visible y captura sin herencia (P1.6).
- `tests/test_harness_no_hardcoded_project_bdd.py`: el harness no nombra
  proyectos del usuario ni rutas absolutas; el proyecto entra por entorno
  (P1.7).
- `tests/test_cli_phase1_dispatch_bdd.py`: `prepare`/`list`/`status` se
  resuelven por composition desde el CLI (P1.7).
- `tests/test_run_tests_framework_bdd.py` y `tests/test_run_tests_green_bdd.py`:
  runner declarado (vitest/go/pytest), sin declaración falla, 0 tests en pytest
  y `test_pass` con error accionable (P1.8).
- `tests/test_language_imports_bdd.py`: adapter local/externo por lenguaje,
  relativos/alias JS, agregado checked/not_checked y Ca por resolución (P1.9).
- `gate.test.mjs` modos `autodeclare` (plan→tarea activa sin implement_task) y
  `scope` (negociación por archivo + plan ampliado) (P1.10); `extverify`
  (verificación por tool fuera del wrapper ⇒ 0 nudges) (P1.11).
- `tests/test_verification_trace_bdd.py`: evento de verificación, fail-closed
  sin sesión, contrato de `flight_state`, diff de escritura y merge en
  `session_context` (P1.11/P1.12).
- `tests/test_flight_control_metrics_bdd.py`: agregado del reporte, evidencia de
  escritura sin hook, `followedBy` y exclusión del harness (P1.11/P1.14).
- `tests/test_verification_trace_bdd.py`: snapshot por sesión y escritura sin
  flight state (P1.13).
- `tests/test_refine_project_init_actions_bdd.py`: `initialize_knowledge_base`
  fail-fast — CKG caído, sin nodo guardado y éxito con lo guardado (P1.15).
- `tests/test_fast_mcp_migration_bdd.py`: `prepare` con layout configurado
  (P1.15).
- `tests/test_verification_trace_bdd.py`: baseline idempotente que no se
  refresca, fijado por `session_open` y `session_bind` (P1.16).
- `tests/test_transform_spec_bdd.py`: `transform_spec` sin servicio (error),
  sin sprint (skip con motivo), sin `spec_id` (skip con motivo) y
  `update_spec -> None` (error) (P1.17).
- `tests/test_fast_mcp_workflow_application_bdd.py`: `side_effects` en el
  resultado del submit, clasificación de `_side_effect_notes` y el evento
  trazado con task/session (P1.17/P1.18).
- `tests/test_side_effects_graph_bdd.py`: `WorkflowEvent → CognitiveTracer →
  TraceGraph` con `data`/`task_id`/`session_id` (P1.18).
- `tests/test_flight_control_metrics_bdd.py`: conteo de `side_effect_skipped`
  y `side_effect_failed` por tipo (P1.18).
- `gate.test.mjs` modo `injectsuggest`: prueba las DOS direcciones — escribir
  deja `followed=true` por `edit` y una sesión que sólo recibe el plan y no
  actúa emite `workflow_suggestion_ignored` con `reason` (P1.14).
- `tests/test_task_context_advance_bdd.py`: `session_plan` registra la tarea
  aunque no tenga spec (P1.10).
- (P0.2) harness con re-`setup` del plugin y test de invalidación por escritura.
