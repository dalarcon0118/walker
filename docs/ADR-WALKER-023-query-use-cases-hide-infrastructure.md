# ADR-WALKER-023: Consultas de negocio por fase — la lectura de memorias se expone como casos de uso del flujo, no como búsqueda de infra

> **Status:** Accepted
> **Fecha:** 2026-08-06
> **Actualizado:** 2026-08-07 — refinamiento: casos de uso **por fase del workflow** + orquestador de joins en `task_execution/application`.
> **Contexto:** las tools `kg_search`, `kg_multi` y `kg_schema` consultan CognitiveGraph vía
> `CognitiveGraphSearcher` (libs/infrastructure) — la infraestructura queda expuesta sin pasar por
> los servicios de dominio de cada feature.
> **Complementa:** ADR-WALKER-016 (Bounded Context Protocol), ADR-WALKER-020 (agent_context), ADR-WALKER-022 (KnowledgeFact)
> **Registrado en CKG:** verificado en sesión

---

## 1. Contexto y problema

El agente consulta las memorias del walker con tres tools:

```
walker kg_search   → CognitiveGraphSearcher.search()    → CognitiveGraph.search()
walker kg_multi    → CognitiveGraphSearcher.search_many() → CognitiveGraph.search_many()
walker kg_schema   → CognitiveGraphSearcher.schema()    → CognitiveGraph.schema()
```

`CognitiveGraphSearcher` (en `libs/infrastructure/cognitive.py`) es un **adaptador genérico** que envuelve
CognitiveGraph y lo expone tal cual. La analogía: es la **"Elasticsearch API"** del walker — operaciones de búsqueda sobre la infraestructura.

### El problema real

Ninguna capa de dominio participa, y el resultado es **lectura genérica sin valor de negocio**:

- `kg_search {type:convention}` con filtro de `superseded` es **lógica de dominio** del módulo `cognitive_knowledge`, pero hoy vive como hack en `kg.py` (`_kg_search_superseded`).
- No hay un **caso de uso que responda a la pregunta de la fase** — solo búsqueda plana por índice.
- `agent_context` hacía 4 searches planos y los empaquetaba (concatenación, sin cruza ni síntesis).
- La consecuencia: **dos caminos de lectura, dos contratos distintos, cero SSOT**.

> **Refinamiento (2026-08-07):** el problema no se resuelve con "más consultas planas". El
> orquestador actual (`TaskIntelligenceService`) cruza las 4 capas por archivo/task, pero devuelve
> **4 tablas crudas** — es `kg_multi` con otro nombre, no un caso de negocio. El salto que falta es:
> **un caso de uso por fase del flujo del agente**, que responda con una síntesis orientada a una
> decisión (riesgo, plan, diagnóstico), no con un dataset.

---

## 2. La decisión

**El orquestador de consulta vive en `task_execution/application` y se refina hasta exponer
casos de uso de negocio** — cada uno correspondiente a una pregunta real del agente en la fase del
workflow donde se encuentra. La infraestructura (CognitiveGraph) queda escondida detrás de los repos
por feature.

### Patrón arquitectónico

```
Agente (en fase del workflow: IMPLEMENT.WORK, VERIFY.QA, REFINE.DIAGNOSE, etc.)
  │  walker <fase>_consulta   (o task por etapa)
  ▼
features/task_execution/tasks/kg_*.py            ← tasks públicas (enrutan, no tocan cg)
  ▼
features/task_execution/application/intelligence_service.py
      │  TaskIntelligenceService.get_<fase>_context(file/task_id/scope)
      │  (el motor cruza los 4 repos con el join que la fase necesita)
  ▼
features/<feature>/application/ports/<repo>.py   ← puertos abstractos (ya existen)
  ▼
features/<feature>/infrastructure/<repo>.py      ← implementación (esconde CognitiveGraph)
```

> **Ubicación decisiva:** el motor/orquestador vive en **`task_execution/application/`**.
> El engine (`features/engine/application`) es **workflow puro que no sabe del negocio** — nunca
> consulta memorias. (Requiere la eliminación de `query_use_cases.py`, que ya se hizo.)

### Dónde viven las capas de datos (memorias)

| Capa | Puerto | Repo infra | Pregunta que responde |
|---|---|---|---|
| CPG (código) | `CpgRepository` | `cognitive_cpg_repo.py` | ¿Cómo está construido/estructura? |
| CKG (conocimiento) | `CkgRepository` | `cognitive_ckg_repo.py` | ¿Qué convenciones/decisiones aplican? |
| Delta (sprint) | `DeltaRepository` | `cognitive_delta_repo.py` | ¿Qué tareas/specs lo tocan? |
| Trace (actividad) | `TraceRepository` | `repository.py` | ¿Qué se hizo y cuándo? |

---

## 3. Los casos de uso de negocio (mapeados al flujo real)

El flujo del agente (ver `features/engine/workflow/workflow.json` y sus `nodes/*.node.json`) define
fases y sub-estados con `prompt_id` y `context`. Cada fase le pide al agente responder **una pregunta
operativa**. La consulta de memoria debe proveer el dato para esa pregunta.

```
SPRINT.PLANNING → SPRINT.ACTIVE → DESIGN → ANALYZE → IMPLEMENT → VERIFY → REFINE → RETRO/CLOSE
```

### Fase SPRINT.PLANNING — "¿qué priorizamos?"
| Caso de uso | Intención del agente | JOIN que necesita |
|---|---|---|
| `sprint_evidence(goal/scope)` | decisiones/preexistentes deben proteger esta meta | ckg(scope) + delta(tareas relacionadas) |
| `dependencies_resolver()` | qué tareas arrancan ya, cuáles bloqueadas | delta(depends_on) + delta(ready/blocked) |

### Fase ANALYZE — "¿qué hay y cómo está construido?"
| Caso de uso | Intención | JOIN |
|---|---|---|
| `module_context(file)` | estructura/funciones de un archivo/servicio | cpg(file) |
| `conventions_map(scope)` | qué convenciones aplican a esa área | ckg(scope/file) |

### Fase IMPLEMENT — "escribo código correcto"
| Caso de uso | Intención | JOIN |
|---|---|---|
| `task_file_intelligence(file, task_id)` | contexto completo de un archivo antes de editar | cpg + ckg + delta + trace + cruce por file |
| `implementation_patterns(pattern)` | cómo se resolvió algo parecido antes | ckg(decision/pattern) + cpg(similares) |
| `conflict_check(convention, intent)` | si una convención restringe lo planeado | ckg(scope) + cpg(file) |

### Fase VERIFY — "¿cumple los criterios?"
| Caso de uso | Intención | JOIN |
|---|---|---|
| `predicate_evidence(task_id)` | hay actividad legítima que sustenta el predicado | trace(task_id) + delta(predicates) |
| `qa_coverage(task_id)` | qué verificaciones pasaron / faltan | delta(predicates/status) + trace(tools) |

### Fase REFINE — "¿por qué falló y cómo arreglo?"
| Caso de uso | Intención | JOIN |
|---|---|---|
| `failure_diagnosis(task_id)` | qué tocó/rompió y qué convenciones se violaron | trace(duration/errores) + ckg(convenciones) + delta(spec) |
| `knowledge_reuse(error_signature)` | hay una decisión que ya resolvió esto | ckg(tipo error/decision) + trace(historial) |

### Fase RETRO/CLOSE — "¿qué aprendimos?"
| Caso de uso | Intención | JOIN |
|---|---|---|
| `sprint_memories(session)` | qué decisiones perduran de estas sesión | ckg(sessionidad) + trace |

---

## 4. Contrato del motor (esquema)

Cada caso de uso es un **método de negocio** sobre el `TaskIntelligenceService` que:

- recibe **intención** (`file`, `task_id`, `scope`, `chain_dir`, etc.),
- ejecuta el **join con propósito** de la fase (NO todos los joins siempre),
- devuelve **una respuesta sintetizada** (riesgo/plan/diagnóstico), NO 4 tablas.

```
Esquema (output) de un caso de uso:
{
  "phase": "IMPLEMENT" ,
  "question": "TODO sobre el archivo antes de editar",
  "entities": { ... seleccionadas para la respuesta ... },
  "insights": [ "síntesis de cruces relevantes para la fase" ],
  "risk": "LOW|MED|HIGH",           // cuando la fase lo requiere (impacto, diagnóstico)
  "ok": true, "error": null | "desc"
}
```

---

## 5. Por qué es correcto

| Argumento | Detalle |
|---|---|
| **Cuestion es negocio** | El agente obtiene una respuesta a su pregunta de fase, no una API de búsqueda |
| **JOIN por propósito** | Cada fase ejecuta solo el cruce relevante (no siempre las 4 capas) |
| **Infra escondida** | CognitiveGraph solo se instancia en `infrastructure/` |
| **Engine puro** | La consulta de memorias vive en `task_execution/application`, NO en `engine/application` |
| **SSOT de lectura** | Un solo camino (task → use case → port → repo), los hacks en kg.py desaparecen |
| **Consistencia** | Igual patrón que `kg_store` (ya usa `CodeKnowledgeGraph`) |

### Qué NO cambia

- **`kg_search`/`kg_multi`/`kg_schema` siguen públicas** (la "Elasticsearch API" genérica por si el agente quiere búsqueda puntual).
- **`CognitiveGraphSearcher` se conserva** como capa técnica, invocado solo desde los repos de cada feature.
- **La escritura (`kg_store`)** ya es correcta — no se toca.
- **El ADR-017** (build-time) sigue pendiente e independiente.

---

## 6. Pasos de implementación

- [x] Crear `TaskIntelligenceService` en `task_execution/application/intelligence_service.py` (motor + `build_intelligence_service(chain_dir)`).
- [x] Migrar `agent_context` a delegar en el motor (reemplaza los 4 `cg.search` planos).
- [x] Eliminar `features/engine/application/query_use_cases.py` (muerto + mal ubicado).
- [x] **Evolucionar el motor a casos de uso por fase**: `task_file_intelligence` (IMPLEMENT),
      `failure_diagnosis` (REFINE), `qa_coverage` (VERIFY), `sprint_evidence` (SPRINT.PLANNING) —
      cada uno con core `@safe` interno y retorno `Result[dict, Exception]` (Result pattern).
- [x] **Composición con use cases de negocio por feature** (el orquestador NO re-implementa joins):
      `_delta_core` compone `QueryDeltaUseCase` + `GetTaskContextUseCase` (dependencias + spring_goal)
      sobre el repo inyectado; el resto replica la semántica de los SSOT por feature (GetCkgNodesForTaskUseCase)
      sobre el repo inyectado, sin construir infra duplicada.
- [x] **Motor 100% tipado con Value Objects + Result pattern** (`view_models.py`): los 4 cores
      `@safe` devuelven `CpgSectionVO`/`CkgSectionVO`/`DeltaSectionVO`/`TraceSectionVO`; `get_task_intelligence`
      devuelve `Result[IntelligenceResultVO, Exception]` (ScopeVO + SummaryVO + 4 secciones + insights),
      cada sección degrada a `ok=False` ante fallo (convention-001) y `as_dict()` serializa en la frontera.
      Los casos de uso por fase devuelven VMs tipados (`TaskFileIntelligenceVM`, `FailureDiagnosisVM`,
      `QaCoverageVM`, `SprintEvidenceVM`) con `as_dict()`.
- [ ] **Alinear el contrato expuesto al agente**: import_schema real (`file/task_id/scope`) + `description`
      de intención + ejemplos, para que el agente descubra el caso de uso (gap de prompt/metadata).
- [ ] Derivar las consultas de los prompts del workflow (`prompts/{implement/work}.prompt.json`,
      `verify/qa`, `refine/diagnose`...) para mapear 1:1 fase → consulta → respuesta.
- [ ] Hacer que `kg_search`/`kg_multi` deleguen en los casos por módulo (no en `CognitiveGraphSearcher`)
      si se busca SSOT total de lectura.
- [ ] Mover `_schema_resource` (mcp.py) al schema del módulo.

---

## 8. Trade-offs

- **Costo de abstracción**: un motor + métodos por fase agrega indirección. Se acepta porque
  centraliza la semántica y convierte herramientas de búsqueda en **producto de agente**.
- **`kg_search`/`kg_multi` coexisten** con los casos de uso: las primeras para quirugía puntual, los
  segundos para preguntas de fase.

---

## 9. Alternativas consideradas

| Alternativa | Por qué se rechaza |
|---|---|
| Dejar las consultas planas (status quo) | Mantiene la violación y devuelve "4 listas" sin valor de negocio |
| Motor que cruza 4 tablas (stado actual del orquestador) | Es `kg_multi` renombrado; no responde la pregunta de fase |
| Eliminar kg_multi/kg_search | El agente necesita búsqueda federada y puntual; se refina, no se quita |
| Poner toda la lógica en kg.py | Centraliza fuera de los módulos dueños — el error que se elimina |

---

## 7. Reglas de diseño (non-negotiables)

- **CognitiveGraph solo se instancia en `infrastructure/`** — nunca en tasks, MCP ni handlers.
- **El orquestador/consultas viven en `task_execution/application/`** — no en el engine.
- **El engine permanece puro/workflow**: nunca consulta memorias desde `engine/application`.
- **Cada consulta es un caso de negocio por fase** (intención → join específico → síntesis),
  no un volcado de tablas.
- **La semántica de cada índice vive en el módulo dueño** (superseded → cognitive_knowledge, etc.).
- **Exponer las consultas al agente con descripción de intención + esquema real** para que
  el agente pueda descubrirlas (bloqueo de producto detectado).
- **Convention-001** aplica: nunca `except: pass`, fallback explícito con log.

---

## 10. Deuda / bloqueo de producto detectado

El motor técnico está listo, pero el **agente no puede descubrir los casos de uso**: hoy el
`input_schema` MCP de `agent_context` (`{layers, query}`) no coincide con la firma real del motor
(`file`, `task_id`, `text`), y no hay descripciónología/casos de uso en metadata. Este bloqueo de
**descubrir / intención** es el siguiente paso definido en el ADR (ver §7), independiente del motor.