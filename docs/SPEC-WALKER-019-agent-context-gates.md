# SPEC-WALKER-019: Context Gates — checklist obligatorio por tool + Trace consolidado del agente

> **Status:** Draft — pendiente de refinar (ADR-WALKER-018)
> **Fecha:** 2026-08-06
> **Autor:** Usuario (PO) + diseño del hook
> **Complementa:** ADR-WALKER-013 (DeltaGraph SSOT), ADR-WALKER-016 (Protocol BC), ADR-WALKER-017 (build-time vs query-time)

---

## 1. Objetivo

Reforzar el sistema de prompting del agente: en lugar de texto plano instructivo
("you MUST consult...", pasivo y opcional), cada tool del agente tendrá un
**checklist de gates obligatorio** que el hook impone de forma **agresiva**:

> "Before X, execute this checklist gates" — el hook bloquea o anota si no se cumplen.

Y cerrar el ciclo del contexto: el hook debe poder **responder preguntas** sobre la
sesión del agente consultando las 4 memorias del walker:

| Memoria | Responde |
|---|---|
| **Delta** | especificación del spring y del proyecto (specs, tasks, sprints) |
| **CKG** | decisiones, reglas, ADRs y convenciones del proyecto |
| **CPG** | modificaciones del código (estructura indexada) |
| **Trace** | eventos generados, herramientas usadas y mensajes (agente + usuario) |

## 2. Contexto y problema

### 2.1 Estado actual del hook (`.opencode/plugins/walker-session.js`)

| Tool del agente | ¿Consulta memorias antes? | Mecanismo actual |
|---|---|---|
| `read` | ✅ Sí | `tool.execute.before` → `kg_multi` (CPG+CKG) → anexa al output |
| `edit` / `write` | ❌ No | Solo traza "Tool executed" |
| `bash` | ❌ No | Solo protege `.cognitive/` + bloquea python inline |
| `grep` | ❌ **No** | Sin handler — búsqueda ciega en disco |
| `glob` | ❌ **No** | Sin handler |
| cualquier tool | ❌ No | El QUERY PROTOCOL inyectado en compaction es solo texto instructivo |

### 2.2 Problemas

1. **El QUERY PROTOCOL es pasivo**: "you MUST consult..." es texto que el modelo puede
   ignorar. No hay verificación ni gates.
2. **`grep`/`glob` no pasan por las memorias**: búsquedas ciegas en disco cuando el
   CPG/CKG/delta/trace ya tienen la respuesta indexada.
3. **No hay ciclo de contexto**: el agente no puede preguntar "¿qué hicimos en esta
   sesión?", "¿qué tools usé?", "¿cuál es el sprint del proyecto?" con una sola query
   consolidada.
4. **Los proyectos son dinámicos**: el hook hardcodea `WALKER_ROOT` a un solo proyecto;
   debe funcionar sobre el proyecto en el que el agente trabaje.

### 2.3 El principio

> **Cada tool del agente es un punto de control de contexto.** Antes de actuar sobre
> una capa (código, conocimiento, estado), el agente DEBE haber consultado la memoria
> que corresponde. El hook es el enforcement: no texto instructivo — checklist gates.

---

## 3. Requerimientos funcionales

### RF-1: Checklist gates por tool

Cada tool tiene un checklist obligatorio. Formato:

```
Before <tool>, execute these checklist gates:
  [ ] GATE-1: walker_kg_schema        → descubrir índices disponibles
  [ ] GATE-2: walker_kg_multi         → federated query CPG+CKG+Delta+Trace
  [ ] GATE-3: <query específica del contexto de la tool>
  → Solo después de pasar todos los gates, ejecuta <tool>.
```

Gates por tool (propuesta inicial):

| Tool | Gates (orden) |
|---|---|
| `grep` | 1. kg_schema · 2. kg_multi `{cpg:{file/symbol}, ckg:{scope}, delta:{spring}}` · 3. si hay hits, usarlos antes que grep |
| `glob` | 1. kg_schema · 2. kg_multi `{cpg:{file}}` (el CPG ya indexa archivos) |
| `read` | 1. kg_multi `{cpg:{file}}` (ya existe hoy) · 2. anexar resultado al output |
| `bash` | 1. kg_search trace `{tool:bash}` reciente · 2. verificar `.cognitive/` protegido |
| `edit`/`write` | 1. kg_multi `{cpg:{file}}` · 2. kg_search ckg `{scope:feature}` (decisiones que aplican) |
| `task` (subagentes) | 1. kg_multi consolidado del proyecto |

### RF-2: Mecanismo agresivo (enforcement)

- **Modo bloquear** (`throw` en `tool.execute.before`): la tool NO se ejecuta hasta que
  las gates se cumplan. El error devuelto contiene el checklist con los gates faltantes.
- **Modo anotar**: si el gate ya fue cubierto en esta sesión (cache por tool+callID),
  se anexa el resultado al output y se permite.
- **Cache por sesión**: `Map<tool, Set<gatesDone>>` — no re-ejecuta gates ya pasados.

### RF-3: Trace consolidado del agente

El hook registra eventos estructurados en el índice `trace` (TraceEvent) con:

- `event_type`: `agent_message` (usuario/agente), `tool_call`, `session_start`, `session_end`
- `session_id`, `agent_id`
- `tool`, `input`, `output`
- `timestamp`, `started_at`, `finished_at`, `duration_ms`
- `files` impactados

### RF-4: Query consolidada de contexto (cierra el ciclo)

Nueva tool/task: `walker_kg_multi` con 4 secciones (delta + ckg + cpg + trace):

```
walker <project>/kg_multi '{
  "delta": {"spring": "<id-activo>"},        ← specs y tasks del sprint/proyecto
  "ckg":   {"scope": "<proyecto/feature>"},   ← decisiones, ADRs, convenciones
  "cpg":   {"file": "<archivo>"},             ← estructura indexada del código
  "trace": {"session_id": "<id>"}             ← eventos, tools, mensajes
}'
```

Responde: "¿Qué hicimos?", "¿Qué tools usé?", "¿Cuál es el sprint?", "¿Qué decisiones
aplican a este archivo?".

### RF-5: Proyectos dinámicos

El hook resuelve el proyecto en el que trabaja el agente (no hardcode):

- Desde `directory` del hook → buscar `.cognitive/` hacia arriba → `project_id`.
- `runWalker` apunta al CLI walker con el `chain_dir`/`project_id` resuelto.

---

## 4. Criterios de aceptación

- [ ] `grep`/`glob` consultan las memorias antes de ejecutarse (gate inyectado/forzado).
- [ ] El checklist se presenta como gates numerados, no como "you MUST consult...".
- [ ] En modo bloquear, la tool no se ejecuta si las gates no se cumplen.
- [ ] El hook registra `agent_message` (usuario y agente), `tool_call`, `session_start/end`
      en el índice `trace` con `session_id`, `tool`, `timestamp`, `duration_ms`.
- [ ] Una sola query consolidada responde: sprint/delta + decisiones/CKG + código/CPG +
      eventos/trace.
- [ ] El hook funciona sobre cualquier proyecto dinámico (no hardcode).
- [ ] Suite de tests pasa (287).

---

## 5. Entregables

1. `docs/ADR-WALKER-018-*.md` — decisión arquitectónica del sistema de gates.
2. `.opencode/plugins/walker-session.js` — implementación de gates + trace + query consolidada.
3. Tests del hook (si la plataforma lo permite) + validación manual.
