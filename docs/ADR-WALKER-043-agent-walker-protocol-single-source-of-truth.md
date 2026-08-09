# ADR-WALKER-043: Protocolo único agente↔walker — contrato de tool como SSOT (inputSchema + outputSchema derivados del registro)

**Status:** Proposed
**Date:** 2026-08-08
**Authors:** Walker Architecture Team
**Related:** ADR-WALKER-023 (casos de uso de consulta), ADR-WALKER-018 (trace/context gates), ADR-WALKER-042 (block resolution), ADR-WALKER-016 (bounded context protocol)

---

## Context

La interacción agente↔walker atraviesa **cuatro capas que re-definen el contrato de cada tool por separado**, lo que genera fricción para el agente (LLM) y viola el principio de una sola fuente de verdad (SSOT).

### El flujo actual de una tool desde su definición hasta el agente

```
1. Task (features/task_execution/tasks/<m>.py)
     registry.register(name, fn, metadata={
         "description", "input_schema", "scope", "tags", "usage", "example",
         "query_instructions", ...
     })
     └── protocolo INCOMPLETO: metadata libre, sin output_schema.

2. TaskRegistry.execute(name, ctx, data) → TaskResult {ok, data, error, side_effects, metrics}
     └── salida GENÉRICA: data es dict sin esquema declarado.

3. Engine MCP (features/engine/mcp.py)
     ToolDefinition(name, description, handler, input_schema=...)  ← RE-ESCRIBE el schema a mano
     server.tool(name, description, handler, input_schema)          ← vuelve a definirlo
     └── el input_schema que el agente ve PUEDE NO coincidir con el de la task.

4. Cliente/agente (MCP protocol: name, description, inputSchema, outputSchema)
     └── outputSchema NO EXISTE en ningún lado.
```

### Los tres problemas concretos (evidencia en el código)

1. **El `input_schema` se define dos veces y se desalinea.**
   - `mcp.py:22` registra `agent_context` con `input_schema={layers, query}`.
   - La task `kg_agent_context` consume `{file, task_id, text}` (vía `body.get("cpg.file")`, etc.).
   - El agente lee el schema MCP, arma un body con `layers/query`, y la task lo parsea con otro contrato. **El contrato visible ≠ el contrato real.**

2. **No existe `outputSchema` en ninguna capa.**
   - El protocolo MCP espera `outputSchema` por tool.
   - `ToolDefinition` (contracts.py:17) y `McpServer.tool` (mcp_server.py:97) solo aceptan `input_schema`.
   - `TaskResult` es genérico (`data: T`). El agente **no sabe qué forma tiene la respuesta** hasta llamar y ver.

3. **Cada capa re-interpreta el body.**
   - El handler MCP (`_public_task_handler` → `dispatch_public_task`) recibe `body`, lo "despliega" (`task_data.setdefault(k, v)`), y la task lo re-parsea. Múltiples interpretaciones del mismo contrato.

> **Principio violado:** SSOT del contrato. Hoy el contrato de una tool se fragmenta entre
> la task, el TaskRegistry, el mcp.py y el adapter — y el agente paga la fricción.

---

## Decision

**El contrato de cada tool se define UNA sola vez en el registro de la task (o del módulo), y todas las capas lo DERIVAN — nunca lo re-escriben.**

### 1. `output_schema` pasa a ser parte del protocolo de task

Toda task pública declara su `output_schema` (JSON Schema de `TaskResult.data`) junto al `input_schema`:

```python
registry.register("kg_task_context", fn, metadata={
    "description": "Contexto de un archivo antes de editarlo (joins cpg×ckg×delta×trace).",
    "input_schema": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "Ruta del archivo"},
            "task_id": {"type": "string", "description": "Spec/TASK del sprint"},
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "phase": {"type": "string"},
            "entities": {"type": "object"},
            "insights": {"type": "array"},
            "risk": {"enum": ["LOW", "MED", "HIGH"]},
        },
    },
    "scope": "public",
    "tags": [...],
    "example": 'walker <project>/kg_task_context \'{"file":"src/auth.py"}\'',
})
```

Los DTOs de contrato de `task_execution/application/view_models.py` (que ya existen; renombrados
a `contracts.py` — ver sección 4.1) son la base natural
para derivar el `output_schema` de cada fase.

### 2. El MCP deriva el contrato, no lo redefine

`McpServer.tool()` y `ToolDefinition` aceptan `output_schema`, y `mcp.py::_register_public_tasks`
pasa el schema de la task tal cual:

```python
# mcp.py — en vez de re-escribir:
server.tool(
    name=name,
    description=f"{desc} (public task). Read walker://help/{name}.",
    handler=_public_task_handler(name),
    input_schema=t.get("input_schema") or DEFAULT_SCHEMA,   # DE la task
    output_schema=t.get("output_schema"),                    # NUEVO: DE la task
)
```

Las tools manuales del workflow (`status`, `submit`, `agent_context`, ...) migran a declarar
también `output_schema`, con el mismo patrón.

### 3. El cuerpo que recibe la task es el del schema (sin re-interpretación)

`dispatch_public_task` deja de "adivinar": el body que llega es el que el `input_schema` declara.
La task consume exactamente las claves del schema, sin mapas intermedios (`layers`→`file`).

### 4. Aplica a TODA la interacción agente↔walker (no solo tasks)

El protocolo único cubre:
- **Tasks públicas** (`kg_*`, `agent_context`, `cpg_analyze`, ...) — via metadata del registro.
- **Tools del workflow** (`status`, `submit`, `transition`, `prepare`, `list`, `help`) — via `ToolDefinition`.
- **REST routes** (`{project_id}/...`) — el `flags_builder` solo traduce la URL al body del schema, sin inventar campos.
- **Adapters** (`mcp_adapter.py`, `_module_tool_handler`) — leen `inputSchema`/`outputSchema` del registro, no los redefinen.
- **Prompts del protocolo** (`walker://help/<tool>`) — se generan desde el metadata (description + example + input/output schema).

### 4.1 Corrección de terminología: NO son ViewModels, son DTOs de contrato

El término **ViewModel** es del patrón MVC/MVVM (capa de presentación: estado de UI, comandos,
formateo para una vista). Walker es **backend** — su cliente es un agente IA que consume JSON,
no una vista. La pieza que cruza la frontera aplicación→transporte se llama correctamente:

- **DTO (Data Transfer Object)** — objeto plano que transfiere datos entre capas.
- **Contract model / response model** — el contrato de entrada/salida de la tool.
- **VO derivado de schema** — el Value Object que *es* el contrato (valida y serializa).

> **Regla:** en el walker NO usamos "ViewModel" ni el patrón de presentación. Usamos
> **DTOs de contrato** (archivo `contracts.py` por feature), derivados de los esquemas.

### 4.2 Principio general: el contrato es un VO derivado de esquema (no solo MCP)

El gap es **general, no solo del MCP**. Toda la interacción agente↔walker debe ser determinista
y auto-descriptiva. El principio:

```
schema (JSON) ──deriva──> DTO/VO (dataclass tipado) ──es──> contrato de la tool
   ckg.schema.json          KnowledgeFactDTO              input_schema + output_schema
   plan.schema.json         SpecDTO / SpringDTO           (para qué, cómo, qué entra, qué sale)
   (output de UseCase)      FailureDiagnosisDTO, etc.
```

Cada DTO:
- se **deriva del schema** (mismo origen de verdad, no coexisten sin relación),
- **valida en construcción** (`__post_init__` / `from_dict`),
- **serializa a JSON** (`as_dict()`) para la frontera,
- es el **mismo tipo** que el `input_schema`/`output_schema` expone al agente.

Esto aplica a: **tasks** (`kg_*`), **workflow** (sub_states), **REST**, **prompts/help**,
**adapters** — cada feature declara su `contracts.py` con sus DTOs derivados de sus esquemas.

> El detalle por feature (mapa de tasks/schemas/VOs y los DTOs de cada feature) se desarrolla
> en la sección **§8 del presente ADR** (Plan de aplicación por feature).

---

## Why this is correct

| Argumento | Detalle |
|---|---|
| **SSOT del contrato** | El schema vive en el registro de la task; MCP, REST, adapters y prompts lo derivan |
| **El agente ve lo real** | `inputSchema` siempre coincide con lo que la task consume; hay `outputSchema` para saber la respuesta |
| **Cero desalineación** | Se elimina la fuente del bug de `agent_context` (schema MCP ≠ firma de task) |
| **Descubrimiento real** | El agente puede planear una llamada sin adivinar (schema + ejemplo + intención) |
| **Consistente con el stack** | El motor ya es tipado (DTOs de `view_models.py`); el output_schema los refleja |
| **Extensible** | Una task nueva solo define su metadata completo; el resto del sistema la adopta solo |

### Qué NO cambia

- **El protocolo de ejecución** (`registry.execute(name, ctx, data)` → `TaskResult`).
- **`TaskResult` como envelope genérico** — sigue siendo el transporte; el `output_schema` describe su `.data`.
- **El discovery automático** (`bootstrap._discover_and_register`) — solo se enriquece lo que registra.
- **Las tools internas del workflow** (`scope="internal"`) — no se exponen al agente (regla existente).

---

## Impact

| Área | Antes | Después |
|---|---|---|
| `input_schema` de tools públicas | duplicado (task + mcp.py), desalineado | definido en la task, derivado por MCP |
| `output_schema` | inexistente | declarado en la task/metadata, expuesto por MCP |
| `agent_context` (mcp.py:22) | schema `{layers, query}` ≠ firma real | schema `{file, task_id, text}` = firma real |
| REST routes | `flags_builder` inventa campos | traduce URL → body del schema declarado |
| adapters | redefinen schema | leen del registro |
| prompts (`walker://help`) | texto fijo | generados desde metadata (description/example/schema) |
| tareas de consulta por fase | no expuestas | `kg_task_context`, `kg_diagnose`, etc. con contrato completo |

### Trade-offs aceptados

- **Costo de schema**: declarar `output_schema` por tool agrega metadata. Se acepta porque elimina
  la fricción de descubrimiento del agente y previene bugs de desalineación.
- **Migración gradual**: las tools existentes se migran a `output_schema` de forma incremental;
  el sistema usa `DEFAULT_SCHEMA` mientras una tool no lo declare.

---

## Alternatives considered

| Alternativa | Por qué se rechaza |
|---|---|
| Dejar el contrato fragmentado (status quo) | Mantiene el bug de `agent_context`, sin `outputSchema`, agente adivinando |
| Solo arreglar `agent_context` | Corrige un síntoma; el protocolo sigue roto en 4 capas |
| Generar schema desde el runtime (inferir del primer resultado) | No determinista; el agente no puede planear sin conocer la forma |
| Definir los schemas en un archivo central aparte | Crea una segunda fuente de verdad, separada del código que ejecuta |

---

## Implementation steps

- [x] Extender `ToolDefinition` (contracts.py:17) con `output_schema` (opcional).
- [x] Extender `McpServer.tool()` y el `ToolRegistry.register` con `output_schema`.
- [x] En `mcp.py::_register_public_tasks`, pasar `output_schema=t.get("output_schema")`.
- [x] En `mcp_adapter.py`, propagar `outputSchema` desde el registro.
- [x] Corregir `agent_context` (mcp.py:22) al schema real `{file, task_id, text}` + output_schema.
- [ ] Agregar `output_schema` a las tasks públicas nuevas de consulta por fase
   (`kg_task_context`, `kg_diagnose`, `kg_qa_evidence`, `kg_sprint_evidence`).
- [ ] Migrar tools del workflow (`status`, `submit`, `transition`, ...) a declarar `output_schema`.
- [ ] Generar `walker://help/<tool>` desde el metadata (description + example + schemas).
- [ ] Tests: schema MCP ≡ schema de la task; output_schema presente en todas las tools públicas.
- [ ] Aplicar el patrón `contracts.py` por feature (§8): task_execution, cognitive_knowledge,
      delta_tracking, code_analysis, event_tracing, engine/workflow.

---

## 8. Plan de aplicación por feature (el patrón completo)

Este ADR cubre **toda** la interacción agente↔walker, no solo el MCP. El patrón a aplicar en cada
feature, en orden de valor para el agente:

### Mapa actual del gap (evidencia)

| Feature | Tasks/tools públicas | Con `input_schema` | Con `output_schema` | Schemas JSON existentes | VOs/entities de dominio |
|---|---|---|---|---|---|
| **task_execution** | ~33 (kg_*, agent_context, cpg/delta/ckg_analyze) | ~8 | 0 | — | DTOs en `view_models.py` (a renombrar `contracts.py`) |
| **cognitive_knowledge** | kg_store, kg_search, ... | parcial | 0 | `ckg.schema.json` | `KnowledgeFact` (entity ODM) |
| **delta_tracking** | delta_analyze, ... | 1 | 0 | `plan.schema.json` | `Spec`, `Spring` (VOs) |
| **code_analysis** | cpg_analyze, analyze_code, ... | 0 | 0 | — | `Graph`, `Query` (domain) |
| **event_tracing** | trace_push, ... | 0 | 0 | — | `TraceEvent` (VO) |
| **engine/workflow** | status, submit, transition, help + 47 prompts (8 sub_states llm_task) | parcial | 0 | `workflow.schema.json`, `knowledge-capture.schema.json` | `WorkflowState`, `WorkflowGraph` |

### Patrón a aplicar en cada feature

```
features/<feature>/
  ├─ contracts.py            ← NUEVO: DTOs de contrato derivados de los esquemas
  │     @dataclass(frozen=True) class <Tool>Input / <Tool>Output:
  │         from_dict()  → valida y construye
  │         as_dict()    → serializa a JSON (frontera)
  │         to_schema()  → genera input_schema / output_schema
  ├─ domain/values.py        ← VOs del dominio (ya existen varios)
  ├─ <feature>.schema.json   ← esquema de datos persistidos (origen de los DTOs)
  └─ tasks/<tool>.py         ← register() usa el contrato del feature
```

### Orden de aplicación

1. **task_execution** — renombrar `view_models.py` → `contracts.py`; añadir `from_dict`/
   `to_schema` a los DTOs existentes; registrar las 4 tools de fase con contrato completo.
2. **cognitive_knowledge** — derivar DTOs de `ckg.schema.json` + `KnowledgeFact`; contrato para
   `kg_store`/`kg_search`.
3. **delta_tracking** — derivar DTOs de `plan.schema.json` + `Spec`/`Spring`; contrato para
   `delta_analyze`.
4. **code_analysis** — convertir dicts de `QueryGraphUseCase.read/grep` a DTOs; contrato para
   `cpg_analyze`.
5. **event_tracing** — contrato para `trace_push` (TraceEvent ya es VO).
6. **engine/workflow** — derivar los `ToolContract` de los sub_states del workflow (ver §9):
   context providers → DTOs de entrada; `output_schema` de sub_states → DTOs de respuesta;
   prompts generados del contrato.

---

## 9. Protocolo del workflow (agente ↔ sub_state)

El workflow se comunica con el agente en **dos direcciones**, y ambas sufren el mismo
gap del ADR-043: contrato fragmentado, sin DTOs derivados de esquema.

### 9.1 El mecanismo actual (evidencia)

```
features/engine/workflow/nodes/<nodo>.node.json   (8 nodos)
  └── sub_state { prompt_id, execution: "llm_task" (8), output_schema }
        │
        ▼
features/prompts/templates/nodes/<nodo>/<sub>.prompt.json   (47 templates)
  └── { id, type, version, description, tags, template }     ← template es TEXTO LIBRE
        │   con placeholders {task_id}, {q1}, {a1}, {task_title}...
        ▼
PromptManager.resolve(template_id, ctx) → collect_all() → render(template, data)
        │
        ├── context_bag (workflow.json)        ← catálogo libre de placeholders
        ├── context providers (7 keys)         ← devuelven TEXTO, no DTOs
        │     cpg:business, ckg, delta, diff, test_results,
        │     trace, trace:reflection, project
        └── extra_data (sprint_ctx, etc.)      ← diccionarios sueltos
```

**Problema de entrada:** el contexto que el agente recibe es texto renderizado desde
providers que devuelven strings sueltos — **no hay DTO tipado** ni schema del contexto.

```
agente → submit(body) → engine valida contra output_schema del sub_state
```

**Problema de salida:** el `output_schema` del sub_state **sí existe** (mejor que las
tasks), pero es un JSON Schema suelto — **no deriva de un DTO**, no valida con el mismo
mecanismo que las tools, y el prompt no le dice al agente el formato exacto con un ejemplo.

### 9.2 El refinamiento (mismo protocolo del ADR-043)

**A. Entrada — context providers devuelven DTOs de contrato.**

Cada provider (en `features/<feature>/context/providers.py`) devuelve un DTO tipado
(en vez de texto libre), y el `context_bag` deriva el schema:

```python
# features/engine/context/providers.py — ejemplo (diff/test_results)
@dataclass(frozen=True)
class TaskSpecContext:                      # contexto que el agente recibe en IMPLEMENT.WORK
    task_id: str
    description: str
    acceptance_criteria: list[str]
    quality_gates: list[str]
    impacted_files: list[str]               # impacted_artifacts.files
    validation_predicates: list[str]        # validation.predicates
    decisions: list[str]                    # ADRs relacionados
    # → to_schema() genera el contrato de contexto
```

**B. Salida — los `output_schema` de los sub_states se conectan a DTOs de respuesta.**

Cada sub_state con `llm_task` tiene su DTO de respuesta con `from_dict()` que valida
contra el schema declarado en el node.json:

```python
# features/engine/workflow/contracts.py (nuevo)
@dataclass(frozen=True)
class ImplementWorkOutput:                  # output_schema de implement.node.json WORK
    result: dict                            # archivos modificados, diff, metrics
    quality_notes: list[str]
    @classmethod
    def from_dict(cls, data: dict) -> "ImplementWorkOutput": ...   # valida
```

**C. El prompt se genera del contrato (no texto libre).**

El `template` de cada prompt se construye a partir del contrato: la sección de contexto
se arma desde los DTOs de entrada (`to_schema`), y la sección de respuesta desde el DTO
de salida con su ejemplo. El agente **sabe exactamente qué recibe y qué debe devolver**.

### 9.3 Mapa sub_state → DTO (a completar en la aplicación)

| Nodo | Sub_state (llm_task) | DTO de entrada (contexto) | DTO de salida (respuesta) |
|---|---|---|---|
| **implement** | WORK | `TaskSpecContext` (task_id, criteria, gates, files, predicates, decisions) | `ImplementWorkOutput` (result, quality_notes) |
| **verify** | QA, PREDICATE_CHECK, PO_ACCEPT | contexto de la spec + predicates | `VerifyOutput` (veredicto, evidencias) |
| **refine** | DIAGNOSE, ESCALATION | `error_context`, `attempt`, `source_node` (reflection) | `RefineOutput` (diagnóstico, acciones) |
| **analyze** | GATHER, REVIEW | contexto ckg/cpg/delta | `AnalyzeOutput` (hallazgos) |
| **sprint** | PLANNING, REVIEW, RETRO | sprint_goal, task_count, ready/blocked | `SprintOutput` (plan, veredicto) |
| **project_init** | SCAN, GATHER, REVIEW | proyecto detectado | `ProjectOutput` (init plan) |

---

## Non-negotiables (design rules)

- **SSOT del contrato**: el schema de una tool se define una sola vez; todas las capas lo derivan.
- **`input_schema` visible == contrato real de la task** — sin re-interpretación intermedia.
- **Toda tool pública expone `output_schema`** (o usa `DEFAULT_SCHEMA` transitorio, con deuda marcada).
- **El agente puede planear una llamada** leyendo description + input_schema + output_schema + example.
- **Nada se re-escribe en el camino**: MCP, REST, adapters y prompts leen del registro.
- **El protocolo del workflow es el mismo contrato**: los context providers devuelven DTOs de
  entrada, los `output_schema` de sub_states derivan de DTOs de respuesta, y los prompts se
  generan del contrato (no texto libre).
