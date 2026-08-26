# ADR-WALKER-057: Eliminación de nomenclatura legacy `kg_*` — tools canónicas por capa de aplicación

> **Status:** Ejecutado
> **Fecha:** 2026-08-25
> **Autor:** Walker (agente) + PO
> **Complementa:** ADR-WALKER-055 (CodeAnalysisService + delegation), ADR-WALKER-051 (gaps refactor), ADR-WALKER-045 (composition root único)

---

## 1. Problema

### 1.1 Duplicación de nombres tool

| # | Hallazgo | Evidencia | Impacto |
|---|---|---|---|
| 1 | **`kg_search` como tool MCP y como función interna** | MCP: `_mcp_call("kg_search", ...)` (tests); Interno: `CkgApplicationService.kg_search()` (app service) | Confusión: el agente llama a una tool MCP que internamente llama a un método con el mismo nombre pero interfaz distinta |
| 2 | **Nombres legacy en protocol templates** | `rest.md`: "tareas PUBLICAS (memorias kg_search/kg_schema/cpg/ckg)"; `mcp.md`: "Use `walker_kg_search`"; `protocol_builder.py`: `PHASE_TOOL_NAMES` con nombres viejos | El agente recibe instrucciones con nombres que ya no existen en el tool catalog |
| 3 | **Task registry con nombres dobles** | `SystemTaskRegistry` registraba `kg_extract` y `extract_knowledge`; `handlers.py` listaba `kg_extract` en fallback | El task dispatch resolvía por string — un rename incompleto creaba tasks fantasma |
| 4 | **Plugin walker-session.js con mapeo incompleto** | `walker-session.js` capturaba `kg_multi` pero no lo mapeaba al nombre canónico | Tareas del agente se perdían en el trace |

### 1.2 Por qué importa ahora

El workflow engine (ADR-056) está migrado. El tool catalog es la interfaz pública del agente — si los nombres no son canónicos, el agente toma malas decisiones (usa tool equivocado, falla en dispatch, o ignora el protocolo).

---

## 2. Decisión

**Unificar toda la nomenclatura de tools: eliminar `kg_*` del código activo, protocol templates, tests y plugin. Nombre canónico = nombre de la tool en el tool catalog.**

### Mapping legacy → canónico

| Legacy | Canónico | Capa |
|---|---|---|
| `kg_search` | `recall_knowledge` | MCP tool |
| `kg_multi` | `search_memories` | MCP tool |
| `kg_schema` | `memory_schema` | MCP tool |
| `kg_task_context` | `task_context` | MCP tool |
| `kg_diagnose` | `diagnose_failure` | MCP tool |
| `kg_qa_evidence` | `verify_evidence` | MCP tool |
| `kg_sprint_evidence` | `sprint_evidence` | MCP tool |
| `save_to_ckg` | `documenting_knowledge` | MCP tool |
| `kg_agent_context` | `agent_context` | MCP tool |
| `cpg_analyze` | `inspect_codebase` | MCP tool |
| `kg_extract` | `extract_knowledge` | Task name |

### D1 — App service methods

- `CkgApplicationService.kg_search()` → `.search()`
- `CkgApplicationService.kg_schema()` → `.schema()`
- `CkgApplicationService.kg_multi()` → `.search_many()`
- `CkgApplicationService._kg_search_superseded()` → `._search_superseded()`
- Response markers: `"type": "kg_search"` → `"search"`, `"type": "kg_schema"` → `"schema"`

### D2 — Callers + hints

- `knowledge_helpers.py`: `.kg_search()` → `.search()`, `.kg_schema()` → `.schema()`, `.kg_multi()` → `.search_many()`
- `entities.py`: command_hint actualizado
- `output.py`, `router/`, `memories/`, `query_service.py`, etc.: docstrings y hints

### D3 — Protocol templates + prompt.json

- `rest.md`, `mcp.md`: tool names actualizados
- `protocol_builder.py`: `PHASE_TOOL_NAMES` usa nombres canónicos
- 10 `prompt.json` de nodos (design/verify/implement/sprint): command_hints y references
- `implement.node.json`: command_hints + constraints (`kg_schema` → `memory_schema`)

### D4 — MCP adapter + handlers

- `mcp_adapter.py`: texto de protocolo (inspect_codebase, task_context, etc.)
- `handlers.py`: `_PROTOCOL_REST_FALLBACK` + help task output

### D5 — Plugin walker-session.js

- Mapeo completo: `kg_multi` → `search_memories`, `kg_search` → `recall_knowledge`, etc.
- Guard `WALKER_GUARD_ENABLED=0` para debug

### D6 — Tests + BDD features

- Tests que llamaban `_mcp_call("kg_search", ...)` → `_mcp_call("recall_knowledge", ...)`
- `test_kg_search_index_bdd.py`, `test_kg_search_integration_bdd.py`: renombrados
- BDD features: `kg_search_integration.feature` → `recall_knowledge_integration.feature`
- `test_composition_root_d1.py`: `kg_extract` → `extract_knowledge`

### D7 — Task registry + handlers

- `handlers.py:849`: `"kg_extract"` → `"extract_knowledge"` en fallback
- `ckg_service.py`: logger messages y comments actualizados

---

## 3. Alternativas evaluadas

| Alternativa | Pros | Contras | Decisión |
|---|---|---|---|
| **A. Mantener dual (kg_search + recall_knowledge)** | Backward compatible | Confusión permanente; dos caminos de dispatch | Rechazada |
| **B. Eliminar kg_* completamente (elegida)** | Single source of truth; agente usa un solo nombre | Breaking change en todos los callers; requiere rename masivo | Aceptada |
| **C. Solo renombrar en MCP, dejar interno** | Menos cambios | Dissonancia entre interfaz pública y lógica interna | Rechazada |

---

## 4. Failure Scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | Agente usa `kg_search` pero MCP retorna "Unknown tool" | Tool catalog no registra `kg_search` (solo `recall_knowledge`) | Rename completo en protocol templates + walker-session.js | Tests E2E que validan `_mcp_call("recall_knowledge", ...)` returns ok |
| 2 | Task dispatch falla: `kg_extract` no encontrado en SystemTaskRegistry | Rename incompleto: handlers.py listaba `kg_extract` pero registry registraba `extract_knowledge` | Fix en `handlers.py:849` + `health_report_analyzer.py` | `test_composition_root_d1.py` valida `extract_knowledge` in `list_tasks()` |
| 3 | Protocol template confunde agente: recomienda tool inexistente | `rest.md` decía "tareas PUBLICAS (memorias kg_search/kg_schema/cpg/ckg)" | Renombrado a "recall_knowledge/memory_schema" | Validación manual del protocol + agent session test |

---

## 5. Consecuencias

### 5.1 Positivas
- Tool catalog es la SSOT: nombre en catalog = nombre en código = nombre en protocol
- Agente recibe instrucciones coherentes (protocol + tool catalog alineados)
- Test suite validable: un solo nombre por tool

### 5.2 Negativas
- Breaking change: cualquier script externo que llame `_mcp_call("kg_search", ...)` falla
- `KgExtractInput` dataclass mantiene prefijo `kg_` (renaming pendiente)
- Specs históricos (`SPEC-005.json`) contienen referencias legacy (se mantienen como registro)

### 5.3 Déuda técnica pendiente
- **Renombrar `KgExtractInput`** → `ExtractKnowledgeInput`
- **Renombrar archivos de test**: `test_kg_search_index_bdd.py` → `test_recall_knowledge_index_bdd.py`
- **Renombrar `ckg_extract`** purpose value en `extract_knowledge.py`

---

## 6. Verification

| Check | Resultado |
|---|---|
| `grep -rn "kg_search" --include="*.py" --include="*.json" --include="*.js" --include="*.feature"` (excluyendo specs/docs) | ✅ 0 resultados |
| `_get_tool_catalog()` returns 31 tools (sin kg_*) | ✅ |
| `_mcp_call("recall_knowledge", {...})` returns ok | ✅ |
| `test_protocol_contracts_bdd.py` (14 tests) | ✅ PASA |
| `test_protocol_domain_bdd.py` (19 tests) | ✅ PASA |
| `test_composition_root_d1.py` (4 tests) | ✅ PASA |
| `test_module_registry_di_bdd.py` (5 tests) | ✅ PASA |
