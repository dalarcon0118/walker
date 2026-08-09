# ADR-WALKER-018: Context Gates — checklist obligatorio por tool + Trace consolidado del agente

> **Status:** Accepted (decisiones PO registradas) — pendiente de implementación
> **Fecha:** 2026-08-06
> **Contexto:** reforzar el prompting del hook (pasivo → gates ejecutables) y cerrar el ciclo del contexto sobre las 4 memorias.
> **Complementa:** ADR-WALKER-013 (DeltaGraph SSOT), ADR-WALKER-016 (Bounded Context Protocol), ADR-WALKER-017 (build-time vs query-time)
> **Spec:** SPEC-WALKER-019

---

## 1. Contexto y problema

El hook `.opencode/plugins/walker-session.js` inyecta l contexto del walker al agente.
Hoy el enforcement es **texto plan instructivo** en `experimental.session.compacting`:

```text
### QUERY PROTOCOL (MANDATORY)
... you MUST consult ... Only after exhausting walker queries, use grep/read ...
```

**Problema**: es pasivo. El modelo puede ignorarlo. Y de hecho hoy:
- `grep`/`glob` **no pasan por las memorias** los mejores casos indexados en CPG/CKG/delta.
- No hay **gates** que verifiquen que las consultas se hicieron.
- No hay forma de **preguntar el contexto de la sesión** en una sola query (qué tools usé,
  qué sprint es, qué decisiones aplican, qué code cambió).

### El principio rector

> **La memoria del walker no es informativa opcional — es el SSOT del contexto.**
> Toda acción del agente sobre una capa (código, conocimiento, estado, ejecución)
> DEBE ir precedida de la consulta a la memoria de esa capa. El hook lo **enforcement**,
> no se limita a aconsejarlo.

---

## 2. Decisiones y alternativas

| Tema | Alternativa descartada | Alternativa elegida |
|---|---|---|
| Tipo de prompt | Texto "you MUST consult..." (pasivo, ignorable) | **Checklist gates numerado por tool, impuesto por el hook** |
| Enforcement | Bloquear (`throw`) para imponer cumplimiento | **`annotate`**: ejecutar gates y anexar resultado al output (decisión PO) |
| Noise | Re-ejecutar gates en cada call | **Cache por sesión** de gates ya pasadas |
| Proyectos | Hardcode de `WALKER_ROOT` en el hook / auto-detect walk up | **`project_id` desde config del agente** (decisión PO) |
| Query consolidada | Consultas separadas (4 calls) / reusar `kg_multi` | **Nueva task `kg_agent_context`** (decisión PO) |
| Almacen de eventos | Solo CKG (`ckgSave`) | **Índice `trace` (TraceEvent)** con tipos estructurados |
| Trace de mensajes | Contenido completo de cada mensaje | **Resumen: role, timestamp, token count, extracto corto** (decisión PO) |

---

## 3. Diseño detallado

### 3.1 Checklist por tool (formato y definición)

En el hook, un catálogo `GATES`:

```js
const GATES = {
  grep: {
    enforce: "block", // o "annotate"
    items: [
      { tool: "kg_multi", args: { cpg: { file: true }, ckg: { scope: true }, delta: { spring: true } }, why: "buscar hits indexados antes de escanear disco" },
      { tool: "kg_schema", why: "descubrir índices disponibles" },
    ],
  },
  glob: { items: [{ tool: "kg_multi", args: { cpg: { file: true } }, why: "el CPG ya indexa archivos" }] },
  read: { items: [{ tool: "kg_multi", args: { cpg: { file: true } }, why: "estructura indexada del archivo" }] },
  edit: { items: [{ tool: "kg_multi", args: { ckg: { scope: true } }, why: "decisiones/convenciones que aplican" }] },
  bash: { items: [{ tool: "kg_search", args: { trace: { tool: "bash" } }, why: "historial reciente de comandos" }] },
}
```

### 3.2 Mecanismo de enforcement (decisión PO: `annotate`, sin bloqueo)

```
for each gate in GATES[tool].items:
  if !cache.get(sessionID).has(gateKey):
      runWalker(gate) → resultado
      annotate → resultado (o "sin hits") se une al pendingGatesByCall
      cache.add(gateKey)
# tool.execute.after: pendingGatesByCall[callID] se anexa a output.output
```

- **Nunca `throw`** (decisión PO 2026-08-06): cero fricción — la tool siempre se ejecuta.
- El resultado de las gates se anexa al output de la tool en `tool.execute.after`
  (mismo patrón que el CPG context de `read` hoy).
- El cache por `sessionID` evita re-consulta repetida en la misma sesión.

### 3.3 Trace consolidado del agente

Registros estructurados en el índice `trace` (usa `TraceEvent` / `trace_push`):

| `event_type` | `source` | Qué guarda |
|---|---|---|
| `session_start` | `agent_hook` | session_id, agent_id, timestamp |
| `agent_message` | `agent_hook` | session_id, role (user/agent), content |
| `tool_call` | `agent_hook` | session_id, tool, input, output, started_at, finished_at, duration_ms |
| `file_a ck` | `agent_hook` | session_id, files impactadas |
| `session_end` | `agent_hook` | session_id, duration |

Para esto es **imprescindible `experimental.chat.messages.transform`** (anexa/registra los
messages agente+usuario) y `session.idle` (session_end).

### 3.4 Query consolidada (cierra el ciclo)

```text
walker <project>/kg_multi '{
  "delta": { "spring": "<id>" },    ← specs + tareas del sprint/proyecto
  "ckg":   { "scope": "<proyecto>" }, ← decisiones, ADRs, convenciones
  "cpg":   { "file": "<archivo>" },   ← estructura indexada
  "trace": { "session_id": "<id>" }   ← eventos, tools, mensajes
}'
```

- **Delta** → `IndexSpec`, `Spec`, `Task`, `Sprint`.
- **CKG** → `decision`, `convention`, `adr` (leído desde `scope`).
- **CPG** → `module`, `function`, `class`, `method`.
- **Trace** → `agent_message`, `tool_call`, `session_*`.

### 3.5 Proyecto dinámico (decisión PO: `project_id` desde config)

```
project_id = env.WALKER_PROJECT_ID || config del agente
runWalker apunta con <project_id>/<task> — sin auto-detección
```

El hook lee el `project_id` de la configuración/entorno del agente y lo usa para
resolver las consultas (`<project_id>/kg_multi`, `<project_id>/kg_agent_context`).

**Implementado (2026-08-06)**: `runWalker(...args)` antepone `{PROJECT_ID}/` a las
tareas públicas que llegan sin prefijo (si el primer arg contiene "/" se usa tal cual).
`PROJECT_ID = process.env.WALKER_PROJECT_ID || "walker"`. Todos los call sites del hook
(`kg_multi`, `kg_search`, `kg_store`, gates) pasan por el prefijo dinámico.

---

## 3. Qué no cambia

- El CPG/CKG/delta/trace siguen siendo los SSOT de cada capa — no se duplican.
- Las tasks públicas (`kg_search`, `kg_multi`, `kg_schema`) se reutilizan.
- La migración `features/code_analysis` → result pattern (ADR-017/decision-139) no cambia.
- El trace se escribe en índice `trace` (no spammear CKG con tool_trace).

---

## 4. Impacto

| Área | Antes | Después |
|---|---|---|
| `grep`/`glob` | búsqueda ciega en disco | gates consultan memorias y anexan resultado al output |
| `read` | inyección CPG actual | refuerzo con checklist + gates anexadas al output |
| Prompt | "you MUST consult" pasivo | checklist gates numerado impuesto (annotate) |
| Trace | solo `session_end` + `tool_trace` genérico | `session_start/end`, `agent_message` (resumen), `tool_call` en índice `trace` |
| Consulta contextual | no existía | nueva task `kg_agent_context` (delta+ckg+cpg+trace) |
| Proyectos | hardcode single root | `project_id` desde config del agente |

**Trade-offs:**

- **Enforcement débil**: al no bloquear (`annotate`), el agente podría ignorar las gates.
  Mitigado porque el resultado de las gates queda **visible en el output** de cada tool —
  el contexto correcto está a la vista, y el checklist se imprime explícitamente.
- **Staleness del trace**: registros estructurados desde el hook; si el hook no carga,
  no hay trace agente. Consistente (igual que hoy).

---

## 5. Alternativas consideradas

| Alternativa | Por qué se rechaza |
|---|---|
| Mantener texto instructivo pasivo | El agente puede ignorarlo; no hay verificación (el problema exacto que resolver) |
| Solo inyectar contexto, sin gates | Mejora el contexto pero no estructura el checklist por tool; el ADR exige gates numerados |
| Bloquear tools (`throw`) | Decisión PO 2026-08-06: fricción innecesaria; se elige `annotate` (inyectar resultado) |
| Escribir todo en CKG (`ckg_store`) | Mezcla knowledge con trace; el índice `trace` ya existe y está diseñado para eventos |

---

## 6. Reglas de diseño (non-negotiables)

- **Cada tool de acción tiene un gate que la antecede** (grep/glob/read/edit/write/bash).
- **El checklist se presenta como gates numerados** — nunca como texto "you MUST".
- **El enforcement es `annotate`** (inyectar resultado, sin bloquear), con cache por sesión.
- **La query consolidada lee 4 índices en ONE call** (delta+ckg+cpg+trace) — cierra el ciclo.
- **El trace del agente se escribe en el índice `trace`** con `TraceEvent` estructurado.
- **Trace resumido**: `agent_message` guarda role, timestamp, token count y extracto corto.
- **El proyecto se resuelve por config** (`project_id` desde config/entorno del agente).
- **Staleness aceptado** (snapshot del último index), igual que ADR-017.

---

## 7. Pasos de implementación

1. Definir `GATES` (por tool: grep/glob/read/edit/write/bash) + cache por sesión.
2. Implementar enforcement en `tool.execute.before` (throw si gate no pasa).
3. Implementar trace estructurado (session_start/end, agent_message, tool_call) via
   `experimental.chat.messages.transform` + `trace_push`.
4. Implementar query consolidada (kg_multi 4 secciones) + nueva tool de respuestas.
5. Refactorizar `experimental.session.compacting` → inyectar el checklist genérico
   (descubrir un llamado compacto) + `experimental.chat.system.transform` para el sys prompt.
6. Resolver proyecto dinámico (no hardcode).
7. Tests + validación manual sobre 2 proyectos.

## 8. Pregunta de refinamiento para el PO

Necesitamos afinar sobre el nivel de agresividad. Sirven las siguientes decisiones:

1. **Nivel de enforcement**: ¿bloquear todas las tools en modo estricto, o `annotate` (avisar) por defecto con `block` solo para grep/read?
2. **Trace de mensajes**: ¿registrar TODOS los `agent_message` (incl. contenidos largos) o solo resumen/cabeceras (role, timestamp, token count)?
3. **Query consolidada**: ¿una nueva task pública `kg_spring_context`/`agent_context` o reusar `kg_multi` con las 4 secciones?
4. **Proyecto dinámico**: ¿autoridad para el hook detectarlo solo (walk up `.cognitive/`), o el hook debe recibir `project_id` desde config/agente?

## 9. Decisiones del PO (2026-08-06)

| # | Decisión | Elección | Implicación |
|---|---|---|---|
| 1 | Nivel de enforcement | **`annotate` para TODAS las tools (sin bloquear)** | El hook ejecuta las gates y anexa el resultado al output de la tool. Nunca `throw`. Cero fricción; el enforcement es por inyección de contexto, no por bloqueo. |
| 2 | Trace de mensajes | **Resumen: role, timestamp, token count** | `agent_message` guarda `role`, `timestamp`, `token_count` y un extracto corto del contenido — no el contenido completo. Índice trace no se infla. |
| 3 | Query consolidada | **Nueva task pública `kg_agent_context`** | Task dedicada que hace `kg_multi` con delta+ckg+cpg+trace y formatea la respuesta. Intención explícita, documentable y testeable. |
| 4 | Proyecto dinámico | **Recibir `project_id` desde config del agente** | El hook lee `project_id` de la config (entorno/agente); no auto-detecta walk up. Control explícito por proyecto. |

### 9.1 Impacto de las decisiones en el diseño

- **Enforcement `annotate`**: el mecanismo de 3.2 cambia — no hay rama `block`. El hook
  ejecuta cada gate, une el resultado al `output.output` de la tool (en `tool.execute.after`)
  o lo deja en `pendingGatesByCall` para anexar tras la ejecución. El checklist se imprime
  igualmente ("Before X, execute these gates: ...") como guía, pero nunca bloquea.
- **Trace resumido**: `agent_message` solo guarda metadata ligera. Los contenidos quedan
  en el chat de opencode (fuente original); el índice `trace` guarda la traza.
- **`kg_agent_context`**: nueva task pública que llama `_kg_search_index`/`search_many` con
  `{delta, ckg, cpg, trace}` y devuelve secciones formateadas. Se registra en el MCP como
  `walker_kg_agent_context` (o `walker_agent_context`).
- **`project_id` desde config**: el hook recibe `WALKER_PROJECT_ID` (entorno o config del
  agente) y lo usa en `runWalker` como `<project_id>/<task>`. Sin auto-detección.