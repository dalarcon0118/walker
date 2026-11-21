# Agent efficiency — estado actual y resultado esperado

- **Rol:** define la **superficie, el costo y la memoria** que el agente consume de
  Walker. No define política de gates: eso vive en
  [`FLIGHT-CONTROL.md`](FLIGHT-CONTROL.md) (FC-INV-01 sigue vigente). Este
  documento responde una sola pregunta: *¿por qué el agente no usa el poder de
  Walker para diseñar, analizar, verificar y comparar?*
- **Vigencia:** v1 · 2026-11-20. Medición base: sesión `ses_e0671430bffeSZNaMTNeT86gs8`
  sobre `walker` (OpenCode v2, MCP `WALKER_MCP_PROFILE=agent`).
- **Cómo leerlo:** §1 son hechos verificables (archivo:línea o comando con
  latencia medida); §2 es la definición de terminado por fase; §3 son invariantes
  con su detección; §4 son los escenarios de fallo; §5 es lo que **no** se sabe.
- **Regla de oro:** ninguna afirmación se completa de memoria. Cada una se puede
  reproducir con el comando que se indica al lado.
- **Caveat de medición:** §1.2 es **una muestra de una sesión**, no un benchmark.
  No hay p50/p95 por fase todavía (eso es F0). Se reportan los valores crudos y
  se marcan como tales.

---

## 1. Estado actual (contexto)

### 1.1 El agente no está desobedeciendo: está haciendo cuentas

El agente elige herramientas por valor esperado contra costo. Hoy, para cualquier
pregunta de diseño, `grep` + `read` cuesta ~10 ms y devuelve texto que ya está en
contexto; la alternativa Walker cuesta 0,7–3 s y devuelve JSON. Si además el
resultado es vacío, la política óptima del agente es **no volver a llamarla**.

Evidencia dura de que la memoria de decisiones no responde (comando al lado de
cada fila, §1.2): dos consultas en lenguaje natural a `agent_context` devolvieron
`ckg.results: []` en 2,2 s y 3,2 s. La consulta por archivo sí funciona (679 ms,
12.096 chars) pero entrega grafo crudo, no juicio.

**Consecuencia:** el agente usa Walker donde lo obligan (intake, cierre) y no
donde le convendría (diseñar, comparar, verificar temprano). El gate de intake
—que sí se respeta— es la prueba de que el problema no es obediencia: es precio y
momento.

### 1.2 Costo medido (una sesión, 2026-11-20)

Reproducible con `time ./bin/walker3 <proyecto>/<tool> '<payload>'`.

| Llamada | Latencia | Salida | Resultado |
| :-- | --: | --: | :-- |
| `status` (caliente) | 53 ms | 667 chars | sano |
| `agent_context {"file":"fast_mcp/features/workflow/application/orchestrator.py"}` | 679 ms | 12.096 chars (~3,0k tok) | CPG + CKG + delta; útil pero caro |
| `agent_context {"text":"flight control eficiencia adopcion agente"}` | 2.195 ms | 3.594 chars | `ckg: []`, `cpg.confidence: low` |
| `agent_context {"text":"ADR-083 gate de alcance flight control"}` | 3.167 ms | 818 chars | `ckg: []` — cero decisiones |
| `workflow_state` | 1.817 ms | 2.744 chars | `expected_payload` + 8 gates |
| `workflow_state` × 3 en paralelo | 3.515 ms total | — | **se serializa** (≈3× una llamada) |
| `workflow_state` (1ª invocación concurrente) | 877 ms | — | **`deadlock detected by _ModuleLock('fast_mcp.features.code_analysis.application.use_cases.check_cpg_health')`** |
| `search_memories {"project_id":…,"query":…}` | 218 ms | 367 chars | error: `Índice 'project_id' no registrado` |
| `run_tests` (suite completa) | >30 s | — | **timeout del cliente → el server muere (exit 1)** |
| `shell` (`echo probe`) sin sprint | — | — | **bloqueado** por gate de intake |

Lectura de la tabla:

1. **El lector de decisiones es el más caro y el único que devuelve vacío.** Es
   la peor combinación posible: máxima latencia, mínimo valor. Y la causa no es
   el índice: el store tiene **103 nodos** en `docs` (índice `ckg`; ojo:
   `memory_health` reporta 85, otra discrepancia) y `adr-083` está indexado. La
   causa es la **semántica de la consulta**: `match_expr`
   (`fast_mcp/shared_kernel/cognitive_graph/fts.py:231-266`) une los tokens con
   **AND estricto**; una consulta de 5 palabras exige las 5. Verificado: `storage`
   (un token) devuelve 5 resultados; `… gate de alcance flight control`
   (seis tokens) devuelve 0.
   Baseline del golden set de 20 preguntas naturales: **hit@5 = 0/20 (0%)**,
   latencia media 1.981 ms (`data/golden/baseline.json`). Con el fallback
   OR+bm25 de F2 (`search_fts(allow_or_fallback=True)` pedido por el CKG) y el
   filtro de nodos de sesión: **17/20 (85%)** a la misma latencia
   (`data/golden/baseline-or.json`).
2. **El server no escala en paralelo.** Tres lecturas concurrentes tardan lo mismo
   que tres secuenciales. Un agente que hace fan-out paga N × latencia.
3. **La firma no es uniforme.** `agent_context` recibe `project_id`+`file|text`;
   `search_memories` recibe un mapa de índices. El agente acierta la firma en la
   primera y falla en la segunda, con un error que nombra índices en vez de dar
   el ejemplo exacto. Y `session_bind` **acepta `goal` y lo ignora en silencio**:
   el sprint se creó con goal `sesión ses_e0671430…` (debería ser un error, no un
   default). Devuelve además `project_id: ""`.
4. **Una lectura paga peaje.** Sin sprint, `echo probe` queda bloqueado
   (`index.js:60`, `MUTATING_TOOLS` incluye `shell`; `index.js:1537-1541`, ancla
   `$`). El gate de intake cubre escrituras y también lecturas.

### 1.3 La maquinaria se enciende tarde y se apaga en consultas

- El plugin se declara explícitamente fuera de las consultas: *"Si es una
  consulta, una revisión o un cambio ad-hoc, ignorá este mensaje"*
  (`index.js:207-217`). Es decir: en diseño y análisis, Walker queda apagado por
  diseño. La ventaja que se busca (diseñar/analizar/comparar) es exactamente el
  caso que el plugin excluye.
- Todo el control ocurre en la **escritura** (`MUTATING_TOOLS`, `index.js:60`).
  Verificar al final no cambia el diseño: lo audita.
- Lo que sí se inyecta es un bloque de plan de ≤400 chars (FC-INV-13,
  `FLIGHT-CONTROL.md:368`) y un bootstrap de una línea. Es un recordatorio, no un
  brief de diseño.

### 1.4 El artefacto de diseño existe, pero está al final de un túnel de 8 turnos

`data/workflow/nodes/design.node.json` sí codifica la habilidad: OBSERVE separa
hechos de inferencias (`:95`), ANALYZE fuerza q1–q3 (`:234`), REVIEW pide
ADR-lite con **alternativas y consecuencias** (`:382`, `:459-467`), CKG_SAVE
persiste (`:488`).

Para llegar a REVIEW hay que atravesar `DESIGN_GATHER → OBSERVE → ANALYZE`, con
4 `submit` (schemas de `minLength: 10`, campos requeridos) y 4 `transition`, a
1–2 s por llamada, y con un CKG que aporta 85 nodos. El valor está al final; el
peaje, al principio. Ningún prompt arregla esa economía.

### 1.5 La superficie creció sin control (drift)

- `docs/OPENCODE-TUNING.md:16` fija el objetivo del perfil `agent`: **16 tools /
  5.573 chars** (−63%).
- Hoy `fast_mcp/tool_profiles.py:33-71` expone **23 tools / 10.314 chars
  (~2.578 tokens)**, que es **49,9%** de las 46 tools / 20.646 chars del perfil
  `full`. Reproducible con
  `WALKER_MCP_PROFILE=agent python3 -c "…build_server()…"` (ver
  `tests/test_tool_profiles_bdd.py:77-86`, que exige `<50%`).
  *Actualización 2026-11-20:* el número de arriba es el **baseline medido**. El
  código ya expone **8 tools**, pero un proceso `walker-mcp` arrancado antes del
  cambio sigue sirviendo las 23 (§7.1ter): la medición de superficie debe
  hacerse contra el proceso vivo, no sólo contra el código.
- Es decir: la superficie **casi duplicó** respecto del objetivo y quedó pegada al
  umbral del test. Las 7 tools que volvieron entraron con justificación individual
  válida (`implement_task` "había quedado fuera"), pero el agregado no se volvió a
  medir: el docstring del módulo (`tool_profiles.py:3-7`) sigue hablando de 33
  tools y ~3.500 tokens.
- Cada tool extra es una decisión que el modelo paga en cada request, y son 23
  nombres solapados (`agent_context`/`preflight`/`snapshot`/`baseline_diff`/
  `status`/`workflow_state`/`search_memories`).

### 1.6 La capa que gobierna no está analizada por su propio analizador

- `memory_health.cyclo.by_language` reporta `.js: {known: 0, total: 150}` y
  `.mjs: {known: 0, total: 8}`.
- El plugin de gobierno son ~2.100 líneas de JS (`FLIGHT-CONTROL.md:21`). El CPG
  no le calcula complejidad a ninguno de sus archivos: la política que decide
  sobre el agente es invisible para el grafo que analiza el agente.
- Además, los seams de OpenCode v2 no tienen tipos (`FLIGHT-CONTROL.md:34-37`):
  un hook mal escrito se descubre en runtime. Eso ya produjo los falsos positivos
  de §1.7.

### 1.7 La fricción ya está medida y no compra nada

De `FLIGHT-CONTROL.md:76-92` (reproducible con
`python3 scripts/flight_control_metrics.py walker --json`):

- 34 denials en 4 sesiones; 27 intentos fuera de alcance, **0 verdaderos
  positivos**; costo evitado estimado: ~54 turnos.
- 255 `close_blocked` históricos: la fricción dominante es el nudge de cierre.
- 13 denials `bind/shell` **sin el comando registrado**: no se puede demostrar
  que fueran read-only.

Un gate que sólo produce falsos positivos y no guarda evidencia no enseña: cobra.

### 1.8 Concurrencia: el sustrato no soporta el patrón del agente

El deadlock de §1.2 apareció al llamar `status` + `agent_context` +
`workflow_state` en paralelo, en la primera invocación del proceso. El mensaje es
`deadlock detected by _ModuleLock(...check_cpg_health)`: contiene un import
concurrente, no un lock del dominio (`check_cpg_health.py` no tiene locks). Y aun
sin deadlock, el server serializa (§1.2). Un agente que paraleliza lecturas —el
patrón normal de OpenCode— no puede apoyarse en este backend.

### 1.9 El cierre se autodestruye: la tool que certifica mata el server

Reproducido en esta sesión, con el log como evidencia
(`~/.local/share/opencode/log/opencode.log:147578-147581`):

1. El gate de cierre manda a correr `run_tests`. La suite completa tarda más que
   el `timeout: 30000` que el cliente tiene configurado para walker
   (`~/.config/opencode/opencode.json:19`).
2. A los 30 s el cliente cancela: `failed to call MCP tool server=walker
   error="Request timed out"`.
3. El handler del server **termina igual** e intenta responder:
   `AssertionError: Request already responded to` (`mcp/shared/session.py:129`,
   `assert not self._completed`).
4. La excepción no la atrapa nadie: el proceso sale con **code 1** y OpenCode
   retira walker del catálogo de tools para el resto de la sesión.

Consecuencia: el gate de cierre manda a correr `run_tests`; `run_tests` mata a
walker; sin walker no hay `verify_predicates`, ni `capture`, ni cierre. No es
lentitud: es un deadlock del workflow. Y no requiere un caso raro — cualquier
suite de más de 30 s lo dispara, es decir, **el camino de cierre está roto por
construcción** en este repo: `python3 -m pytest -q` da **1.515 passed en
37,14 s**, siempre por encima del timeout del cliente.

---

## 2. Resultado esperado (cómo debe quedar)

Al terminar, el sistema cumple **todo** lo siguiente. Lo que no se pueda cumplir
no se implementa: se documenta acá y se vuelve a decidir. Cada fase se commitea y
declara verde su checklist; sin métricas verdes no se pasa a la siguiente.

### 2.1 Una llamada barata en el momento correcto

El agente debe poder resolver el contexto de una fase con **una** llamada:

```jsonc
// brief(project_id, file|task|text, mode) → respuesta compuesta, ≤1.200 tokens
{
  "anchor":   { "type": "file", "value": "…", "resolved": true },
  "decisions": [ { "id": "adr-083", "title": "…", "state": "aceptado",
                   "why": "…", "supersedes": ["adr-081","adr-082"] } ],
  "impact":   { "callers": 12, "tests": ["…"], "risk": "…" },
  "gates":    { "sprint": "…", "task": "…", "scope": ["…"] },
  "missing":  [],                     // lo que no se pudo leer, explícito
  "degraded": false
}
```

- Reemplaza a `agent_context` + `preflight` + `workflow_state` en el camino del
  agente. Las tools de lectura no se duplican (§3 AE-INV-01).
- `missing` y `degraded` son obligatorios: una respuesta parcial nunca se
  presenta como completa.
- p95 caliente ≤400 ms; salida ≤1.200 tokens. Si no cumple, no entra al perfil.

### 2.2 La memoria de decisiones existe y se recupera

- Toda decisión (ADR/spec/decisión de sprint) se indexa con `supersedes` y
  estado. Ninguna decisión retirada queda activa (FC-INV-14 sigue vigente).
- `brief(text=…)` devuelve top-5 decisiones con razón y estado, no eventos
  agregados. Un ADR indexado no puede devolver vacío.
- Golden set congelado de 20 consultas (pregunta → decisión esperada) que corre
  en CI; sin él no se declara verde.

### 2.3 El artefacto de diseño es la unidad de trabajo

El gate deja de validar la virtud del agente y pasa a validar la **forma del
artefacto**:

```jsonc
// design_brief — lo que se exige antes de la primera escritura de una tarea
{
  "problem": "…",
  "options": [
    { "id": "A", "summary": "…", "cost": "…", "risk": "…" },
    { "id": "B", "summary": "…", "cost": "…", "risk": "…" }
  ],
  "chosen": "A",
  "rejected_because": { "B": "…" },       // obligatorio, no vacío
  "invariants_touched": ["FC-INV-03", "INV-007"],
  "verification": [ { "criterion": "…", "check": "test|command", "ref": "…" } ]
}
```

- Es un objeto, no 8 submits: una llamada, un gate.
- En una **consulta** read-only no se exige (ver §4, escenario 5).

### 2.4 Comparar es una primitiva

- `baseline_diff` ya compara código (A/B). Falta lo mismo para el plan:
  `plan_diff(session)` → qué cambió desde A, qué criterio de aceptación sigue sin
  prueba, qué archivo se amplió y por qué.
- El cierre exige `verification` completa: ningún criterio sin `check` verde.

### 2.5 Leer nunca paga peaje y el sustrato escala

- Read-only (`pwd`, `ls`, `cat`, `rg`, `git status/diff/log`, `--help`) → `allow`
  en cualquier estado (ya decidido en FC-INV-04; acá es criterio de verde).
- 4 llamadas concurrentes ≤1,5× una sola; 0 deadlocks en 100 llamadas paralelas.

### 2.6 Superficie mínima y adopción medida

- Perfil `agent` ≤8 tools y ≤2.500 chars de esquema. Test de drift que falla si
  el conteo cambia sin actualizar este documento.
- Se publica por sesión: ratio llamadas Walker/nativas, tokens gastados en salida
  Walker, rework rate, tests verdes al primer intento. Sin adopción medida no hay
  "verde" (hoy se mide cuánto bloquea, no cuánto ayuda).

### 2.7 Orden de trabajo y definición de terminado

1. **F0 · Observar (sin cambiar comportamiento).** Instrumentar cada llamada a
   tools Walker desde el agente: tool, latencia, tokens de salida, resultado
   (`hit|empty|error`), firma inválida. Congelar el golden set de 20 consultas.
   - [ ] M-02 Los tres lectores de contexto (`agent_context`, `preflight`,
     `workflow_state`) tienen p50/p95 y tasa de `empty` publicados — anti-patrón a
     evitar: **Magic Numbers** (reportar latencias sin n ni percentil).
   - [ ] M-04 Cada llamada emite evento con `tool`, `latency_ms`, `out_tokens`,
     `outcome` — anti-patrón a evitar: **Eventual Consistency sin safeguards**
     (medir por muestreo y llamarlo total).
   - [ ] M-06 El golden set y el perfil viven en archivos versionados, no en
     constantes del código — anti-patrón a evitar: **Accidental Complexity**.
   - Verde: métricas en el trace del proyecto y reporte reproducible; 0 cambios de
     política en esta fase.

2. **F1 · Sustrato.** Eliminar el deadlock de import concurrente, dejar de
   serializar lecturas (daemon o pool), invalidar caché por `mtime`, y hacer
   uniforme la firma (`project_id` siempre; error con ejemplo ejecutable).
   - [ ] M-05 100 llamadas concurrentes en 10 lotes: 0 deadlocks, 0 timeouts —
     anti-patrón a evitar: **God Class** (un lock global que serializa todo).
   - [ ] M-02 p95 caliente ≤400 ms y caché invalidada por cambio en disco —
     anti-patrón a evitar: **Hidden Dependency** (estado caliente que no ve el
     cambio de workflow).
   - [ ] M-03 Una firma inválida devuelve el payload exacto que funciona, no la
     lista de índices — anti-patrón a evitar: **Boolean Trap** (parámetros
     posicionales/ambiguos).
   - [ ] M-04 Una tool que excede el timeout del cliente no mata el server:
     devuelve `job_id` o error tipado y el proceso sigue vivo — anti-patrón a
     evitar: **Hidden Dependency** (el cierre depende de un timeout configurado
     en otro archivo).
   - Verde: los tres escenarios de §1.2 repetidos con los nuevos números.

3. **F2 · Memoria de decisiones.** Indexar ADRs y specs con `supersedes`;
   retrieval por texto con top-5; `brief` compuesto.
   - [ ] M-03 Ningún nodo activo cita una decisión retirada; `supersedes` no
     vacío cuando corresponde — anti-patrón a evitar: **Anemic Domain Model**
     (nodo con id y texto, sin ciclo de vida).
   - [ ] M-02 90% del golden set devuelve la decisión esperada en top-5 —
     anti-patrón a evitar: **Shotgun Surgery** (arreglar el retrieval por query).
   - [ ] M-01 `brief` es atómico: o devuelve todas las secciones o marca las
     faltantes en `missing` — anti-patrón a evitar: **Transaction Script**
     (tres lecturas encadenadas sin resultado único).
   - Verde: `brief(text=…)` devuelve ≥1 decisión relevante en <1 s.

4. **F3 · Diseño y comparación.** `design_brief` como artefacto, `plan_diff`,
   perfil ≤8 tools, medición de adopción.
   - [ ] M-06 El gate valida el schema del `design_brief` (≥2 opciones, descartada
     con motivo, invariantes, verificación) — anti-patrón a evitar: **Not
     Invented Here** (validar con regex ad-hoc en el plugin).
   - [ ] M-02 Una consulta de diseño se resuelve con ≤2 llamadas Walker y
     ≤1.200 tokens — anti-patrón a evitar: **Magic Numbers** (presupuesto de
     tokens sin medición).
   - [ ] M-04 `plan_diff` publica criterios sin prueba y ampliaciones de scope —
     anti-patrón a evitar: **Distributed Big Ball of Mud** (estado del plan en
     tres lugares que no coinciden).
   - Verde: ratio Walker/nativas ≥1 en sesiones de implementación; 0 criterios de
     aceptación sin verificación al cerrar; perfil estable con test de drift.

---

## 3. Reglas que no pueden violarse (invariantes)

Cada regla trae cómo se detecta su violación.

- **AE-INV-01 · Un lector.** El contexto de una fase se resuelve con `brief`. No
  se agregan lectores nuevos sin retirar uno.
  *Detección:* test de completitud del perfil; conteo de tools de lectura ≤1.
- **AE-INV-02 · Leer no paga peaje.** Ninguna lectura (tool o shell read-only)
  exige sprint, tarea o scope.
  *Detección:* fixtures `pwd|ls|cat|rg|git status` en estado sin bind → allow.
- **AE-INV-03 · Firma uniforme.** `project_id` en todas las tools de agente;
  ningún mapa de índices como payload; todo error de firma trae ejemplo
  ejecutable.
  *Detección:* test de contrato que invoca cada tool con la firma canónica.
- **AE-INV-04 · Memoria con vida.** `supersedes` obligatorio; ninguna decisión
  retirada activa; todo ADR indexado es recuperable por texto.
  *Detección:* golden set + consulta de nodos activos que citan retiradas.
- **AE-INV-05 · El precio es contrato.** p95 caliente ≤400 ms y salida ≤1.200
  tokens por llamada del camino del agente; si no cumple, no entra al perfil.
  *Detección:* benchmark en CI con umbrales; falla el build, no el agente.
- **AE-INV-06 · El artefacto manda.** El gate valida la forma del `design_brief`,
  nunca "que el agente haya pensado". Un deny sin `remedy` es un bug (FC-INV-07).
  *Detección:* fixtures de `design_brief` incompleto → deny con remedio.
- **AE-INV-07 · Concurrencia.** Lecturas concurrentes no se serializan ni
  deadlockean.
  *Detección:* 100 llamadas en 10 lotes; comparación 1 vs 4 concurrentes ≤1,5×.
- **AE-INV-08 · Adopción medida.** Ningún cambio de superficie se declara verde
  sin ratio Walker/nativas y tokens de salida publicados.
  *Detección:* reporte por sesión en el trace; ausencia = rojo.
- **AE-INV-09 · Cancelación no mata.** Una tool cancelada por el cliente no
  responde dos veces ni termina el proceso; una tool larga devuelve `job_id` o
  error tipado y walker sigue disponible.
  *Detección:* test que cancela a mitad y luego invoca `status`; el proceso
  sobrevive y responde.

---

## 4. Escenarios de fallo

1. **`brief` compuesto con una fuente caída devuelve parcial silencioso.**
   *Causa:* se agregan CPG + CKG + delta y un fallo se traga por `try/except`.
   *Mitigación:* `missing[]` y `degraded` obligatorios; nunca 200 con secciones
   vacías sin marca.
   *Detección:* test con CKG caído (puerto que lanza) → `degraded: true` y
   `missing: ["decisions"]`.

2. **El retrieval mejora en la demo y empeora en el corpus.**
   *Causa:* se optimiza por la consulta que se está mirando (embeddings nuevos,
   sin conjunto de referencia).
   *Mitigación:* golden set de 20 consultas congelado y versionado; se cambia
   sólo con evidencia.
   *Detección:* benchmark en CI; regresión de top-5 bloquea el merge.

3. **El perfil se recorta y los prompts siguen mandando a una tool removida.**
   *Causa:* ya pasó con `search_memories` (`tool_profiles.py:42-45`): el agente
   recibía "Unknown tool" desde el template del workflow.
   *Mitigación:* test de completitud que cruza los `prompt_id`/templates del
   workflow contra `AGENT`.
   *Detección:* test que falla si un template nombra una tool fuera del perfil.

4. **El estado caliente del daemon queda viejo cuando cambia el workflow.**
   *Causa:* caché in-process sin invalidación (ya pasó con `_SERVER`).
   *Mitigación:* invalidación por `mtime` + reloader; el estado se lee con firma
   de versión.
   *Detección:* test con `tmp_path` que edita el nodo y verifica la recarga.

5. **El gate de diseño convierte una consulta en un trámite.**
   *Causa:* exigir `design_brief` sin distinguir lectura de escritura; el agente
   inventa un brief para poder seguir.
   *Mitigación:* el artefacto se exige sólo cuando hay escritura; en read-only,
   `allow` (AE-INV-02).
   *Detección:* corpus de consultas read-only con `design_brief` exigido = 0.

6. **El deadlock se "arregla" con un lock global.**
   *Causa:* el síntoma (corrupción) se ataca serializando.
   *Mitigación:* el fix es de import/estado por request, no un mutex compartido.
   *Detección:* AE-INV-07 falla si 4 concurrentes tardan ≥3× una sola.

7. **La suite tarda más que el timeout del cliente y el server muere.**
   *Causa:* handler síncrono que responde después de la cancelación
   (`assert not self._completed`); timeout del cliente (30 s) menor que el del
   server (300 s).
   *Mitigación:* capturar la cancelación y no responder dos veces; tools >5 s
   devuelven `job_id`; alinear los timeouts.
   *Detección:* test de cancelación + `run_tests` sobre una suite artificial de
   >30 s sin que el proceso muera (hoy: `pytest -q` = 37,14 s → muere).

---

## 5. Lo que NO se sabe (no inventar)

- **Causa exacta del deadlock.** El mensaje apunta a `_ModuleLock` sobre
  `check_cpg_health`, pero no hay traceback completo: puede ser import circular
  entre hilos o un import dentro de un `lru_cache`. Se resuelve en F1 con un
  stack dump, no por inferencia.
- **p50/p95 reales por fase.** §1.2 es una muestra; no hay percentiles ni n por
  tool. F0 los produce.
- **Cuánto del costo es arranque de proceso** (Python + imports) versus la
  consulta. La diferencia decide si alcanza con un daemon o hay que cambiar el
  motor de lectura.
- **Si el agente usa el `brief` cuando exista.** No está probado; se mide en F3
  con el ratio de adopción.
- **Tasa de falsos negativos de `shellMutation` en tiempo real** (sin corpus).
  El cierre no depende de eso: depende del diff del watcher (FC-INV-11).
- **Cuántas de las 13 denials `bind/shell` eran read-only:** el denial no guarda
  el comando (F0 de `FLIGHT-CONTROL.md` lo mide).

---

## 6. Relación con otros documentos

| Documento | Qué define | Qué **no** define |
| :-- | :-- | :-- |
| `FLIGHT-CONTROL.md` | política de gates, intake, cierre, invariantes FC-* | superficie ni costo de las tools |
| `OPENCODE-TUNING.md` | configuración del runtime (perfil, compactación) | memoria de decisiones ni artefactos |
| `AGENT-EFFICIENCY.md` (este) | superficie, costo, memoria y artefacto de diseño | política de gates |
| `FABLE-5.2-ALIGNMENT.md` | **hipótesis** sobre la economía de un modelo con razonamiento preservado | nada ejecutable: sus modelos no están en el catálogo (§8.1) |

Si este documento y `FLIGHT-CONTROL.md` se contradicen en política, gana
`FLIGHT-CONTROL.md` (FC-INV-01) y este documento se corrige en el mismo cambio.

---

## 7. Estado de implementación (2026-11-20)

Lo que ya está en el árbol, con la evidencia que lo respalda. Lo que no figura
acá sigue abierto.

| Ítem | Estado | Evidencia |
| :-- | :-- | :-- |
| Guard de cancelación (AE-INV-09) | **implementado** | `fast_mcp/shared_kernel/runtime_safety.py`; `tests/test_mcp_cancellation_bdd.py` (4 tests) |
| Timeouts alineados | **aplicado** | walker `timeout: 420000` en `~/.config/opencode/opencode.json` (era 30.000) |
| Telemetría por tool (F0) | **implementado** | `fast_mcp/shared_kernel/telemetry/` + `scripts/agent_metrics.py`; primera llamada medida: `status` 3 ms · 255 tok · `hit` |
| Golden set (F0) | **congelado y en la suite** | `data/golden/agent_queries.json`; baseline 0/20 en `baseline.json`, 18/20 en `baseline-f2.json`; `tests/test_golden_retrieval_bdd.py` falla bajo 17/20 |
| Causa del retrieval vacío | **corregida en F2** | AND estricto (`fts.py:match_expr`); OR+bm25 para texto libre (`match_mode=any`) con fallback cuando el AND devuelve `<5`; **cuerpo del ADR indexado** (`ingest_adrs.py`, era 800 chars de cabecera); golden set **18/20** (`baseline-f2.json`) |
| Firma uniforme | **implementada** | `search_memories`: `project_id`/paginación fuera del mapa de índices, `text`/`query` → `ckg.text`, error con ejemplo (`tests/test_memory_signature_bdd.py`); `preflight`/`snapshot`/`baseline_diff`/`session_context` declaran `project_id` — antes morían con "No project context" (`test_tool_profiles_bdd.py`) |
| Ranking por autoridad | **implementado** | ADR/`docs/` antes que memoria de sesión, con orden bm25 estable (`tests/test_ckg_retrieval_ranking_bdd.py`) |
| `run_tests` con entorno limpio | **implementado** | heredaba `WALKER_MCP_PROFILE=agent`: 14 tests del contrato `full` fallaban sólo desde la tool. `_suite_env()` los descarta (`tests/test_run_tests_env_bdd.py`) |
| Clasificador de escritura | **implementado** | `shell_mutation.mjs`: el **destino** decide (`cp x /tmp/y` no muta; `cp x src/y` sí). 25 casos en `node --test shell_mutation.test.mjs`; `index.js` sólo pasa `projectDir` |
| Registro de specs del sprint | **corregido** | `get_specs` pedía una página de 50: con 86 specs en el índice el sprint devolvía 0 aunque la spec existiera. Ahora `size=1000` (`tests/test_fast_mcp_delta_infrastructure_bdd.py`) |
| Grafo reproducible | **implementado** | `workflow-src/` (13 archivos versionados) + `GraphRepository` lo siembra si falta + `scripts/sync_workflow.py --check` (`tests/test_workflow_src_bdd.py`) |
| `supersedes` (M-03) | **implementado** | `ingest_adrs.py` parsea `**Consolida:** ADR-081 y ADR-082` y resuelve a ids en `build_nodes`; 081/082 quedan `superseded` en el CKG (`tests/test_ingest_adrs_bdd.py`) |
| Plan → spec | **parcial** | `implement_task` ya no pisa `flight_plan.acceptance` (merge). La spec todavía no hereda: `_persist_spec` devuelve `None` sin error (§7.1-6). |
| `brief` compuesto (F2) | **implementado** | una llamada: `decisions` (top-5 con `supersedes`), `impact` (reusa preflight), `gates`, **`step`** (absorbe `workflow_state`), **`skills`/`skill`** (absorbe `search_skills`/`load_skill`) y **`plan`** (absorbe `plan_diff`); `missing[]`/`degraded` obligatorios (`tests/test_brief_bdd.py`) |
| `plan_diff` (F3/M-04) | **implementado** | compara flight_plan ↔ spec: criterios en uno y no en el otro, scope ampliado y criterios sin predicado (`tests/test_plan_diff_bdd.py`) |
| Inyección de `brief` (F4.1) | **implementado** | El plugin inyecta el flight card (≤600 chars) en el primer turno con tarea **de la sesión**, en el primer `read` y tras un fallo de comando de verificación. Reemplaza `agent_context`+`preflight` (2 CLI) por **una** llamada que sí se inyecta — antes el bloque se calculaba y se descartaba (`rt.pendingBaseline = null`). Trazas `brief_injected` con `trigger:intake|read|failure`. `brief_block.test.mjs` (5) + harness 25/25 |
| Ancla por plan | **implementado** | si la spec no declara `task_context`, `brief(task_id)` toma tarea y alcance del plan de la sesión. Antes moría en `sin ancla`/`degraded` (medido: la inyección llegaba vacía) |
| Impacto cacheado (F4.5) | **implementado** | `_IMPACT_CACHE` por firma `(ruta, mtime_ns, tamaño)` + TTL 120 s; `impact.cached` es visible. Medido por archivo: **1.985 ms frío → 380 ms tibio** |
| `brief` reconcilia step ↔ plan | **implementado** | `consistency {ok, governs, reason, hint}`: con spec `completed` y workflow en `START.SELECT` declara que gobierna el plan y que no se avance. Antes el card afirmaba dos estados a la vez |
| Telemetría correlacionada (F4.3) | **implementado** | `session_id` y `task_id` en cada registro; `summarize_tasks` publica tareas, con brief, brief-primero y brief-antes-de-escribir (`tests/test_agent_adoption_bdd.py`, `tests/test_tool_metrics_bdd.py`) |
| `design_brief` (F3) | **implementado** | `DESIGN_ANALYZE` declara `x-artifact` y el contrato vive en código (`ARTIFACT_GATES`), así que no depende de `data/workflow`; valida forma (>=2 opciones, descarte con motivo, invariantes, verificación) y deniega con `missing[]`+`remedy` (`tests/test_design_brief_bdd.py`) |
| Perfil `agent` | **8 tools / 1.470 chars** | loop: `brief`, `search_memories`, `submit`, `advance`, `transition`, `implement_task`, `run_tests`, `capture`. Medido en proceso nuevo: **8 tools, 1.470 chars de schema, 1.974 con nombre+descripción**; los schemas se compactan (sin descripciones por propiedad) y el resto vive en `full`/`admin`. Cumple §2.6 (≤8 / ≤2.500). |
| Adopción medida | **N/D declarado, no estimado** | `scripts/agent_adoption.py` lee telemetría + trace + DB del harness. La DB de opencode escribe hasta **2026-09-04** (76,9 días): el `0,039` que se publicaba era de esa era. Ahora `read_harness` publica el conteo pero **`native_ratio: null`** con motivo si la última sesión supera 2 días (`tests/test_agent_adoption_bdd.py`). Con `session_id`/`task_id` en la telemetría, `summarize_tasks` mide tareas, brief-primero y brief-antes-de-escribir. |
| Superficie en ejecución | **verificada tras reinicio** | Un `walker-mcp` arrancado antes del cambio sirve 23 tools sin `brief` (medido 2026-11-20: PID 35258, 13:01, antes del commit de las 15:39). **Reiniciado y verificado:** `list_tools()` = 8 exactos y `brief` responde con `missing: []`. Queda pendiente el chequeo automático (`walker doctor`) para que esto no dependa de acordarse de reiniciar. |
| Sustrato (AE-INV-07) | **cerrado** | warm imports + caches por firma + **pool de 4 procesos** para lecturas (`fast_mcp/memories/read_pool.py`). `agent_context` caliente **169 ms**, 4 concurrentes **248 ms = 1,32×**; probe 100/100, **p50 110 / p95 219 ms**. Costo: la primera ráfaga paga el spawn de los workers (mitigado con warm en background) |

Verificación: `python3 -m pytest -q` → **1.584 passed** (`scripts/golden_queries.py`
da 18/20; `scripts/agent_metrics.py` sigue disponible). Plugin:
`node --test brief_block.test.mjs` (5) y `node suite.mjs --fast` (**25/25**).

### 7.2bis Inyección medida (F4.1)

El flight card inyectado en una sesión real: **561 chars** con `trigger:intake`;
tras el fallback de ancla por plan y el cache de impacto, el mismo pedido deja de
venir degradado (`missing: []`, 2+ decisiones, impacto y conflicto declarados).
Costo: **~1,6 s** la primera vez (preflight frío) y **380 ms** cuando el impacto
está cacheado; se paga una vez por sesión, no por turno.

`brief_injected` deja `trigger`, `task_id`, `chars`, `ms`, `decisions` y `cached`
en el trace: la inyección es medible, no una intención. El riesgo que se corrigió
en el camino: usar `state.data.taskId` (del proyecto) inyectaba el card de otra
tarea en sesiones nuevas — la tarea sale sólo de `planFor(sid)`.


### 7.1 Hallazgos nuevos durante la implementación (no corregidos)

Salieron de operar el sistema, no de leerlo. Quedan acá para que no se pierdan:

1. **El gate de alcance absorbió un path externo.** Al editar
   `~/.config/opencode/opencode.json`, el plugin hizo `scope_expanded` y lo sumó
   al plan. FC-INV-08 §2.4.2 dice que un path fuera del `projectDir` **se
   bloquea** con el remedio del proyecto correcto, no se absorbe. La
   verificación quedó con el path externo dentro del `scope`.
2. **La telemetría ya mide el escenario 3 del §4.** En la primera corrida real
   aparecieron `list` (5 llamadas) y `prepare` (2) con `outcome=invalid`
   (`Unknown tool`): son tools que el perfil `agent` retira y que algún camino
   todavía invoca. Antes era invisible; ahora está contado
   (`data/metrics/tool_calls.jsonl`).
3. **`spec_status` no acepta los artefactos donde el schema dice.** El handler
   los lee como **parámetros de primer nivel** (`completed_at`, `commit_hash`,
   `review_verdict`, `evidence`), pero el schema de la fase
   (`fast_mcp/phases/phase3_tasks.py:276-290`) no los declara; un
   `artifacts: {…}` anidado se ignora en silencio y el error sólo dice "missing
   required artifacts". Viola AE-INV-03 (firma uniforme).
4. **El plan y la spec divergen.** El `session_plan` tenía 4 criterios de
   aceptación; `implement_task` creó `SPEC-001` con
   `validation.errors: ["sin criterios de aceptación ni predicados
   ejecutables"]`. El plan no se propaga a la spec que se cierra.
5. **El cierre bloquea la documentación del cierre.** Al pasar el sprint a
   cerrado, el gate de intake vuelve a negar `edit`/`write`/`shell` hasta un
   `session_bind` nuevo: la sesión que acaba de cerrar no puede escribir su
   propio reporte. Es AE-INV-02 en su forma más cara (una escritura de
   documentación paga el peaje de un sprint nuevo).
6. **`_persist_spec` puede ser un no-op silencioso.** `implement_task` devuelve
   `ok` con `files_modified`, pero la spec no se escribe: `_persist_spec`
   retorna `None` cuando no resuelve `spring_id` y el caller no lo distingue de
   "no había nada que guardar". Medido 2026-11-20: `task_context.task_id` y
   `acceptance_criteria` quedaron nulos tras un `implement_task` exitoso. Un
   `Result` fallido, no `None`.

### 7.1bis Hallazgo: el workflow no está versionado

`data/workflow/` está en `.gitignore`, así que el grafo runtime no se reproducía
en un checkout limpio. Resuelto: `workflow-src/` versiona los 13 archivos,
`GraphRepository` los siembra cuando falta (sólo en el path canónico, para no
ocultar un error de resolución) y `scripts/sync_workflow.py --check` detecta
drift. El gate del `design_brief` además no depende del archivo: el contrato está
en `ARTIFACT_GATES` (código).

### 7.1ter Hallazgo: la superficie viva no es la del código

El MCP es un proceso de vida larga (`bin/walker-mcp`, stdio). Arrancó a las
13:01 del 2026-11-20; el perfil de 8 se commiteó a las 15:39. Resultado: el
agente siguió viendo **23 tools sin `brief`**, incluidas las cuatro que el perfil
retiró (`agent_context`, `preflight`, `snapshot`, `baseline_diff`). Cualquier
métrica de adopción tomada en esa ventana mide otra superficie.

Consecuencia de diseño: `apply_profile` corre en el `build_server()`, una sola
vez. No hay señal de "perfil viejo" en runtime. Remedio propuesto: `walker doctor`
que compare `server.list_tools()` con `WALKER_MCP_PROFILE` y falle con el
comando de reinicio; y que el plugin registre la superficie al iniciar sesión
(evento `tool_surface`), para que la telemetría sepa qué tools existían.

**Verificado tras reiniciar (2026-11-20):** `list_tools()` = 8 exactos
(`advance`, `brief`, `capture`, `implement_task`, `run_tests`, `search_memories`,
`submit`, `transition`) y `brief` responde en 3.563 chars con `missing: []`,
`degraded: false`, `plan.spec_status: completed` y 5 decisiones. El riesgo sigue
siendo sistémico: el chequeo automático está pendiente.


### 7.2 Resultado de F2 (retrieval + firma)

`python3 scripts/golden_queries.py` → **18/20 (90%)**, media 2.019 ms
(`data/golden/baseline-f2.json`). La mejora no vino de relajar el AND: vino de
indexar lo que se pregunta.

| Cambio | Efecto medido |
| :-- | :-- |
| `match_expr(mode="any")` + OR cuando el AND da `<5` | `brief(text=…)` deja de devolver decisiones vacías |
| Cuerpo del ADR indexado (8 KB, era 800 chars de cabecera) | q01 pasa de 0 a **rank 3**; q15 vuelve a rank 2 |
| Autoridad antes que memoria de sesión (orden bm25 estable) | `decision-001` ("proceed") deja de ser top-1 de q06 |
| Variantes morfológicas en la query (sólo `match_mode=any`) | `registry`→`registries`, `capas`→`capa`: q09 pasa de rank 10 a top-5 |
| `search_memories` sin `project_id` en el mapa | desaparece `Índice 'project_id' no registrado` |

Quedan **2 fallos, conocidos y no parcheados a propósito**:

- **q05** — "por qué el engine no puede importar infraestructura": el ADR-067
  escribe `infra`, la pregunta dice `infraestructura`. Es morfología, no
  ranking; se resuelve con stemming/embeddings, no con sinónimos ad-hoc.
- **q06** — "cómo se separan las tareas del workflow de las acciones": el
  ADR-065 está en inglés (`tasks`/`workflow actions`). Es brecha bilingüe.

Tocar el golden set para que pasen sería el escenario 2 de §4 (optimizar la
consulta que se está mirando). Se documentan como límite léxico.

**Por qué `brief` no entra todavía:** AE-INV-01 prohíbe sumar un lector sin
retirar otro. Agregarlo hoy sería la 24ª tool del perfil `agent` (ya en 23
contra un objetivo de 8) y pagaría esquema en cada request sin sacar
`agent_context`/`preflight`/`workflow_state`. Entra en F3, con el corte del
perfil y la migración de las 30 referencias a tools en prompts/workflow.

`failure_scenarios` de este cambio:

1. *El cuerpo indexado degrada la precisión por dilución de bm25.* Mitigación:
   el AND se conserva mientras devuelva ≥5; el OR sólo completa. Detección:
   golden set en CI (hoy 18/20).
2. *El índice queda desincronizado al re-ingerir.* Mitigación: ids
   determinísticos + `save_nodes` actualiza FTS en la misma transacción.
   Detección: `memory_health` y el marker `fts_config`.
3. *El payload crece con el corpus.* Mitigación: `_normalize_node` recorta a
   600 chars y declara `rationale_chars`. Detección:
   `tests/test_ckg_retrieval_ranking_bdd.py`.

---

## 8. Alineación con "modelos de razonamiento preservado" (F5)

- **Rol:** tomar [`FABLE-5.2-ALIGNMENT.md`](FABLE-5.2-ALIGNMENT.md) como
  **hipótesis sobre la economía del harness** y conservar sólo lo que se puede
  verificar contra este repo. No define política de gates (FC-INV-01) ni
  reemplaza §1–§7: los agrega como una fase más (F5).
- **Regla de admisión:** un principio entra a F5 sólo si (a) el mecanismo existe
  en este runtime, (b) su violación es medible con lo que ya tenemos, y (c) no
  agrega una tool ni un lector (AE-INV-01).

### 8.1 Qué del documento es verificable (y qué no)

| Afirmación del doc | Estado acá | Evidencia |
| :-- | :-- | :-- |
| "Fable 5.2 / Mythos 5.2 / Opus 5.5 / GPT-6 Astra / Gemini 4 Carbon", precios y benchmarks | **no verificable** | El catálogo de este OpenCode es `opencode-go/{qwen4-max, hy4-flash-free, longcat-2.5-free, iceman-contributor-free, kimi-k3.1-pro, minimax-m3.1-flash}`. Ningún número del doc se puede reproducir localmente, así que ninguno entra a una decisión. |
| Los bloques de razonamiento y el cache pagan por **prefijo** de la conversación | **verificable por mecánica** | No hace falta un modelo nuevo: cualquier proveedor con cache de prefijo cobra distinto si el encabezado cambia. El instrumento ya existe (abajo). |
| El harness reescribe el `system` por turno | **verificado en el plugin** | `agent-plugins/walker-flight-control/index.js:1019-1031`: cuatro bloques volátiles (`block`, `gate`, `pendingBrief`, `failureText`) se hacen `event.system.push(...)` y el hook corta con `return` cuando no hay ninguno. Dos requests consecutivos **no** mandan el mismo encabezado. |
| El runtime ofrece un seam para inyectar sin tocar el `system` | **verificado** | El hook `context` entrega `system`, `messages`, `tools` y `options` (docs V2, *Model requests*). `@opencode-ai/plugin` v2.3.2 expone además `experimental.chat.messages.transform` (`dist/index.d.ts:255`) y `experimental.chat.system.transform` (`:261`). El plugin usaba sólo `system`: la vía de mensaje existía y no se usaba. |
| "Effort" con cinco niveles por llamada | **verificado, pero con otro mecanismo** | Acá el nivel es una **variante de modelo del agente**, no un parámetro por llamada: `minimax-m3.1-flash [low,medium,high,xhigh,max]`, `kimi-k3.1-pro [low,high,max]`, `qwen4-max [none,low,high,xhigh]`. Walker no puede fijarlo por nodo; el plugin tampoco puede cambiarlo a mitad de sesión. |
| "El modelo corta antes de terminar" / "pide permiso de más" | **no medido acá** | Es comportamiento de un modelo que no está en el catálogo. Se puede medir con la telemetría de §7, no se asume. |
| La ceremonia de nodos cuesta más que el juicio | **ya medido** | §1.4: 4 `submit` + 4 `transition` a 1–2 s por llamada antes de llegar a REVIEW. Ese número es de este repo y sostiene P2 sin depender del modelo. |

### 8.2 Los cuatro choques, con estado real

**F5.1 · Inyección vs. prefijo estable — el único caro y sin dueño.**
El plugin inyecta en el `system` cuatro bloques que cambian por turno. El
`system` es el **encabezado** del prefijo: cambiarlo es el peor lugar para poner
un byte que cambia. Y no sólo invalida cache de prompt: si un proveedor exige
devolver los bloques de razonamiento atados al prefijo, el bloque se descarta o
la llamada falla, y el modelo vuelve a pensar desde cero. Costo real por turno:
`tokens` completos en vez de `cacheRead`.

Estado: **no medido**. El instrumento existe y ya corre:
`index.js:334-357` suma `cost` y `tokens{input,output,cacheRead}` de la sesión y
los publica en `session_metrics` (`:1127-1128`). Lo que falta es la serie por
turno; `scripts/flight_control_metrics.py` todavía no la reporta.

**F5.2 · Effort vs. ceremonia — mitad aplicable, mitad no.**
Aplicable: el nivel de esfuerzo se elige **por agente** (`model:
proveedor/modelo#variante` en el frontmatter), no por nodo. Un agente de
implementación y uno de exploración pueden correr a niveles distintos; hoy
`agents/build.md:3` fija `opencode/beam-free` sin variante.
No aplicable: que `brief` devuelva `effort` por nodo. Walker no controla el
request; devolver un nivel que nadie aplica es un dato muerto.
Lo que ya se puede hacer sin depender de nadie: **bajar la ceremonia** (§1.4) y
no poner coaching de esfuerzo en el prompt (verificado: no hay líneas de "pensá
menos" en `AGENTS.md` ni en `project.md`).

**F5.3 · Verificación de primera clase — ya está, falta el stamp.**
Ya implementado en §7: `design_brief` con `ARTIFACT_GATES` (forma, ≥2 opciones,
descarte con motivo), `plan_diff`, golden set en CI, guard de cancelación
(AE-INV-09), `run_tests` con entorno limpio. Lo que **no** existe: (a) el stamp
`tests|predicates|e2e|none` consultable desde el commit, (b) la regla de 3 rondas
→ humano, (c) que un check no pueda modificar sus propias reglas. Eso sí es
nuevo y es barato.

**F5.4 · Delegación — el tablero existe, la jerarquía no se usa.**
`parent_session_id` ya está en el dominio (`sprint_service.py:309`,
`entities.py:208`) y `subagent_depth: 2` está configurado
(`OPENCODE-TUNING.md:21`). Pero el plugin abre sesión con
`session_open {session_id, project_id, agent}` (`index.js:904-913`): no pasa el
padre, así que un subagente queda como raíz y su costo no se agrega a la tarea.
La lección del doc (el valor está en el tablero compartido, no en la cantidad de
agentes) acá se traduce en **una** cosa: que la jerarquía de sesiones sea
visible. No en más orquestación.

**F5.5 · Podar antes de agregar — el número es el prompt estático.**
`project.md` 30.044 chars + `AGENTS.md` 6.959 + `~/.config/opencode/AGENTS.md`
423 = **37.426 chars** de encabezado fijo, antes de tools y de cualquier
inyección. Es el costo por request más grande del harness y el que menos se
revisa. Recortarlo es una decisión de gobernanza (ADR + diff por línea), no una
limpieza.

### 8.3 Principios adoptados

- **P1 · Prefijo estable, contexto append-only (AE-INV-10).** Lo que cambia por
  turno va como **mensaje del turno** o, como mínimo, se repite byte-idéntico
  mientras la firma del nodo no cambie. El hook `context` recibe `system`,
  `messages` y `tools`: no hace falta el `experimental.chat.messages.transform`
  — se puede anexar al último turno del usuario en el mismo hook, sin reescribir
  historial (nunca al turno del assistant: sus bloques de razonamiento están
  firmados). Modos: `stable` (default, prefijo idéntico por firma), `once`
  (control: un envío por firma) y `messages` (opt-in; cae a `system` con
  `injection_fallback` trazado si la forma no se reconoce). *Aceptación:* 20
  turnos sin caída de `cacheRead/input`; salida ≤+10%. *Detección:*
  `token_turn.hit_rate` por turno.
- **P2 · Effort es una propiedad del agente, no del nodo (AE-INV-11).** Se elige
  variante en el frontmatter; no se pide "pensá menos" por prompt y no se muestra
  budget de tokens. Un `effort` que `brief` recomienda y nadie aplica es ruido.
  *Aceptación:* mismo gate-pass con ≤70% de tokens en tareas rutinarias.
  *Detección:* tokens por tarea en `summarize_tasks`.
- **P3 · "Done" es un predicado, no una afirmación (AE-INV-12).** Cierre =
  criterios con `check` verde + stamp en el commit; 3 rondas → humano; los checks
  leen de la rama base y no se auto-modifican. *Aceptación:* 0 cierres sin stamp;
  1er intento ≥70%. *Detección:* `plan_diff` ya publica criterios sin predicado.
- **P4 · Podar antes de agregar (AE-INV-13).** Ningún bloque nuevo de prompt o de
  inyección entra sin declarar qué sale. *Aceptación:* diff con justificación por
  línea y medición de salida. *Detección:* `wc -c` del encabezado en el reporte.

### 8.4 Cómo se mide (sin modelo nuevo)

El A/B es una sesión con el harness actual contra una con P1, sobre la misma
tarea, comparando `tokens.cacheRead / tokens.input`, tokens de salida, tiempo y
gate-pass. La serie por turno ya se publica (`token_turn.hit_rate`); el harness
tiene los dos lados del canal (`inject` = `stable`, `injectonce` = control), así
que la comparación no depende de acordarse de cambiar el env.

### 8.5 Lo que no se adopta

- La ceremonia de `submit`/`transition` como sustituto del juicio (§1.4 ya la
  mide como peaje).
- Coaching genérico ("sé riguroso", "pensá paso a paso"): ya está en el modelo y
  compite por atención con el plan.
- Límites duros de estilo y cautelas sin fecha de revisión: se obedecen para
  siempre y la prosa se rompe.
- Mostrar el budget de tokens en el prompt.
- Confiar en la verificación que el propio cambio produce (P3 la prohíbe).

### 8.6 failure_scenarios

1. **Mover la inyección a mensaje sin verificar el seam** → el bloque no llega
   o se duplica. Mitigación: feature flag + 20 turnos con proveedor real.
   Detección: trace `injection_mode` (`system|messages`) y `chars` por turno.
2. **Bajar la ceremonia de nodos y perder el gate de diseño** → specs vacías.
   Mitigación: `ARTIFACT_GATES` sigue validando forma. Detección: specs con <2
   opciones o sin descarte.
3. **Podar el encabezado y perder un invariante** → regresión silenciosa.
   Mitigación: diff por línea con justificación y revisión humana. Detección:
   reverts y `pytest -q` por invariante.
4. **Imputar al harness un costo que era del índice de memoria** → se poda lo
   que servía. Mitigación: el A/B cambia **una** variable. Detección: §7.2 ya
   mostró que la mejora vino de indexar, no de relajar el retrieval.
5. **Registrar jerarquía de subagentes sin tope de costo** → sesiones caras sin
   mejora. Mitigación: cap por sesión y `cost` por subagente trazado. Detección:
   `% de tokens en subagentes` por tarea.

### 8.7 Estado

| Ítem | Estado | Evidencia |
| :-- | :-- | :-- |
| P1a · prefijo estable (AE-INV-10) | **implementado** | `injection.mjs` (modos `stable`/`once`/`messages`) + memo por firma en `index.js`; modos `inject`/`injectonce` del harness prueban los dos caminos. Por defecto `stable`: el prefijo se repite byte-idéntico dentro del nodo. |
| P1b · canal de mensaje | **medido y descartado como default** | Probe real (`context_probe`, sesión `ses_e0581bc04ffeSFlM0Tldqa5Ojl`): `count=653`, `last_keys=["role","content"]`, `last_role="tool"`, `has_parts=false`, `target="no-anexable"`. En una continuación de tool el último turno es `tool`: anexar ahí rompe el par tool_use/tool_result, y anexar al turno del usuario (que quedó atrás) invalida todo lo posterior. El modo queda como opt-in y cae a `system` con `injection_fallback`. |
| P1c · telemetría por turno | **implementado, con punto ciego del proveedor** | `token_turn` con `input`/`output`/`cache_read`/`cache_write`/`reasoning`/`hit_rate`/`write_ratio`/`injection_mode`. Medido en esta sesión: `cache_read=0` en todos los turnos y `cost_usd=0`. Ese 0 **no prueba** que no haya cache: `opencode-go` no reporta `tokens.cache` ni costo, así que el A/B de P1 no se puede leer con este proveedor. Necesita uno que reporte `cache.read`. |
| P2 · effort por agente | **propuesto** | Variantes disponibles (`minimax-m3.1-flash [low..max]`); `build` sigue sin variante. No hay API por nodo. |
| P3a · sello de verificación (AE-INV-12) | **implementado** | `session_close` normaliza y persiste `stamp {kind, verified, level, weak, criteria, rounds, source, at}`; `session_context` lo publica. `kind=none` + `source=unstated` cuando falta: nunca se inventa un verde. |
| P3b · 3 rondas → humano | **implementado** | `entry.closeRounds` (no se resetea por ejecución) + `escalateBlock`; modo `closerounds` del harness: 1-2 insisten, 3 escala, 4 calla. |
| P3c · checks que no se auto-certifican | **implementado en `observe`** | `verify.py` extrae las reglas que EJECUTA cada predicado (`test_pass`/`command_exit`) y las compara con `git diff --name-only HEAD`: si están modificadas, el verde queda `weak` (y `strict=true` lo vuelve rojo). Un test NUEVO no cuenta como regla cambiada. `tests/test_verify_self_certified_bdd.py` (7). |
| F5.4 · jerarquía y costo por subagente | **implementado** | El plugin pasa `parent_session_id` desde `Session.Info.parentID` y persiste `cost_usd`/`tokens` en la sesión al cerrar; `session_list` ya devuelve `children`, así que el costo por subagente es un join, no un escaneo de traza. |
| P6 · drift instrumentado | **implementado** | `workflow_suggestion_ignored` lleva `suggested_next` (lo que pidió el motor) y `did` (lo que hizo el agente); `scripts/flight_control_metrics.py` imprime la tasa por semana ISO y los contadores por sugerencia/acción. Antes el evento traía `position` vacío: se contaba el desvío sin poder decir de qué. |
| P7 · poda de ceremonia | **implementado** | `driveEvidence`: un `llm_task` de `IMPLEMENT.*`/`VERIFY.*` avanza solo con `wrote` + verificación verde; los `user_input` no se cruzan. Medido antes: 3 `advance` en 1016 llamadas — el agente no caminaba la ceremonia. |
| P8 · paso visible y gate de decisión | **implementado** | El bloque contextual muestra `paso <posición> (<tipo>) · motor pide: <next_action>` (≤120, fuera del brief congelado) y una decisión abierta bloquea la escritura con `submit` (FC-INV-17). Antes el único paso visible era el del brief del intake, congelado; el `next_action` sólo iba a la traza. |
| P9 · revisión independiente para cerrar | **implementado** | `spec_status` acepta `reviewed_by`/`review_findings`; `close_spring` rechaza el cierre si alguna spec `completed` no tiene revisor distinto de la sesión que implementó (o si el autor se firmó a sí mismo). `force=true` + `reason` lo permite y deja la deuda en `context.closed_without_review`. |
| P10 · sello re-sellado y sin downgrade | **implementado** | `record_verification` re-sella una sesión activa (sólo hacia adelante; el rojo re-sella a `none`); `close_session` no pisa un verde guardado con un `none` que no trae `verified_at` (el plugin no vio ese verde). El sello deja de quedar congelado en el primer cierre. |
| P4 · poda del encabezado | **propuesto** | 37.426 chars medidos; requiere ADR + diff por línea. |

`WALKER_FLIGHT_CONTROL_INJECTION` (`stable`|`once`|`messages`) y
`WALKER_FLIGHT_CONTROL_CLOSE_ROUNDS` (default 3) son las dos palancas nuevas.
El A/B de P1 se lee en `token_turn.hit_rate`: mismo nodo con `once` pierde el
prefijo después del primer request; con `stable` lo conserva.

### 8.8 El peaje del workflow, medido

`scripts/node_path_cost.py` camina los nodos rellenando los campos que el propio
`workflow_state` declara (sin modelo: mide ceremonia, no juicio) y lo compara con
el camino que el agente usa de verdad.

| Camino | Llamadas | Tiempo | Qué se obtiene |
| :-- | --: | --: | :-- |
| `tools`: `session_bind` + `implement_task` + `brief` | 3 | ~1,9–2,4 s | tarea, criterios, alcance, decisiones top-5 |
| `nodos`: hasta `SPRINT.REVIEW` | 9 | ~4,7 s | todavía **nada de diseño** (no llegó a `DESIGN.*`) |
| `nodos`: una vuelta completa (vuelve a `START.LIST`) | 11 | ~6,2 s | ciclo, sin avanzar |

- Cada cruce de nodo son **dos** llamadas (`submit` + `transition`), ~500 ms cada
  una: sostiene el "4 submits + 4 transitions" del documento de referencia, y es
  la razón por la que el camino de diseño arranca después de ~8 llamadas.
- Medido con campos sintéticos el walk **cicla** (`START.LIST ⇄ SPRINT.REVIEW`):
  cruzar `SPRINT.REVIEW` exige contenido real (plan de sprint), es decir más
  ceremonia antes de llegar al diseño. No se reporta como bug: se reporta como
  el costo de tener el contenido detrás de la ceremonia.
- Dos defectos de contrato encontrados al caminarlo (corregidos):
  1. el enum de `decision` en `START.SELECT` era `select|create`, pero los gates
     reales son `project_selected|create_project|refresh`: seguir el enum deja
     el workflow trabado;
  2. el template manda `intent` y el motor lee `decision`: ahora `_extract_fields`
     los aliasa, así el payload **documentado** cruza el gate.
- `existing_work` (F5/P2): `build_prompt_extra` proyecta el plan de la **sesión**
  (no el del proyecto) al prompt, y el renderer lo pinta con
  `{existing_work_line}` en `start/select`. Antes el primer nodo preguntaba
  "elegí un proyecto" con la tarea ya declarada, y citaba la spec de otra tarea
  (`workflow_state` devolvía `SPEC-014` en una sesión que trabajaba en otra).

**Límite de medición del cache (2026-11-20).** En `opencode-go` los turnos reportan
`cache_read=0` y `cost_usd=0`: el proveedor no publica `tokens.cache` ni costo.
Sin ese dato, `hit_rate` no distingue "no hay cache" de "no se reporta", y el
A/B de P1 queda **no concluyente en este proveedor**. La instrumentación ya está
(no hay que tocarla): alcanza con correr la misma tarea en un modelo que reporte
`cache.read` para tener el número. Hasta entonces, `stable` se sostiene por el
argumento de forma (repetir bytes idénticos no puede invalidar más que
cambiarlos), no por un delta medido.




