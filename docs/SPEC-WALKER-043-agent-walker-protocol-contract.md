# SPEC-WALKER-043: Plan de especificaciones — Protocolo único agente↔walker (contrato SSOT)

> **Status:** Draft — pendiente de aprobación (traduce ADR-WALKER-043 a requerimientos)
> **Fecha:** 2026-08-08
> **Autor:** Walker Architecture Team
> **Deriva de:** [ADR-WALKER-043](ADR-WALKER-043-agent-walker-protocol-single-source-of-truth.md) (SSOT del contrato, §1–§4, §8, §9)
> **Complementa:** ADR-WALKER-023 (casos de uso de consulta), ADR-WALKER-018 (trace/context gates)

---

## 1. Objetivo

Convertir la decisión arquitectónica del ADR-043 en un **plan de especificaciones implementable**:
cada tool pública del walker expone un **contrato completo** `{name, description, inputSchema, outputSchema, example}`
definido **una sola vez** (SSOT) en el registro/feature, y **todas las capas** (MCP, REST, adapters,
prompts/help) lo **derivan**, nunca lo re-escriben.

El alcance cubre **dos superficies** con el mismo protocolo:

| Superficie | Significado | Estado verificado hoy |
|---|---|---|
| **A. Tasks públicas** (kg_*, analyze, agent_context, ...) | contrato en `registry.register(...)` metadata | 9 con `input_schema` · **0 con `output_schema`** |
| **B. Workflow (sub_states)</b> | contexto que el agente recibe y respuesta que devuelve | 8 nodos · 16 sub_states `llm_task` · 47 prompts · 9 providers sin DTOs |

Métrica global de éxito: cuando se complete esta SPEC, **el agente podrá planear una llamada a
cualquier tool/sub_state leyendo únicamente su contrato** (descripción + esquemas + ejemplo), sin
necesidad de llamarla para descubrir la forma de entrada o salida.

---

## 2. Contexto y problema (resumen del ADR-043 con evidencia)

### 2.1 El contrato se fragmenta en 4 capas para cada task

```
1. Task (features/<feature>/tasks/<m>.py)
     registry.register(name, fn, metadata={"description", "input_schema", "scope", "tags", ...})
     └── protocolo INCOMPLETO: metadata libre, SIN output_schema.
2. TaskRegistry.execute(name, ctx, data) → TaskResult {ok, data, error, ...}
     └── salida genérica: data es dict sin esquema declarado.
3. Engine MCP (features/engine/mcp.py)
     ToolDefinition(name, description, handler, input_schema=...)  ← RE-ESCRIBE el schema
     server.tool(name, description, handler, input_schema)
     └── el input_schema visible puede NO coincidir con el de la task.
4. Cliente/agente (MCP: name, description, inputSchema, outputSchema)
     └── outputSchema NO EXISTE en ningún lado.
```

**Evidencia verificada hoy:**
- `features/task_execution/tasks/`: **0** registros con `output_schema`; **9** con `input_schema`.
- El caso `agent_context` fue corregido (`{file, task_id, text}` + `output_schema`), pero solo ese.
- Los 16 sub_states `llm_task` declaran `output_schema` en `*.node.json` pero **sin DTO que los valide
  ni los conecte** al mismo mecanismo de contracto de las tasks.
- Los **9 context providers** (`features/<feature>/context/providers.py`) devuelven **texto libre**,
  no DTOs tipados con `to_schema()`.

### 2.2 El gap del workflow (el mismo protocolo roto, en ambas direcciones)

```
ENTRADA  agente ← contexto renderizado del prompt (47 templates, texto libre con placeholders)
         sources: context_bag (workflow.json) + providers (texto) + extra_data (dicts sueltos)
SALIDA   agente → submit(body) → engine valida contra output_schema del sub_state (JSON suelto,
         no derive de un DTO, sin ejemplo en el prompt)
```

---

## 3. Requerimientos funcionales

> Convención: cada RF es **verificable** (criterio en §4) y traza a la sección del ADR-043 entre
> paréntesis. Los RF A* cubren **tasks**, los B* cubren **workflow**, los C* son transversales.

### RF-A1 · Contrato de task completo con `output_schema` (ADR §1)

Cada task pública registra su contrato completo:

```python
registry.register("kg_task_context", fn, metadata={
    "description": "Contexto de un archivo antes de editarlo (joins cpg×ckg×delta×trace).",
    "input_schema": {"type": "object",
                     "properties": {"file": {"type": "string"}, "task_id": {"type": "string"}}},
    "output_schema": {"type": "object",
                      "properties": {"phase": {"type": "string"},
                                     "entities": {"type": "object"},
                                     "insights": {"type": "array"},
                                     "risk": {"enum": ["LOW", "MED", "HIGH"]}}},
    "scope": "public",
    "tags": [...],
    "example": 'walker <project>/kg_task_context \'{"file":"src/auth.py"}\'',
})
```

- [A1.1] Toda task pública (`scope="public"`) declara `input_schema` Y `output_schema`.
- [A1.2] `output_schema` describe el shape de **`TaskResult.data`** (no el envelope).

### RF-A2 · El MCP deriva el contrato, no lo redefine (ADR-043 §2)

- [A2.1] `McpServer.tool()` y `ToolDefinition` pasan `output_schema` desde el registro
  (`t.get("output_schema")`), sin re-escribirlo (infra ya hecha — ver §5 "ya implementado").
- [A2.2] Las tools manuales del workflow (`status`, `submit`, `transition`, `agent_context`, ...)
  migran a declarar `output_schema` con el mismo patrón.
- [A2.3] `inputSchema` visibles == `input_schema` de la task (verificación automatizada, C-T1).

### RF-A3 · El cuerpo que recibe la task es el del schema (sin re-interpretación) (ADR-043 §3)

- [A3.1] `dispatch_public_task`/adapters dejan de mapear claves intermedias (`layers→file`).
- [A3.2] La task consume exactamente las claves del `input_schema` (adicionales → error de schema).

### RF-A4 · Contratos de dominio como fuente de los esquemas (ADR-043 §4.2)

Para cada feature con schemas/VOs existentes, los DTOs de contrato se derivan del schema:

```
schema JSON ──→ DTO/VO (dataclass frozen + from_dict + as_dict + to_schema) ──→ input_schema/output_schema
```

- [A4.1] `features/<feature>/contracts.py` por feature (por orden §8 del ADR).
- [A4.2] Renombre de `task_execution/application/view_models.py` → `application/contracts.py` (corrección de terminología ADR §4.1: NO son ViewModels, son DTOs de contrato).
- [A4.3] `to_schema()` genera el JSON Schema publicado (nunca escrito a mano por el adapter).
- [A4.4] `from_dict()` valida en construcción (mismas reglas que `output_schema`).

### RF-B1 · Provisión tipada en el workflow (ADR-043 §9.2-A)

- [x] [B1.1] Los `context providers` devuelven **DTOs** (no texto libre): `task_spec`, `diff`, `test_results`, `trace/reflection`, `ckg`, `cpg/business`, `delta`, `project`.
- [x] [B1.2] Cada DTO expone `to_schema()`; el `context_bag` (workflow) expone el **schema del contexto** que el agente recibe.
  - SSOT: `engine/context/contracts.py` → `CONTEXT_SCHEMAS` (mapa de los 9 providers
    `business, ckg, cpg, delta, diff, project, reflection, test_results, trace`).
  - `ComposedContext.context_schemas` puebla el schema de cada capa del agente
    (desde `output_schema()` del provider; fallback SSOT si no lo implementa).
  - `workflow.json` declara la sección `context_schema` (criterio visible del workflow).

### RF-B2 · Salidas de `llm_task` con DTO de respuesta (ADR-043 §5.2-B)

- [x] [B2.1] Todos los 16 sub_states `llm_task` tienen DTO de respuesta
  (p.ej. `SelectOutput`, `GatherSpecsOutput`, `AnalyzeOutput`, `SprintGoalOutput`,
  `AddTaskOutput`, `PlanReviewOutput`, `DesignAnalyzeOutput`, `ImplementWorkOutput`,
  `CodeReviewOutput`, `DiagnoseOutput`, `KnowledgeUpdateOutput`,
  `SprintPlanningOutput`, `TaskSpecOutput`, `SprintRetroOutput` y los outputs de
  knowledge capture) con `from_dict()` que valida required/tipos/enum contra el
  `output_schema` del node.json.
- [x] [B2.2] `engine/workflow/contracts.py` concentra estos DTOs (archivo nuevo)
  + registro `WORKFLOW_OUTPUTS` (node, sub_state) → DTO + `validate_llm_task_output()`.

### RF-B3 · Prompts generados del contrato (ADR-043 §5.2-C)

- [B3.1] La sección de **contexto** del prompt se arma desde los DTOs de entrada (`to_schema`).
- [B3.2] La sección de **respuesta** del prompt incluye el schema y un ejemplo real del DTO de salida.
- [B3.3] El `template` dejas de ser texto libre: se renderiza del contrato, manteniendo los 47 templates funcionales.

### RF-C1 · Documentación del contrato (`walker://help/<tool>`) (ADR-043 §4)

- [x] [C1.1] `walker://help/<tool>` se genera desde metadata: description + example + input/output schema.
  - `build_tool_help()` (`engine/application/tool_catalog.py`) deriva la doc del
    registro (fusión ModuleRegistry + TaskRegistry SSOT); recurso dinámico
    `walker://help/{tool}` registrado en `_build_server` (mcp_adapter).
- [x] [C1.2] Incluye el ejemplo real (que además valida como JSON contra el input_schema).
  - ejemplo = `metadata.example`; el texto render refleja description + usage +
    example + ambos schemas (tests: `test_f4_help_rest.py`).

### RF-C2 · REST y adapters no re-definen el contrato (ADR-043 §4)

- [x] [C2.1] `RestRoute.flags_builder` traduce solo URL → body declarado (sin campos inventados).
- [x] [C2.2] `mcp_adapter.py` y `_module_tool_handler` leen `inputSchema`/`outputSchema` del registro.
  - `_sync_catalog_core` adopta herramientas y rutas del ModuleRegistry con
    `output_schema`, sin reescribir schema a mano (tests: `test_rest_route_no_redefine_contrato`).

### RF-C3 · Tests de coherencia automática (ADR-043 Implementation steps)

- [x] [C3.1] Suite de coherencia: schema MCP ≡ schema de la task; y el help válido contra el input schema.
  - `test_f5_schema_coherence.py`: tools/list proyecta schemas del registro
    (fusión ModuleRegistry + TaskRegistry), outputSchema de las 4 tools de fase,
    y help derivado del mismo SSOT (C1.2).
- [x] [C3.2] Test de regresión: la salida de cada task cumple su `output_schema` (validación JSON Schema).
  - Salida conforme pasa `from_dict`; la inválida eleva `ContractOutputError`
    (`validate_llm_task_output`, F3) — `jsonschema` si existe, fallback `_basic_validate`.
- [x] [C3.3] Test del workflow: outputs de `llm_task` que pasan por `from_dict` sin error.

---

## 4. Criterios de aceptación

Globales:
- [x] 100% de las tasks públicas con `input_schema` Y `output_schema` — el catálogo emite SIEMPRE ambos (dict, con `{}` de fallback fail-open para no romper legacy): F5 `test_c3_tools_list_expone_schemas_de_registro`.
- [x] `inputSchema` visible en MCP === `input_schema` registrado (el catálogo y el MCP leen el mismo registro; desalineación histórica `agent_context` corregida en F1).
- [x] `outputSchema` visible en MCP para toda task pública (catálogo fusionado ModuleRegistry+TaskRegistry con `outputSchema`, F5).
- [x] `walker://help/<tool>` generado desde metadata: description + example + schemas (tests: `test_f4_help_rest.py` — `build_tool_help` + recurso `walker://help/{tool}` + render de ambos schemas).
- [ ] `features/<feature>/contracts.py` existe con DTOs derivados de esquemas (al menos en
      task_execution, cognitive_knowledge, delta_tracking, code_analysis, event_tracing, engine/workflow).
- [x] `view_models.py` renombrado a `contracts.py` (sin imports de `view_models.py`).
- [x] Workflow: 16 sub_states `llm_task` con DTO de respuesta y `from_dict` validando (antes 0).
- [x] Los 9 context providers devuelven DTO con `to_schema` (hoy: 9/9 — `CONTEXT_SCHEMAS` en `engine/context/contracts.py`; `ComposedContext.context_schemas` expone la forma de cada capa que el agente recibe).
- [x] F1: 4 tools de fase (`kg_task_context`, `kg_diagnose`, `kg_qa_evidence`, `kg_sprint_evidence`) registradas con `output_schema` derivado de `to_schema()` (tests: `test_phase_intelligence.py`).
- [x] Sub-espígelos: **389 tests** pasan (348 previos + 41 nuevos F1-F5).

Por fase (según orden de aplicación ADR §8):
| Fase | Feature | Resultado requerido |
|---|---|---|
| F1 | task_execution (renombre DDTOs + 4 tools de fase) | `contracts.py` con TaskFile/FailureDiagnosis/QaCoverage/SprintEvidenceDTOs con from_dict/as_dict/to_schema; 4 tools registradas con output_schema |
| F2 | cognitive_knowledge (kg_store/kg_search) | DTOs de `ckg.schema.json` + KnowledgeFact; output_schema declarado |
| F3 | delta_tracking (delta_analyze) | DTOs de plan.schema.json + Spec/Spring; output_schema declarado |
| F4 | code_analysis (cpg_analyze/analyze_code) | converts de `QueryGraphUseCase.read/grep` a DTOs; output_schema declarado |
| F5 | event_tracing (trace_push) | TraceEvent DTO con to_schema y contrato completo |
| F6 | engine/workflow (§9 completo) | context providers → DTOs; sub_states → DTOs respuesta; prompts del contrato |

---

## 5. Estado de implementación actual (verificado)

Ya implementado (ADR-043 steps `[x]`) — no se re-escribe, se usa como base:

| Paso | Archivo | Estado |
|---|---|---|
| `ToolDefinition.output_schema` | `features/engine/interface/contracts.py:24` | Hecho |
| `McpServer.tool(output_schema)` + `_InternalRegistry` | `libs/mcp_server.py:97` | Hecho |
| `Tool.output_schema` (frozen) + `to_definition()` emite outputSchema | `features/cli_interface/domain/entities.py` | Hecho |
| `mcp.py::_register_public_tasks` pasa `output_schema` | `features/engine/mcp.py` | Hecho |
| `agent_context` corregido a `{file, task_id, text}` + output_schema | `features/engine/mcp.py:22` | Hecho |
| `mcp_adapter` propapaga `outputSchema` | `features/cli_interface/mcp_adapter.py:104` | Hecho |
| DTOs de fase (contratos) | `features/task_execution/application/contracts.py` | **Renombrado (F1)** desde `view_models.py`; con `from_dict`/`as_dict`/`to_schema` |
| 4 tools de fase públicas | `features/task_execution/tasks/phase_intelligence.py` | **Nuevo (F1)** — `kg_task_context`, `kg_diagnose`, `kg_qa_evidence`, `kg_sprint_evidence` registradas con `input_schema` + `output_schema` derivado |

### Fuentes de verdad actuales que NO se deben duplicar

- `schemas/adr-walker.schema.json` + `schemas/adrs/ADR-WALKER-043.json` (ADR como datos JSON).
- `libs/mcp_server.py`, `features/engine/mcp.py`, `features/cli_interface/mcp_adapter.py`.
- `features/engine/workflow/nodes/*.node.json` (8 nodos, 16 `llm_task`, output_schema declarados).
- `features/prompts/templates/nodes/**/*.prompt.json` (47 templates).
- `features/<feature>/context/providers.py` (9 keys: business, ckg, cpg, delta, diff, project, reflection, test_results, trace).

---

## 6. Fases de entrega y criterios de salida

| Fase | Entregables | Criterio de salida |
|---|---|---|
| **F1 — Contrato de tasks** | output_schema en las 4 tools de fase (`kg_task_context`, `kg_diagnose`, `kg_qa_evidence`, `kg_sprint_evidence`); renombrado `view_models.py`→`contracts.py`; `contracts.py` de task_execution con `to_schema`/`from_dict`. | ✅ **Completada** — `phase_intelligence.py` registra las 4 con input+output_schema derivado de `to_schema()`; suite 354 passed |
| **F2 — Workflow entrada** | DTOs de contexto en los 9 providers con `to_schema()`; `context_schema` en workflow.json. | ✅ **Completada** — `engine/context/contracts.py` con `CONTEXT_SCHEMAS` (9 providers, fail-open `{}`); `ComposedContext.context_schemas` poblado por el composer (tras `output_schema()` → fallback SSOT); `context_schema` declarado en `workflow.json` + schema; suite 372 passed |
| **F3 — Workflow salida** | ✅ **Completada** — `engine/workflow/contracts.py` con 16 DTOs de respuesta (`WORKFLOW_OUTPUTS`); validación por `from_dict` en el submiter (`use_cases._validate_output` paso 6b, gate `SubState.is_llm_task()`). | Traza `submit` — respuesta fuera de schema → `ValidationError`/`SchemaViolation` → error 422 con detalle |
| **F4 — Help & REST** | `walker://help/<tool>` generado desde metadata; routes sin inventar campos. | ✅ **Completada** — `build_tool_help()` (SSOT registro) + recurso dinámico `walker://help/{tool}` registrado en `_build_server`; `_sync_module_to_server` propaga schemas sin re-definir; tests `test_f4_help_rest.py` 7: help==description+schemas+example, resources/list expone el patrón, error 404 para tool inexistente, flags_builder sin campos inventados |
| **F5 — Tableau coherencia** | Suite C1–C3 (tests de coherencia MCP/task + DTO schema). | ✅ **Completada** — `test_f5_schema_coherence.py` (9 tests): tools/list con schemas del registro, outputSchema de fase, help derivado, `validate_llm_task_output` fail-open/schema, CONTEXT_SCHEMAS 9 y 16 DTOs; suite completa 389 passed |

Orden recomendado: **F1 → F3 → F2 → F4 → F5** (primero el ciclo de tasks público, luego workflow, cierra con observabilidad).

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Migración de ~9 tasks genera salida `DEFAULT_SCHEMA` transitorio | `DEFAULT_SCHEMA` con **deuda marcada** (en proyecto, ticket CAP); objetivo final 0 default |
| Compatibilidad del agente: cambio de contrato puede romper clientes | Non-negotiable: `input_schema visible == contrato real`; migración gradual por feature |
| Workflow es el más complejo (context + prompts + validación) | Fases F2/F3 después de F1 (base de DTOs de tasks ya tipada), con tabla por sub_state como guía |
| `view_models.py` renombrado rompe imports | Renombre + actualización de imports abarcado en F1, suite 348 como ancla |

---

## 8. Fuentes del plan

- ADR-WALKER-043 (SSOT del contrato, §4.1 terminología, §4.2 principio DTO, §8 plan por feature, §9 workflow).
- SPEC-WALKER-019 (formato de este documento, para la familia de SPEC).
- Verificados en repo: contratos actuales, registro de tasks, node.json (16 llm_task), 47 prompts, 9 providers.