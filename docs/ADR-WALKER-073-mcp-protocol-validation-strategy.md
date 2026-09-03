# ADR-WALKER-073 — Validación del protocolo MCP: el agente como validador

## 0. Contexto

Tras ADR-WALKER-071, `fast_mcp/` sirve 28 tools MCP sobre FastMCP. La
pregunta crítica es: **¿cómo sabe el agente IA que el protocolo funciona
correctamente antes de usarlo en producción?**

La respuesta no es **solo** "pytest pasa" (pytest valida estructura; es
precondición necesaria, no suficiente). La respuesta es: **el agente mismo llama
las tools MCP de Walker, interpreta las respuestas, y valida que todo funciona** —
sobre la base verde de la conformancia (ver §1).

### Por qué el agente es el validador correcto

| pytest | Agente validador |
|--------|-----------------|
| Valida estructura JSON | Valida **significado** de la respuesta |
| Sabe qué esperar (hardcoded) | **Interpreta** respuestas inesperadas |
| Falla con AssertionError | **Razona** sobre el error y sugiere fix |
| Corre en CI | Corre **antes de cada sesión** de trabajo |
| No puede decidir | **Decide** si Walker está listo para usar |

### El flujo real

```
1. Agente necesita trabajar con el proyecto "walker"
2. El agente YA tiene una conexión MCP nativa a Walker (walker_* / memora_*)
3. Agente llama walker_status({"project_id": "walker"}) → Walker responde el estado
4. Agente **interpreta**: "proyecto sano, sin sprint activo (sprint: null)"
5. Agente decide: "puedo empezar a trabajar" (o pide sprint_planning si la task lo requiere)
6. Agente dispara la ejecución (submit/transition vía server stdio) → Walker ejecuta
7. Agente valida: "la task se completó correctamente"
```

Este ADR define **cómo el agente valida** que el paso 3-4 funciona correctamente
**antes** de confiar en él para trabajo real.

## 1. Decisión

> **⚠️ REVISIÓN 2026-09-02 (metodología):** este ADR se escribió cuando el agente
> "abría una conexión" con un cliente JSON-RPC a mano (`subprocess.Popen` +
> `mcp_call`). Ese método fue **abandonado como anti-patrón** (buffering frágil de
> pipes; duplica el protocolo que el agente ya habla nativamente — memoria 11842).
> La decisión vigente es **agent-as-validator en dos capas**:
>
> 1. **Conformancia de protocolo** (determinista, CI): el cliente MCP **oficial**
>    (`mcp.client.stdio` + `ClientSession`) ejercita initialize / tools/list /
>    tools/call contra `bin/walker-mcp` →
>    `tests/test_fast_mcp_stdio_validation_bdd.py`. Es la **precondición** de todo
>    veredicto.
> 2. **Sanidad semántica** (canary, pre-sesión): el agente llama las tools MCP ya
>    expuestas para él (`walker_list` / `walker_status` / `walker_prepare`,
>    `memora_*`) e **interpreta** las respuestas. No reemplaza a (1) y no valida
>    solo.
>
> Ningún PASS del agente vale si la capa 1 no está verde: el agente es canary,
> no oráculo del protocolo.

La validación en 5 fases se mantiene, con esa división de responsabilidades:

1. El agente **usa** su conexión MCP nativa a Walker (no escribe clientes)
2. El agente **llama** tools específicas
3. El agente **interpreta** las respuestas
4. El agente **valida** que las respuestas son correctas (semánticamente)
5. El agente **reporta** hallazgos en lenguaje natural — siempre sobre la base
   verde de la capa de conformancia

### 1.1 Cómo se conecta el agente

**Camino A — Superficie MCP nativa del agente (canary pre-sesión):**
El agente ya tiene servers MCP conectados. Llama directamente:

```
walker_list({})                          # registry de proyectos (data/projects.json)
walker_status({"project_id": "walker"})  # salud + sprint
walker_prepare(...)                      # idempotente (already_exists)
memora_memory_stats({})                  # memoria del agente
```

**Camino B — Cliente MCP oficial (conformancia de protocolo, CI):**
```python
from mcp.client.stdio import stdio_client
from mcp.client.session import ClientSession
# Implementado en tests/test_fast_mcp_stdio_validation_bdd.py:
# initialize → tools/list (completitud del catálogo) → tools/call status
```

> **Anti-patrón (NO hacer):** escribir un cliente MCP a mano (`subprocess.Popen`
> + JSON-RPC crudo por fd — el antiguo `tests/test_mcp_validation_live.py`).
> Frágil (buffering de pipes) y duplica el protocolo. Los snippets de subprocess
> de versiones anteriores de este ADR quedaron obsoletos por esta decisión.

### 1.2 Las 5 fases

```
Fase 0: Descubrimiento — Capa B (cliente oficial): handshake + catálogo
Fase 1: Cada tool individual — Capa B (contrato) + Capa A (semántica de las tools visibles al agente)
Fase 2: Workflow completo — Capa B (submit/transition vía server stdio, sprint desechable)
Fase 3: Errores — Capa B (códigos JSON-RPC) + Capa A (gracefulness observable)
Fase 4: Production readiness — Capa B (consistencia, reconexión) + Capa A (go/no-go)
```

### 1.3 Orden de dependencias

```
Fase 0 ──→ Fase 1 ──→ Fase 2 ──→ Fase 3 ──→ Fase 4
  │                                                      
  └── Si falla, NO continuar                           
```

Cada fase es **prerrequisito** de la siguiente. Si Fase 0 falla (el server no
responde al handshake, o el catálogo no cumple el contrato mínimo), no tiene
sentido continuar.

## 2. Diseño de cada fase

---

### Fase 0: Descubrimiento del server (Capa B — cliente oficial)

**Objetivo:** confirmar que el server arranca por stdio, negocía el protocolo y
expone el catálogo completo. Determinista — vive en CI
(`tests/test_fast_mcp_stdio_validation_bdd.py::test_stdio_handshake_y_tools_list`).

**Checks (patrón de completitud de AGENTS.md — NO count literal hardcodeado):**

| Check | Esperado | Cómo se valida |
|-------|----------|----------------|
| Handshake | `initialize` responde | `init.serverInfo.name == "walker"` (se valida el nombre del server, no el literal de versión de la librería) |
| Completitud del catálogo | `EXPECTED ⊆ tools/list` | Set esperado por contrato (F1: prepare/list/status + F2: memorias + F3: tasks); el total (28 hoy) se actualiza explícitamente al registrar una fase nueva |
| Schemas | cada tool tiene `inputSchema` | `type == "object"` |
| Descriptions | no vacías | por cada tool |

**Canary complementario (Capa A):** el agente verifica su superficie viva —
`walker_list` / `walker_status` / `walker_prepare` y `memora_*` responden.
(Evidencia 2026-09-02: 7 proyectos en `data/projects.json`; `walker_status` →
`sprint: null` graceful.)

**Si falla:** se bloquea el trabajo — la suite no pasa y el agente no continúa
con las demás fases.

---

### Fase 1: Cada tool individual (Capa B contrato + Capa A semántica)

**Objetivo:** cada tool del catálogo responde con params válidos y la respuesta
**tiene sentido**. La llamada mecánica es de la Capa B (cliente oficial,
`session.call_tool`); la interpretación semántica de las tools expuestas al
agente es de la Capa A (superficie nativa).

**Checklist por tool:**

1. Llama la tool con params válidos (Capa B; o superficie nativa si está expuesta)
2. Lee la respuesta
3. Valida:
   a. ¿El resultado tiene "content" con al menos un elemento?
   b. ¿El "text" del primer elemento es JSON válido?
   c. ¿El JSON tiene campo "ok" o "status" o "error"?
   d. ¿La respuesta tiene sentido para lo que pediste?

Ejemplo (Capa B) para "status":
  Llamas: session.call_tool("status", {"_project_id": "walker"})
  Esperas: JSON con datos del proyecto
  Validas: el "project_id" en la respuesta es "walker"

Ejemplo (Capa A) para walker_status:
  Llamas: walker_status({"project_id": "walker"})
  Esperas: health del proyecto
  Validas: interpretas "sprint: null" como "sin sprint activo" (graceful), no como error

Para cada tool reporta:
- ¿Funcionó? (sí/no)
- ¿Qué devolvió? (resumen en 1 línea)
- ¿La respuesta tiene sentido? (sí/no, por qué)
```

**Tabla de tools (contrato Capa B) y qué valida el agente (Capa A):**

> Nombres canónicos del catálogo actual (28: F1 prepare/list/status + F2 memorias
> + F3 tasks). Las tools de escritura se prueban **solo sobre proyecto desechable**
> (`validate-mcp-*`, con teardown en Fase 4) — nunca mutan el sprint del proyecto
> `walker`. Las tools marcadas ⏳ dependen del cableado de ejecución en producción
> (deferido: `phase3_tasks.py` / índice delta) y se validan hoy en la capa BDD.

| Tool | Params | Qué espera | Qué valida |
|------|--------|-----------|------------|
| `prepare` | proyecto desechable `validate-mcp-*` | proyecto creado | project_id no vacío; **teardown al final** |
| `list` | `{}` | proyectos | lista no vacía; incluye "walker" |
| `status` | `{"_project_id": "walker"}` | datos del proyecto | project_id coincide; sprint null = graceful |
| `sprint_planning` ⏳ | `{"_project_id": "validate-mcp-...", "goal": "..."}` | sprint creado | sprint_id no vacío (en proyecto desechable) |
| `resolve_project_sprint` ⏳ | proyecto desechable | chain_dir | chain_dir es path válido |
| `trace_push` ⏳ | proyecto desechable | ok | evento registrado |
| `implement_task` ⏳ | proyecto desechable | resultado | resultado presente |
| `run_tests` ⏳ | proyecto desechable | resultado | resultado presente |
| `extract_knowledge` / `knowledge_query` / `recall_knowledge` / `search_memories` | `{"_project_id": "walker", ...}` (lectura) | resultados | results es lista |
| `memory_schema` / `agent_context` | `{"_project_id": "walker"}` (lectura) | schema/context | válido/presente |
| `code_context` / `read_code` / `code_search` / `inspect_codebase` | `{"_project_id": "walker", ...}` (lectura) | código/info | contenido coherente con el repo |
| `build_code_index` / `index_repo` / `update_incremental_code` ⏳ | proyecto desechable | ok/stats | índice construido |
| `documenting_knowledge` | proyecto desechable | ok | doc persistida (teardown) |

> Si el nombre de una tool en esta tabla no coincide con `tools/list` del server,
> **la tabla está desactualizada** — corregir la tabla, no forzar la llamada.

**Validación semántica del agente (Capa A, sobre lo que el agente ve):**

- `walker_status` para "walker" → el agente espera datos del proyecto real, no un error
- `read_code` con "fast_mcp/main.py" → espera Python, no un traceback
- `walker_list` → espera el registry real (hoy: 7 proyectos), no una lista vacía inexplicable
- "sprint: null" → el agente lo interpreta como "sin sprint activo" (estado válido), no como fallo

**Si falla:** el agente documenta qué tool falló, por qué, y decide si es un bug
del server o un problema de params.

---

### Fase 2: Workflow E2E (Capa B — cliente oficial, sprint desechable)

**Objetivo:** demostrar el ciclo completo de trabajo real — crear sprint, enviar
workflow, avanzar estado, verificar — **sobre un proyecto desechable**
(`validate-mcp-*`), nunca mutando el sprint del proyecto `walker` (delta_tracking
es SSOT; INV del sistema). La mecánica es de la Capa B (`session.call_tool` a
través del server stdio); el agente interpreta la coherencia del ciclo.

> Precondición: las tools de ejecución (`submit`/`transition`) deben estar
> registradas en el server (deferido: `phase3_tasks.py`). Mientras no lo estén,
> esta fase queda cubierta por `tests/test_fast_mcp_workflow_application_bdd.py`
> (capa application/BDD) y se marca ⚠️ parcial en el reporte — como se hizo en
> `MCP_VALIDATION_REPORT.md`.

**Pasos (project = proyecto desechable):**

```
PASO 1: Crea un sprint
  session.call_tool("sprint_planning", {"_project_id": PROJECT, "goal": "Validación MCP E2E"})
  Guarda el sprint_id

PASO 2: Resuelve el chain_dir
  session.call_tool("resolve_project_sprint", {"_project_id": PROJECT})

PASO 3: Envía el workflow
  session.call_tool("submit", {"_project_id": PROJECT, "chain_dir": CHAIN_DIR})
  Verifica que la respuesta sea "ok"

PASO 4: Avanza el estado
  session.call_tool("transition", {..., "from_node": "PLANNING", "to_node": "ACTIVE"})
  Verifica "ok"

PASO 5: Verifica el estado
  session.call_tool("status", {"_project_id": PROJECT})
  Verifica current_sub_state == "ACTIVE"

PASO 6: Registra traza
  session.call_tool("trace_push", {"_project_id": PROJECT, "event_type": "e2e_validation_complete", "data": {}})
```

**Coherencia del ciclo (interpretación del agente):**

1. ¿El sprint_id del paso 1 aparece en el chain_dir del paso 2?
2. ¿El submit fue para el sprint correcto?
3. ¿La transition movió el estado como se esperaba?
4. ¿El status del paso 5 refleja la transition del paso 4?
5. ¿El trace del paso 6 se registró?

**Si falla:** el agente identifica **dónde** se rompió el ciclo y **por qué**
(p. ej., "la transition falló porque el sprint no estaba en el estado correcto").
Al terminar (ok o fallo): **teardown** del proyecto desechable.

---

### Fase 3: Errores (Capa B códigos JSON-RPC + Capa A gracefulness)

**Objetivo:** verificar que Walker maneja errores **sin crashear**, retorna
errores JSON-RPC válidos con códigos correctos, y sobrevive a errores repetidos.

**Pruebas (Capa B, `session.call_tool` / protocolo):**

| Prueba | Envía | Espera |
|--------|-------|--------|
| Tool inexistente | `session.call_tool("nonexistent_tool", {})` | Error de protocolo (tool no encontrada — MCP: `Unknown tool`) |
| Params faltantes | `session.call_tool("status", {})` | Error de validación (`-32602`), no traceback |
| Project inexistente | `session.call_tool("status", {"_project_id": "nonexistent_xyz"})` | "project not found" (no crash) |
| Chain_dir inválido | `session.call_tool("submit", {"chain_dir": "/this/does/not/exist", ...})` ⏳ | Error descriptivo (no traceback) |
| Post-error health | repetir la última llamada exitosa | Server sigue respondiendo (no corrupción de estado) |

**Checks:**

1. ¿Los errores de validación usan códigos JSON-RPC correctos (`-32602`, etc.)?
2. ¿Los mensajes son entendibles (no stack traces)?
3. ¿El server sobrevive a cada error y a 4 errores seguidos?

**Capa A (lo que el agente percibe):** después de provocar errores vía su
superficie nativa, `walker_status` sigue devolviendo salud coherente.

**Si falla:** el agente reporta qué error causó problema y sugiere fix
(p. ej., "el error de params faltantes retornó un traceback en vez de un
JSON-RPC error — falta try/except en el dispatch de tools").

---

### Fase 4: Production readiness (Capa B medible + Capa A go/no-go)

**Objetivo:** confirmar condiciones reales de uso — consistencia, reconexión,
estado tras errores — y emitir el veredicto **con evidencia**. Cierra con el
**teardown** del proyecto desechable.

**Pruebas (Capa B — el cliente oficial puede reconectar; el agente no gestiona
su propia conexión, por eso esto NO es prueba del agente):**

| Prueba | Qué valida | Criterio |
|--------|-----------|----------|
| Consistencia | 5x `session.call_tool("status", {"_project_id": "walker"})` | 5/5 equivalentes (mismo project_id/estado) |
| Reconexión | cerrar el `stdio_client` y abrir uno NUEVO; `initialize` + `status` | Nueva sesión funciona igual (server sin estado de sesión) |
| Estado post-errores | tras las pruebas de Fase 3, `status` del proyecto válido | Proyecto accesible (sin corrupción) |
| Teardown | eliminar el proyecto desechable `validate-mcp-*` | Registry sin basura de validación |

**Métricas de latencia:** NO se miden con el "reloj del agente" (no es un
oráculo de tiempo confiable — ningún "45ms promedio" narrado es evidencia). Si
se quiere medir, se instrumenta en la Capa B con el cliente oficial. La latencia
**no** es criterio de bloqueo de esta fase.

**Evidencia obligatoria:** el reporte adjunta las respuestas crudas (JSON) de
cada llamada. Un veredicto sin evidencia adjunta no es auditable y no cuenta.

**Veredicto (formato honesto, condicionado a la precondición):**

```
VEREDICTO: Walker MCP listo para trabajo del agente
Precondición — suite de conformancia (Capa B) en verde: SÍ/NO
- Superficie del agente (Capa A): walker_list / walker_status / walker_prepare OK, memora_* OK
- Consistencia: 5/5 equivalentes
- Reconexión: OK (nueva sesión stdio funcional)
- Estado post-errores: OK
- Teardown de proyecto desechable: OK
EVIDENCIA: respuestas crudas adjuntas (apéndice del reporte)
RECOMENDACIÓN: proceder a usar Walker para trabajo real.
```

O si hay problemas — con hallazgos concretos, sin números que el método no puede
producir:

```
VEREDICTO: Walker MCP tiene problemas
Precondición — suite de conformancia (Capa B) en verde: NO (test_stdio_call_status falla)
PROBLEMAS:
1. Consistencia: 3/5 — status divergió entre llamadas (posible estado compartido)
2. Reconexión: la nueva sesión no expone tools/list
EVIDENCIA: respuestas crudas adjuntas
RECOMENDACIÓN: corregir antes de usar en producción.
```

## 3. Cómo ejecuta el agente la validación

### 3.1 Conexión MCP — cliente oficial (Capa B)

**Prohibido** el cliente a mano (`subprocess.Popen` + JSON-RPC crudo —
anti-patrón abandonado, ver §1). La conexión automatizada usa el cliente MCP
oficial, exactamente como `tests/test_fast_mcp_stdio_validation_bdd.py`:

```python
import asyncio, os
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession

WALKER_ROOT = "/path/to/walker"

def _params():
    return StdioServerParameters(
        command="python3",
        args=[os.path.join(WALKER_ROOT, "bin", "walker-mcp")],
        cwd=WALKER_ROOT,
        env={**os.environ},
    )

async def fases_capa_b():
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            # Fase 0: handshake + catálogo
            init = await session.initialize()
            assert init.serverInfo.name == "walker"
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {"prepare", "list", "status"} <= names  # contrato mínimo
            # Fase 1/3: llamada y errores
            result = await session.call_tool("status", {"_project_id": "walker"})

asyncio.run(fases_capa_b())
```

La Capa A no abre conexiones: el agente llama sus tools nativas
(`walker_list` / `walker_status` / `walker_prepare`, `memora_*`) sobre los
servers ya conectados a su sesión.

### 3.2 Estructura del reporte

El agente genera un reporte en lenguaje natural **con evidencia adjunta**:

```markdown
# Validación MCP — {fecha}

## Precondición: Conformancia (Capa B) ✅
- tests/test_fast_mcp_stdio_validation_bdd.py: 2/2 passed
- Handshake: serverInfo.name == "walker"; catálogo: EXPECTED ⊆ tools/list

## Fase 0: Descubrimiento ✅
- Superficie nativa del agente: walker_list / walker_status / walker_prepare responden
- memora_* responde (memora_memory_stats, memora_memory_tags)

## Fase 1: Tools ✅
- Capa A: walker_list → 7 proyectos; walker_status → salud + sprint: null (graceful);
  walker_prepare → idempotente (already_exists)
- Capa B: status por stdio → project_id "walker" confirmado

## Fase 2: Workflow E2E ⚠️ parcial
- submit/transition cubiertos por capa BDD/application
  (tests/test_fast_mcp_workflow_application_bdd.py) — tools de ejecución
  pendientes de cablear en el server (deferido phase3_tasks.py)

## Fase 3: Errores ✅
- Params faltantes → error de validación, no traceback
- Server sobrevive errores repetidos (walker_status sigue coherente)

## Fase 4: Production readiness ✅
- Consistencia: 5/5 equivalentes; reconexión stdio OK (Capa B)
- Teardown de proyecto desechable: OK (registry sin basura)

## Veredicto: PASS (condicionado a precondición verde)
EVIDENCIA: apéndice con respuestas crudas (JSON) de cada llamada
```

Regla del reporte: sin números que el método no puede producir (nada de
"45ms promedio" narrado sin instrumentación Capa B) y con la cobertura marcada
honesta (⚠️ parcial donde la capa correspondiente no se ejercitó).

### 3.3 Invocación del agente

El agente puede ejecutar la validación de varias formas:

**Forma 1 — Prompt directo (canary Capa A + conformancia Capa B):**
```
Valida el protocolo MCP de Walker:
1. Ejecuta la suite de conformancia: python3 -m pytest tests/test_fast_mcp_stdio_validation_bdd.py -v
2. Con tus tools MCP nativas, llama walker_list({}) y walker_status({"project_id": "walker"}) e interpreta
3. Genera un reporte con las respuestas crudas adjuntas
```

**Forma 2 — Comando pytest (validación automatizada, Capa B):**
```bash
python3 -m pytest tests/test_fast_mcp_stdio_validation_bdd.py tests/test_fast_mcp_workflow_application_bdd.py -v
```

> `tests/test_mcp_validation_live.py` (cliente custom subprocess) está abandonado
> y pytest no colecciona nada de él (runner con `main()`); su destino pendiente
> de decisión: borrar o conservar solo como documento histórico.

**Forma 3 — Antes de cada sesión de trabajo (canary):**
```
Antes de empezar:
1. Confirma que la suite de conformancia (Capa B) está verde
2. Llama walker_status({"project_id": "walker"}) con tus tools nativas
3. Si ambas responden correctamente, procede
4. Si falla, reporta el problema y no continúes
```

## 4. failure_scenarios

```json
[
  {
    "fallo": "La conformancia de protocolo falla (Fase 0, Capa B)",
    "causa_raiz": "bin/walker-mcp no existe, dependencias faltantes, o el server no arranca por stdio",
    "mitigacion": "La suite (tests/test_fast_mcp_stdio_validation_bdd.py) falla en CI y bloquea el veredicto; se verifica el entry-point antes de intentar",
    "deteccion": "Fase 0: initialize no responde o tools/list no cubre el contrato mínimo (prepare/list/status)"
  },
  {
    "fallo": "Una tool retorna traceback en vez de JSON (Fase 1)",
    "causa_raiz": "Excepción no capturada en el handler de la tool",
    "mitigacion": "El agente reporta el traceback completo y sugiere agregar try/except en el handler",
    "deteccion": "Fase 1: respuesta no es JSON válido, contiene 'Traceback' o 'Error'"
  },
  {
    "fallo": "El workflow E2E no completa el ciclo (Fase 2)",
    "causa_raiz": "submit requiere sprint activo que no existe, o transition requiere estado incorrecto",
    "mitigacion": "El agente identifica qué paso falló y reporta la cadena de dependencias; el proyecto desechable se limpia igual (teardown)",
    "deteccion": "Fase 2: algún paso retorna 'ok: false' o error"
  },
  {
    "fallo": "El server crashea con errores (Fase 3)",
    "causa_raiz": "Error no manejado causa exit del proceso del server",
    "mitigacion": "La siguiente llamada sobre la misma sesión falla; se reinicia la sesión stdio y se reporta 'server crash'",
    "deteccion": "Fase 3: la sesión stdio se cierra o las llamadas fallan con error de transporte tras provocar el error"
  },
  {
    "fallo": "Las respuestas son inconsistentes (Fase 4)",
    "causa_raiz": "Estado compartido entre sesiones, o race condition en file-based storage",
    "mitigacion": "Se comparan 5 llamadas idénticas con evidencia adjunta y se reportan divergencias",
    "deteccion": "Fase 4: 2 de 5 llamadas a status retornan datos diferentes"
  },
  {
    "fallo": "La validación muta el SSOT del proyecto real (sprint de 'walker')",
    "causa_raiz": "Fases 1-2 ejecutan tools de escritura sobre el proyecto real en vez de un proyecto desechable",
    "mitigacion": "Proyecto desechable validate-mcp-* + teardown obligatorio en Fase 4; prohibición explícita en este ADR",
    "deteccion": "walker_status de 'walker' muestra un sprint distinto de null tras una validación; registry con proyectos de prueba sin limpiar"
  },
  {
    "fallo": "Un contribuidor reintroduce un cliente MCP a mano (anti-patrón abandonado)",
    "causa_raiz": "Los snippets históricos de subprocess + JSON-RPC crudo parecen 'el camino' si no se lee la decisión",
    "mitigacion": "§1 y §3.1 de este ADR lo declaran prohibido; el método vigente es cliente oficial (Capa B) o superficie nativa del agente (Capa A)",
    "deteccion": "Aparece un subprocess.Popen hacia bin/walker-mcp con lectura de fd a mano; PLAN y reporte lo marcan como ❌"
  }
]
```

## 5. Métricas de calidad por fase

> **Capa:** B = cliente oficial (determinista) · A = superficie nativa del agente
> (canary). Las métricas no ejecutables por una capa se declaran (AGENTS.md regla 4).

| Fase | Métrica | Criterio | Anti-patrón |
|------|---------|----------|------------|
| 0 | M-01 (B) | Handshake `initialize` responde, `serverInfo.name == "walker"` | Hardcodear respuesta o versión de librería |
| 0 | M-02 (B) | `EXPECTED ⊆ tools/list` — completitud por contrato (28 hoy, se actualiza explícitamente) | Contar tools con literal mágico |
| 0 | M-03 (B) | Conexión establecida < 5s (instrumentada por el cliente oficial) | Timeout excesivo sin reportar |
| 1 | M-04 (B+A) | 100% del catálogo responde (B) + toda la superficie nativa del agente OK (A) | Solo probar 3 tools |
| 1 | M-05 (B+A) | 0 tracebacks en respuestas | Ignorar errores |
| 1 | M-06 (—) | Latencia: NO APLICABLE como criterio del agente (el agente no es oráculo de tiempo); opcional e instrumentada en B, nunca bloqueante | Medir con el "reloj del agente" o narrar "45ms" sin instrumentación |
| 2 | M-07 (B) | Ciclo completo 6/6 pasos sobre proyecto desechable | Saltar pasos intermedios o mutar el sprint de 'walker' |
| 2 | M-08 (B+A) | Estado final consistente + teardown del desechable | No verificar coherencia entre pasos |
| 3 | M-09 (B+A) | 0 crashes del server (sobrevive a errores repetidos) | No probar errores |
| 3 | M-10 (B) | Errores JSON-RPC válidos con códigos correctos (`-32602`, etc.) | Aceptar tracebacks |
| 4 | M-11 (B) | Consistencia 5/5 equivalentes con evidencia adjunta | Comparar sin conservar las respuestas |
| 4 | M-12 (B) | Reconexión: nueva sesión stdio funcional (el agente NO puede re-conectarse a sí mismo) | Afirmar reconexión sin sesión nueva real |
| 4 | M-13 (A) | Evidencia cruda adjunta en el reporte + precondición Capa B declarada en el veredicto | Veredicto sin evidencia ni precondición |

## 6. Cross-references

- ADR-WALKER-071: Migración a FastMCP (base de esta validación) y §3bis (arquitectura objetivo)
- `docs/MCP_VALIDATION_REPORT.md`: reporte consolidado (evidencia real del agente, 2026-09-02)
- `docs/PLAN-MCP-VALIDATION-phases.md`: plan por fases — el método subprocess histórico está marcado ❌ anti-patrón
- Memoria memora 11842: decisión agent-as-validator (NO reescribir cliente MCP)
- ADR-WALKER-018: context gates (checklist de memoria antes de tools de escritura)
- SPEC-003: Niveles de testing (E2E, BDD, unit)
- AGENTS.md: Patrón de completitud para registries y reglas de métricas por fase
