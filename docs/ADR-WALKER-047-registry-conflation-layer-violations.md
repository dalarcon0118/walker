# ADR-WALKER-047: Confusión conceptual de registries y violaciones de capas — inventario de bugs y problemas de diseño (modelo refinado)

> **Status:** Propuesta (pendiente de aprobación)
> **Fecha:** 2026-08-13
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-023 (query use cases esconden infraestructura), ADR-WALKER-043/044 (SSOT del protocolo), ADR-WALKER-045/046 (composition root), ADR-WALKER-048 (recuperación determinista), `capa_rout.md` (D2: dispatch, D3: tasks usan servicios de módulo)
> **Revisión 2 (2026-08-13):** refinamiento del modelo por el PO — ModuleRegistry es mecanismo de DI (no catálogo de tools); la fuente de la verdad de tasks/tools/comandos es el TaskRegistry de main; las tasks y side effects son conceptos del workflow que viven en main; los features son módulos puros que exportan servicios y casos de uso.

---

## 1. Problema

Desde el punto de vista del dominio, Walker tiene **mecanismos con roles distintos** que la implementación confunde:

| Mecanismo | Rol conceptual (modelo refinado) | Dónde vive hoy |
|---|---|---|
| `features/{feature}` (bounded contexts) | **Módulos puros**: solo hacen lo que deben hacer como bounded context. Exportan **servicios y casos de uso** (capa pública). NO tienen tasks, NO tienen tools, NO tienen ToolDefinitions | `features/cognitive_knowledge/application/use_cases.py`, `features/code_analysis/application/...`, etc. |
| TaskRegistry (de main) | **FUENTE DE LA VERDAD (SSOT)**: convierte los casos de uso y servicios de los features en 1) tasks del workflow, 2) tools del agente (MCP), 3) comandos CLI (si aplican) | `core/engine/infrastructure/task_registry.py` + `features/main/task_execution/tasks/*` |
| Task (concepto del workflow) | **Pertenece a main**, nunca a un feature. La task llama al servicio/use case del feature — nunca a su infraestructura | `features/main/task_execution/tasks/` |
| Tool MCP | **Concepto del agente**: main lee sus tasks públicas y las **inyecta dinámicamente** desde el TaskRegistry hacia las tools MCP (y comandos CLI si aplican). La tool NO sabe de use cases | `core/engine/application/tool_catalog.py` (fusión actual — bug) |
| ModuleRegistry | **Mecanismo de DI** (similar a NestJS): registra servicios por clave lógica. **NO debe tener ToolDefinition** | `core/engine/interface/contracts.py` + `mcp.py` de features (declaran ToolDefinitions — bug) |
| Side effects | **Concepto del workflow**: se crean y registran en **main**. Si un side effect decide llamar a un feature, el feature exporta la operación como use case/servicio | `features/delta_tracking/side_effects/` (dentro de un feature — bug) |
| Domain vs System registry | **Clasificación del workflow**: tasks internas del grafo vs invocables desde fuera | `domain_task_registry.py` / `system_task_registry.py` |
| TaskIntelligenceService | **SSOT de la consulta multi-capa**: define cómo consultar múltiples capas de memoria haciendo joins entre ellas; se convierte en tasks específicas (workflow, desde main) y en tools (MCP) | `features/main/task_execution/application/intelligence_service.py` (ya vive en main ✓) |

**El modelo correcto (consenso PO, revisión 2):**

1. **Los features son módulos puros.** Su única cara pública son sus **servicios y casos de uso**. No existen tasks dentro de los features.
2. **La task es un concepto del workflow** → vive en **main**. Main trata los servicios/use cases de los features como tasks.
3. **La tool es un concepto del agente** → main define qué se consume como tool (sus tasks públicas + comandos del workflow como submit/transition). La fuente de la verdad es **el TaskRegistry de main**, que convierte los casos de uso/servicios de los features en tasks del workflow, tools del agente y comandos CLI.
4. **ModuleRegistry = mecanismo de DI** (como NestJS), no un catálogo de tools.
5. **Las tasks tienen nombre semántico** (operación de negocio, no infraestructura).
6. **TaskIntelligenceService = SSOT de la consulta multi-capa** con joins → tasks específicas (workflow) + tools (MCP).

### 1.1 Bugs y problemas de diseño identificados (verificados en código)

| # | Bug / problema | Evidencia | Impacto |
|---|---|---|---|
| **B-1** | **ModuleRegistry declara ToolDefinitions (mcp.py de features) y `list_tools()` fusiona ModuleRegistry + TaskRegistry con prioridad silenciosa.** Las tools son concepto del agente (main), no de los módulos | `features/cognitive_knowledge/mcp.py:46-62` (RestRoutes + ToolDefinitions legacy); `core/engine/application/tool_catalog.py:110-137` fusiona con dedupe `seen` priorizando el ModuleRegistry (líneas 111-114) | La metadata del nombre perdedor se pierde sin error (deuda A); el catálogo de tools no sale de la fuente de verdad (TaskRegistry de main) |
| **B-2** | **Las tasks de main tocan la infraestructura de los features en vez de sus servicios/use cases.** | `features/main/task_execution/tasks/kg.py:171-244`: `kg_schema`/`kg_search` resuelven el searcher por DI (`cognitive_searcher`) y llaman `cg.search/search_many/schema` directo ∈ infra legacy (docstring línea 1: *"thin adapters to features/engine/legacy searcher"*). `kg.py:530`: branch sin `chain_dir` usa `ckg_service()` directo. El propio feature documenta la intención contraria: `use_cases.py:10-14` ("GatherCkgContextUseCase → tool kg_search"). ADR-023 §7.1: CognitiveGraph solo se instancia en infrastructure/ | Se duplican caminos de acceso al mismo índice; cambios internos del feature rompen tasks públicas; viola "la task llama al servicio del feature" |
| **B-3** | **Doble fuente del contrato de la misma capability: metadata rica en el TaskRegistry (tasks) + ToolDefinitions/RestRoutes en el ModuleRegistry (módulos).** | `kg.py:565-745` (description/usage/input_schema en metadata); `features/cognitive_knowledge/mcp.py:46-62` (RestRoutes con flags_builder por tool); la fusión de `list_tools()` junta ambas | El contrato de una tool está dividido; la fuente de la verdad no es única (debe ser el TaskRegistry de main) |
| **B-4** | **Dispatch a dos caminos + re-parsing en las tasks: el adapter no sabe a qué capa llama y adivina por la presencia de `route_def`.** | `features/main/cli_interface/mcp_adapter.py:386-460` (Camino A: route_def; Camino B: fallback `{project_id}/{name}`); `kg.py:227-243` compensa re-parseando `_payload` vs `data` directa y aliases `index`→`_index`, `query`→`text` | Cada tool pública debe tolerar dos formas de llegada de argumentos; bugs de dispatch silencioso |
| **B-5** | **Bug concreto de código muerto: el bloque "Camino B" está duplicado en `_call_tool`.** | `features/main/cli_interface/mcp_adapter.py:450-460` y `462-472` — el segundo bloque es **inalcanzable** (la línea 460 ya hace `return`) | Riesgo de merge manual: si alguien "corrige" el bloque muerto creyendo que es el camino vivo, rompe el fallback |
| **B-6** | **Tasks y side effects (conceptos del workflow) viven DENTRO de un feature.** | `features/delta_tracking/tasks/` ("Tasks de dominio del sprint", `__init__.py:1`: implementation, planning, review, verification, mocks) y `features/delta_tracking/side_effects/` (create_adr, planning, spec_transform); el discovery `container.py:311` escanea `features/*/tasks/` (perpetúa la anomalía). CONTRASTE: `features/main/task_execution/tasks/` (20 archivos) ya es el lugar correcto | El feature deja de ser un módulo puro; el discovery mezcla el registro de tasks de features con las de main |
| **B-7** | **Dispatchers stringly-typed deprecados pero registrados y vivos.** | `features/main/task_execution/tasks/ckg_analyze.py:113-155` (`action=search/save/active/supersede/context`); `kg.py:707-727` registra `kg_multi` como pública pese a ADR-023 §3/§9; tests que las usan: `tests/test_rag_pipeline_bdd.py:78`, `tests/test_agent_e2e_bdd.py:647` | La eliminación anunciada (Fase 2 de `memory-tools-naming-convention.md`) no se ejecutó: siguen expuestas al agente y bloquean tests si se retiran sin migrarlos |
| **B-8** | **kg_search/recalling_knowledge (multi-memoria) no pasa por TaskIntelligenceService (SSOT de joins).** | `kg.py` usa el searcher legacy; el SSOT de la consulta multi-capa con joins (`TaskIntelligenceService`, `intelligence_service.py:272-349`) no es la vía | Dos implementaciones de la misma consulta multi-memoria; el SSOT no es la fuente única |

### 1.2 Lectura estructural

Causa raíz única: **la fuente de la verdad no es única**. El TaskRegistry de main DEBE ser el SSOT que convierte servicios de features en tasks + tools + comandos CLI; en su lugar conviven ModuleRegistry (con ToolDefinitions) + TaskRegistry + fusión con prioridad silenciosa. B-1/B-3/B-8 son esa confusión; B-2/B-6/B-7 sus consecuencias; B-4/B-5 sus artefactos.

---

## 2. Decisión

**Adoptar el modelo refinado (sección 1): el TaskRegistry de main es la fuente de la verdad que convierte los casos de uso/servicios de los features en tasks del workflow, tools del agente y comandos CLI; ModuleRegistry es un mecanismo de DI sin ToolDefinitions; los features son módulos puros.**

Decisiones de diseño:

1. **TaskRegistry (de main) = SSOT.** Convierte los casos de uso y servicios de los features en: tasks del workflow, tools del agente (MCP) y comandos CLI (si aplican). Las tasks tienen **nombre semántico**.
2. **Tools = concepto del agente.** Main lee sus tasks públicas del TaskRegistry y las **inyecta dinámicamente** como tools MCP (y comandos CLI si aplican). La tool NO sabe de use cases: la cadena es `tool (main) → task (main) → use case/servicio (feature) → infra (privada)`.
3. **ModuleRegistry = mecanismo de DI** (similar a NestJS): registra servicios por clave lógica. **NO declara ToolDefinition**.
4. **Features = módulos puros**: exportan servicios y casos de uso; solo hacen lo que deben como bounded context. Sin tasks, sin tools.
5. **Side effects = concepto del workflow**: se crean y registran en main. Si un side effect llama a un feature, el feature exporta la operación como use case/servicio.
6. **TaskIntelligenceService = SSOT de la consulta multi-capa** (joins entre memorias) → tasks específicas para el workflow (desde main) y tools (desde MCP). `kg_search`/`recalling_knowledge` delegan en él.
7. **Eliminar**: dispatchers stringly-typed (`ckg_analyze action=...`, `kg_multi`), bloque muerto duplicado de `mcp_adapter.py:462-472`, fusión con prioridad silenciosa de `list_tools()`, ToolDefinitions en mcp.py de features, y el discovery de tasks en `features/*/tasks/`.

---

## 3. Consecuencias

### 3.1 Positivas

- **Fuente de la verdad única**: el TaskRegistry de main define qué es task, tool y comando; un cambio de nombre semántico se propaga automáticamente a los tres canales (workflow, MCP, CLI) — desaparece la deuda de names (ADR-047 B-7 habilitada por ADR-048).
- **Features puros**: exportan solo servicios/casos de uso; sus internals dejan de ser alcanzables desde el exterior.
- **ModuleRegistry recupera su rol de DI**: sin ToolDefinitions, sin catálogo de tools, sin fusión.
- **Tools dinámicas**: main inyecta sus tasks públicas al MCP; no hay dos fuentes de contrato (B-3 muere por construcción).
- **La consulta multi-memoria tiene un solo SSOT** (TaskIntelligenceService) — B-8 muere por construcción.
- **Código muerto eliminado** (bloque duplicado) y dispatchers deprecados cumplen su anuncio de eliminación.

### 3.2 Negativas / Trade-offs

- **Refactor amplio**: toca tool_catalog, mcp.py de features, delta_tracking/tasks + side_effects, container discovery, mcp_adapter y tests. Por fases (sección 8).
- **Dos modelos coexisten durante la transición**: la fusión seguirá activa hasta Fase 4; riesgo de que una capability nueva siga cayendo en la confusión (mitigado en failure_scenarios).
- **Mover tasks/side effects de delta_tracking a main cambia imports y registro**: un import roto = tareas no registradas (mitigado en failure_scenarios).
- **La inyección dinámica de tools** cambia la forma en que el MCP construye su catálogo (de declarativo en mcp.py a dinámico desde el TaskRegistry) — el plugin/prompts deben migrar a capas (ADR-048) antes de renombrar.
- **El Camino B (fallback dinámico) tiene un edge conocido**: una task cuyo nombre colisione con una ruta de controller (status, list, info...) matchearía la ruta REST equivocada. Hoy no ocurre porque los públicos reales son kg_*/scan_*/build_*; la inyección dinámica debe resolver tools contra el TaskRegistry antes que contra rutas REST.

---

## 4. Arquitectura objetivo

```
        MCP / CLI (agente)
          ▲
          │  tools = inyección dinámica (main): tasks públicas del TaskRegistry
          │         + comandos del workflow (submit, transition)
          │  la tool NO sabe de use cases
          ▼
┌──────────────────────────────────────────────────────────┐
│  features/main  (capa del workflow — composition root)    │
│  ├─ TaskRegistry  = SSOT                                  │
│  │    convierte servicios de features en:                 │
│  │      → tasks del workflow (nodos/edges)                │
│  │      → tools del agente (inyección → MCP)              │
│  │      → comandos CLI (si aplican)                       │
│  ├─ task_execution/tasks/   (tasks SEMÁNTICAS, de main)   │
│  ├─ task_execution/side_effects/ (workflow concept)       │
│  ├─ TaskIntelligenceService = SSOT multi-capa (joins)     │
│  │    → tasks específicas (workflow) + tools (MCP)        │
│  └─ ModuleRegistry = DI (como NestJS): servicios,         │
│        SIN ToolDefinition                                 │
│                    ▲ llaman a la capa pública              │
│                    ▼                                       │
│  features/{feature}  (módulos puros)                       │
│  ├─ application/use_cases.py + servicios ← ÚNICA cara      │
│  │     pública del feature                                 │
│  └─ infrastructure/  (privada)                             │
└──────────────────────────────────────────────────────────┘
```

**Reglas de dependencia:**
1. `core/engine/` NO importa `features/` (ADR-045, sin cambios).
2. Las tasks y side effects viven en `features/main/` (concepto del workflow); los features no contienen tasks.
3. Las tasks de main llaman a servicios/use cases de los features — nunca a su infraestructura.
4. La fuente de la verdad de tools = TaskRegistry de main (inyección dinámica); `list_tools()` NO fusiona fuentes ni usa prioridad silenciosa.
5. ModuleRegistry es DI: registra servicios, NO ToolDefinitions.
6. Las tools de memoria no se nombran en prompts/hook: los prompts referencian capas (ADR-048).

---

## 5. Mapa de archivos afectados (implementación futura, no incluida en este ADR)

| Archivo | Cambio previsto |
|---|---|
| `features/main/task_execution/tasks/kg.py` | `kg_search` delega en los repos de los features (ckg/cpg/delta/trace) y la multi-memoria pasa por `TaskIntelligenceService` — **HECHO (Fase 3, B-2/B-8)**; `kg_schema` aún por el searcher (nota paso 4); eliminar `kg_multi`; names semánticos (Fase 2 del naming) |
| `features/main/task_execution/tasks/ckg_analyze.py` | Eliminar (dispatcher stringly-typed, Fase 2 del naming) |
| `features/delta_tracking/tasks/` → `features/main/task_execution/tasks/` | Migrar (tasks = concepto del workflow, viven en main) — **HECHO (Fase 3)** |
| `features/delta_tracking/side_effects/` → `features/main/task_execution/side_effects/` | Migrar (side effects = concepto del workflow, se registran en main); los features exportan use cases/servicios si son llamados — **HECHO (Fase 3)** |
| `features/{feature}/mcp.py` + `core/engine/mcp.py` | Eliminar ToolDefinitions; ModuleRegistry queda como DI (servicios + rutas) — **HECHO (Fase 4)**: `core/engine/mcp.py` y `code_analysis/mcp.py` declaran `tools=[]`; las 6 tools del workflow y cpg_analyze viven en el TaskRegistry de main |
| `core/engine/application/tool_catalog.py` | `list_tools()` = inyección dinámica desde el TaskRegistry de main (tasks públicas); sin fusión ni `seen` — **HECHO (Fase 4)** |
| `features/main/container.py` | Discovery de tasks: dejar de escanear `features/*/tasks/`; main registra declarativamente sus tasks y side effects — **HECHO (Fase 3)** |
| `features/main/cli_interface/mcp_adapter.py` | Eliminar bloque duplicado (462-472, hecho); con ModuleRegistry sin tools, `_call_tool` resuelve todo contra el Router (controller routes o `:taskName`) — **HECHO (Fase 4, por construcción)** |
| `tests/` | Migrar `test_rag_pipeline_bdd.py:78`, `test_agent_e2e_bdd.py:647`, `test_kg_search_integration_bdd.py` (Fase 2); tests de completitud del TaskRegistry de main — **HECHO (Fase 3, `test_registry_completeness_bdd.py` extendido)** |

---

## 6. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | Una tool pública pierde su metadata silenciosamente (description/schema vacíos en `tools/list`) | La fusión de `list_tools()` sigue viva durante la transición; el `seen` prioriza el ModuleRegistry | Fase 4 elimina la fusión; mientras tanto, guard explícito: si el TaskRegistry de main y el ModuleRegistry registran el mismo nombre, log warning + test de colisión | Test: registrar la misma capability en ambas fuentes con metadata distinta → el catálogo sale del TaskRegistry y emite warning |
| 2 | Una task de main toca infraestructura directa (searcher/repo) y rompe cuando el feature cambia su internals | Migración incompleta de B-2; un dev reutiliza el patrón `_resolve_searcher` por costumbre | Linter rule: prohibir `CognitiveGraph`/`Searcher`/`cg.search` en `features/main/task_execution/`; las tasks llaman a servicios/use cases | CI grep: `grep -rn "cg.search\|CognitiveGraphSearcher\|cognitive_searcher" features/main/task_execution/` |
| 3 | Eliminar `kg_multi`/`ckg_analyze` rompe la suite E2E y agentes conectados | Deprecación anunciada (ADR-023) sin migración de dependientes (plugin, prompts, tests) | Migrar plugin/prompts a capas (ADR-048) y tests a las tools semánticas ANTES de retirarlas; eliminación tras suite verde | `pytest` contra la rama sin los dispatchers + grep de 0 names legacy en prompts/plugin |
| 4 | Migrar `delta_tracking/tasks` y `side_effects` a main deja el workflow sin tareas registradas (nodos/edges rotos) | El discovery por convención (`features/*/tasks/`) no se actualizó al mover los imports | Test de completitud del TaskRegistry de main (patrón AGENTS.md): conjunto esperado de tasks por nodo/sub_state del workflow; fallo explícito si falta alguna | `test_task_registry_completeness_bdd.py`: `EXPECTED` ⊆ `registry.list_tasks()` |
| 5 | El adapter vuelve a mezclar canales: una tool despachada con `_channel` equivocado o payload re-parseado dos veces | Unificación incompleta del dispatch; se conserva el Camino B y el re-parsing `_payload`/aliases | El dispatch resuelve tools contra el TaskRegistry de main (una sola vía); el canal se fija en un solo punto | Test E2E: `tools/call` recalling_knowledge con body JSON → TraceGraph con `channel=mcp` e índice solicitado |
| 6 | La inyección dinámica de tools colisiona con rutas REST de controller (status, list, info...) | Una task pública cuyo nombre coincide con una ruta de controller se despacha a la ruta equivocada | La resolución de tools consulta el TaskRegistry de main ANTES que las rutas REST; edge documentado y cubierto por test | Test: registrar una task pública "status" → `tools/call` resuelve la task, no el controller REST |
| 7 | Un rename semántico de task rompe el plugin o un prompt que aún lista el nombre viejo | Migración incompleta (ADR-048): un consumidor quedó hardcodeando el name viejo | La regla de capas estables aplica a prompts Y plugin; CI grep de 0 names legacy | `grep -rn "kg_multi\|kg_search\|save_to_ckg\|ckg_analyze" .opencode/plugins/ features/main/prompts/` → 0 resultados |

---

## 7. Checklist de segundo orden

1. **¿Qué módulos importan esto?**
   - `core/engine/application/tool_catalog.py` (fusión → inyección desde TaskRegistry), `core/engine/interface/contracts.py` (ModuleRegistry → DI), `core/engine/infrastructure/task_registry.py` + vistas.
   - `features/main/task_execution/tasks/*` (kg.py, phase_intelligence.py, delta tasks migradas), `features/main/container.py` (discovery), `features/main/cli_interface/mcp_adapter.py` (dispatch).
   - `features/{cognitive_knowledge, code_analysis, event_tracing}/mcp.py` (quitar ToolDefinitions), `features/delta_tracking/tasks/` + `side_effects/` (migrar).
   - `tests/` (rag_pipeline, agent_e2e, kg_search_integration, cli_walker, mcp_tools_list_equivalence).

2. **¿Qué side effects dispara?**
   - Los side effects de dominio (transform_spec, create_adr, planning) se crean y registran en main (no en delta_tracking); su ejecución en el `SideEffectEngine` no cambia.
   - Las tools kg_* trazan `kg_query` (best-effort) — al delegar en TaskIntelligenceService/QueryManager se conserva el tracing.
   - Mover tasks cambia el orden de registro del boot (idempotencia, ADR-046).

3. **¿Qué eventos de dominio publica?**
   - Ninguno nuevo. Los eventos del workflow siguen emitiéndose desde el engine; TraceGraph continúa recibiendo `task_execute`/`kg_query`.
   - Cambio indirecto: la retirada de `kg_multi`/`ckg_analyze` deja de emitir sus trazas.

4. **¿Qué tests dependen de este comportamiento?**
   - `test_rag_pipeline_bdd.py:78`, `test_agent_e2e_bdd.py:647` (ckg_analyze) → migrar a tools semánticas.
   - `test_kg_search_integration_bdd.py` (bug F821, aliasing) → reescribir contra TaskIntelligenceService.
   - Suites que usan `list_tools()`/`_mcp_call` → el catálogo saldrá del TaskRegistry de main.
   - Tests de completitud nuevos: TaskRegistry de main, colisiones task↔ruta REST, channel en trace.

5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?**
   - MCP adapter: dispatch resuelve contra el TaskRegistry de main; sin fusión ni bloque duplicado.
   - CLI: `walker list`/`help` reflejan las tools inyectadas desde el TaskRegistry.
   - CKG: kg_search/recalling_knowledge pasan por TaskIntelligenceService; el searcher legacy deja de ser vía de tasks públicas.
   - DeltaGraph: sin cambios de interfaz; el workflow consume el TaskRegistry (tasks migradas a main).
   - Trace: canal y payload consistentes; kg_query trazado desde el SSOT multi-capa.

---

## 8. Implementación por fases

### Fase 1 — Defectos inmediatos (chico, reversible)

1. ~~Eliminar el bloque "Camino B" duplicado muerto en `mcp_adapter.py:462-472`~~ — **HECHO** (731 → 719 líneas, un solo Camino B).
2. Verificar y cerrar los fixes pendientes de deuda A (metadata SSOT en TaskRegistry, alias semánticos) — suite completa verde.

### Fase 2 — Migrar tests y retirar dispatchers stringly-typed (medio)

> **ESTADO: implementada en 2026-08-13** (paso 1 parcial — `kg_multi` queda bloqueado por ADR-048; paso 2 hecho).

1. Migrar `ckg_analyze` en tests y prompts a las tools semánticas; eliminar `ckg_analyze` y `kg_multi` del registro (prerrequisito: ADR-048 — plugin/prompts a capas).
   - **`ckg_analyze` ELIMINADA del registro** — **HECHO**: no aparecía en prompts ni en el plugin (0 hits), solo en tests. Migrados: `tests/test_rag_pipeline_bdd.py:78` (`ckg_analyze action=search` → `kg_search(ctx, {"text": ...})`), `tests/test_agent_e2e_bdd.py:640-663` (`test_mcp_tool_ckg_analyze_returns_nodes` → `test_mcp_tool_recalling_knowledge_returns_nodes` vía `recalling_knowledge` canónico con asserts del shape canónico `{type, index, total, results}`). Eliminado: `features/main/task_execution/tasks/ckg_analyze.py` (task file), `register(server)` legacy + RestRoute `{project_id}/ckg_analyze` de `features/cognitive_knowledge/mcp.py` (eran código muerto: nadie importaba ese módulo), docstrings actualizados (mcp_adapter, use_cases, kg.py, cognitive_knowledge/__init__).
   - **`kg_multi` NO ELIMINADA** — **BLOQUEADA**: prerrequisito ADR-048 pendiente — el plugin `.opencode/plugins/walker-session.js` (líneas 98/121/138/152/248/619) y los prompts aún referencian `kg_multi`; `test_registry_completeness_bdd.py:43` la espera en EXPECTED. Se retira tras la migración de plugin/prompts a capas (Fase 4 paso 5).
2. Reescribir `test_kg_search_integration_bdd.py` contra `TaskIntelligenceService` (sin re-parsing interno) — **HECHO**: el test ya no re-parsea el envelope MCP (`json.loads` de `content[0].text`); cubre (a) B-8: `build_intelligence_service(chain_dir, project_id)` + `get_task_intelligence(text=...)` → `Result[IntelligenceResultVO, Exception]` con las 4 secciones (`ok` bool + `total` int, degradación convention-001) y (b) la regresión F821 (kg_search por la ruta MCP real con proyecto registrado en el registry global → no cae a projects_list, shape canónico). Feature file `tests/bdd/features/kg_search_integration.feature` reescrito acorde.
   - Ajuste extra: asserts desactualizados de `test_mcp_tool_kg_search_returns_delta` (`hits.total` → shape canónico `total`; fallaba en verde por el shape real `{type, index, total, results}`).

### Fase 3 — Tasks y side effects a main; tasks sobre servicios de features (medio)

> **ESTADO: implementada en 2026-08-13** (pasos 1-4; ver nota de `kg_schema` en el paso 4).

1. ~~Migrar `features/delta_tracking/tasks/` → `features/main/task_execution/tasks/` y `features/delta_tracking/side_effects/` → `features/main/task_execution/side_effects/`~~ — **HECHO**: planning/implementation/review/verification/mocks → `tasks/`; create_adr/planning/spec_transform → `side_effects/`. Los features exportan use cases/servicios (planning.py delega en `use_cases_sprint`; side_effects/planning.py usa `spring_service` de delta_tracking). ADR-045 queda parcialmente supersedido (nota al inicio del documento).
2. ~~Cambiar el discovery: `container.py:311` deja de escanear `features/*/tasks/`; main registra declarativamente~~ — **HECHO**: `register_all()` importa `register_task_execution_tasks`/`register_task_execution_side_effects` de `features/main/task_execution`; se eliminan `_discover_and_register_tasks`/`_discover_and_register_side_effects`. `test_container_lifetime_bdd.py` actualizado (inyecta el fallo en `register_task_execution_tasks`).
3. ~~Test de completitud del TaskRegistry de main~~ — **HECHO**: `test_registry_completeness_bdd.py` extiende EXPECTED con las 16 tasks de sprint migradas (incluye save_task_spec, advance_task_index, show_dashboard, edit_sprint, update_sprint_progress, log_rejection_to_ckg, notify_po_escalation, archive_sprint_metrics) + EXPECTED_SIDE_EFFECTS (transform_spec, create_adr_from_spec, save_task_spec, advance_task). Verde.
4. ~~**B-2 (PENDIENTE)**~~ — **HECHO**: `kg_search` ya NO toca el searcher legacy. `kg.py` resuelve los repos de los features vía `_resolve_memory_repos` (factories `cpg_repository`/`ckg_repository`/`delta_repository`/`trace_repository` — la capa de servicios, SSOT ADR-023) y `_kg_search_index` delega por índice: ckg → `ckg_repository.search/query`, cpg → `cpg_repository.search_nodes`, delta → `QueryDeltaUseCase` (get_active_spring/get_specs), trace → `trace_repository.load_traces`; respuesta normalizada `{type, index, total, results}` (mismo shape que `_kg_search_superseded`). **B-8 HECHO**: la multi-memoria pasa por `TaskIntelligenceService.get_task_intelligence(...).as_dict()` (SSOT de joins), con degradación loggeada a ckg full-text (convention-001). Las tasks de sprint migradas YA son B-2-compliant.
   - **Nota `kg_schema`**: se queda resuelto vía `_resolve_searcher` — la introspección de schema (`schema`/`list_indexes`) es una capacidad de la shared lib CognitiveGraph; ningún feature service la expone aún. Pendiente: moverla a un service de cognitive_knowledge (issue para Fase 4). `_resolve_searcher` queda solo como vía de `kg_schema`/`kg_multi` (este último deprecado → Fase 2), prohibida para `kg_search`.
   - **Verificación**: 99 tests verdes (registry 3/3, container 19/19, transform_spec+gw6 17/17, migrated_gaps+workflow_e2e 58/58, mcp_tools_equivalence+lifetime+phase_intelligence 17/17). `test_kg_search_integration_bdd.py` falla pre-existente (kg_search → fallback projects_list por resolución de proyecto en el entorno del test; aislado con stash: falla igual sin este refactor) → se reescribe contra TaskIntelligenceService en Fase 2.

### Fase 4 — Tools dinámicas desde el TaskRegistry; ModuleRegistry como DI (grande)

> **ESTADO: implementada en 2026-08-13** (pasos 1-4; el paso 5 queda bloqueado por ADR-048 — plugin/prompts aún no migrados a capas).

1. ~~**ModuleRegistry = DI**~~ — **HECHO**: `core/engine/mcp.py` y `features/code_analysis/mcp.py` declaran `tools=[]` (quedan SOLO las RestRoutes). `cognitive_knowledge`/`event_tracing`/`delta_tracking` ya eran routes-only. `InMemoryModuleRegistry.all_tools()` queda vacío; sigue registrando servicios por clave (DI, NestJS-like) y rutas.
2. ~~**Inyección dinámica**~~ — **HECHO**: main registra las 6 tools del workflow (status/submit/transition/prepare/list/help) como tasks públicas en el TaskRegistry (`features/main/task_execution/tasks/workflow_tools.py`), con contrato completo (description + input_schema + output_schema migrados de los ToolDefinitions del engine) y handlers que delegan en los use cases de cli_interface (handle_*). El contrato de `cpg_analyze` se mueve a la metadata de su task (antes vivía en el ToolDefinition de code_analysis).
3. ~~`tool_catalog.list_tools()`~~ — **HECHO**: `_list_tools_core`/`_get_tool_core` iteran SOLO las tasks públicas del TaskRegistry canónico (SystemTaskRegistry.discover(scope="public")). Sin fusión con ModuleRegistry, sin `seen`, sin prioridad silenciosa; el input/output schema se sirve desde la metadata del registro (SSOT, ADR-043).
4. ~~Dispatch de una sola vía~~ — **HECHO (por construcción)**: con ModuleRegistry sin tools, `_call_tool` ya no resuelve `tool_def` (siempre None) → todo tool resuelve contra el Router: rutas controller (status/submit/transition/prepare/list/help vía plantillas) o rutas task (`:taskName` → handle_task → execute_public_task). El edge de colisión (escenario 6) muere: no hay herramientas fuera del TaskRegistry que colisionen con rutas. Nota: las 6 tasks del workflow existen para el catálogo + fallback `:taskName`; el dispatch nominal pasa por las rutas controller (mismos use cases).
5. **Nombres semánticos como canónicos (Fase 2 del naming)** — **BLOQUEADO**: prerrequisito ADR-048 (plugin/prompts a capas estables) pendiente.

Verificación (parcial, suite no completa): 35+ tests verdes en las suites críticas — `mcp_tools_list_equivalence` (names idénticos + 6 tools workflow con outputSchema rico + kg_*/semánticas), `f5_schema_coherence`, `call_tool_dispatch` (Camino A "list" → projects_list intacto), `container_lifetime`, `registry_completeness`, `mcp_workflow_tools`, `agent_session` (lifecycle/context), `mcp_adapter`, `mcp_project_id_resolution`, `protocol_contracts`, `workflow_session`. Ajuste: `EXPECTED_PROVIDERS` del container incluye `task_intelligence` (provider ADR-048, omitido antes). Fallos pre-existentes NO tocados: `test_kg_search_integration` (kg_search→projects_list), `test_agent_multi_tool_sequence`, `test_cli_router_bdd` ×3 (verificado por stash: fallan igual sin Fase 4 — fallout de la migración Fase 3).

---

## 9. Referencias

- ADR-WALKER-023 — *query use cases hide infrastructure* (§3: sin dispatchers stringly-typed; §7.1: CognitiveGraph solo en infrastructure/).
- ADR-WALKER-043/044 — SSOT del protocolo y tools (la fusión actual de `list_tools` nace aquí y es la que se corrige).
- ADR-WALKER-045/046 — composition root obligatorio y `AppContainer` (el refactor convive con el container único).
- ADR-WALKER-048 — recuperación determinista (prompts a capas estables) → prerrequisito para Fase 2 y Fase 4.
- `capa_rout.md` — D2 (dispatch) y D3 (tasks usan servicios de módulo; aquí se documenta la misma violación).
- `docs/design/memory-tools-naming-convention.md` — Fase 1 hecha (alias semánticos), Fase 2 pendiente (eliminar kg_*/ckg_analyze/kg_multi).
- Modelo refinado (consenso PO, 2026-08-13): ModuleRegistry = DI (NestJS-like) sin ToolDefinition; TaskRegistry de main = SSOT que convierte servicios de features en tasks/tools/comandos CLI; features = módulos puros; tasks y side effects = concepto del workflow en main; TaskIntelligenceService = SSOT de la consulta multi-capa con joins; tasks con nombre semántico.