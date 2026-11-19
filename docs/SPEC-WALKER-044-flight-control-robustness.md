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
reabrir sprints cerrados (ADR-WALKER-078) y el extractor de cyclo para JS.

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

## 6. failure_scenarios

```json
"failure_scenarios": [
  "Un pipe devuelve exit 0 con la suite roja y el plugin certifica por exitCode → causa: el exit code es del último comando del pipe → mitigación: con pipe sólo certifica WALKER_RESULT; sin marcador no se certifica → detección: modo closeclipipefail del harness + assert de nudge",
  "Un preview/truncado corta el JSON y no hay marcador → el plugin queda verified=false y el nudge se repite → causa: certificación depende de un payload mutable → mitigación: marcador final estable y regla 'sin marcador no hay verde' con mensaje accionable → detección: modo closeclimark (corte + marcador) y un test que verifique que un corte sin marcador no certifica",
  "Un reload del plugin pierde el Map y borra verified → el agente recibe el nudge aunque la suite esté verde → causa: estado de vuelo en memoria → mitigación: session_state en trace + flight_state en session_context + hidratación en setup → detección: harness con re-setup después de verificar",
  "Una escritura después de verificar deja verified=true → el cierre acepta cambios sin probar → causa: la mutación no invalida el verde → mitigación: verified=false + dirty_since en toda mutación → detección: test de escritura posterior a la certificación",
  "Dos sesiones o dos hooks concurrentes escriben flight_state y el último gana con un estado incoherente → causa: last-write-wins sin validación → mitigación: transiciones atómicas y `verified_at`/`dirty_since` consistentes → detección: test de dos transiciones intercaladas",
  "Otro proceso avanza la generación del delta entre la lectura y la escritura → el CAS rechaza el store y el repo descarta el ok:False → la spec/plan no queda en el delta y el gate de desvío opera con scope vacío (medido: implement_task ok sin spec, harness mode=scope en rojo) → mitigación: refrescar el diff-cache y reintentar una vez; si sigue fallando, DeltaWriteError visible → detección: test de dos servicios sobre el mismo data_dir + harness mode=scope",
  "El agente captura la salida de la verificación a /tmp (o usa 2>&1) y el detector de mutaciones lo cuenta como escritura → el verde recién obtenido se invalida y el cierre vuelve a nudgar (medido: `run_tests … > /tmp/out 2>&1`) → causa: el detector busca '>' sin mirar destino ni comillas → mitigación: shellMutation ignora /dev/null, fd dups, /tmp y `>` citados; sólo cuenta redirects dentro del proyecto → detección: harness mode=shelltmp (verde + redirect a /tmp ⇒ 0 nudges) y mode=shellmut (redirect al proyecto ⇒ mutación)"
]
```

---

## 7. Criterios de aceptación

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

---

## 8. Riesgos y compatibilidad

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

## 9. Tests

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
- (P0.2) harness con re-`setup` del plugin y test de invalidación por escritura.
