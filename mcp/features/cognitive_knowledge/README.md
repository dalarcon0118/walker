# Módulo cognitive_knowledge — guía para el agente

> Lee esto ANTES de tocar tools públicas del CKG. Convención global:
> `docs/design/memory-tools-naming-convention.md`.

## Responsabilidad de la capa (según estado del proyecto)

| Momento del proyecto | Operación | Caso de uso | Tool pública |
|---|---|---|---|
| El proyecto produce una decisión/aprendizaje | **documentar** | `SaveToCkgUseCase` | `save_to_ckg` → objetivo `documenting_knowledge` |
| El agente necesita contexto previo | **recordar** | `GatherCkgContextUseCase` | `kg_search` → objetivo `recalling_knowledge` |

## Reglas de este módulo

1. **Naming semántico**: la tool se llama por la OPERACIÓN (`documenting_knowledge`, `recalling_knowledge`), no por la infraestructura (`_to_ckg`, `kg_`).
2. **Una tool por caso de uso**: prohibido reintroducir dispatchers stringly-typed (`ckg_analyze action=...`). Cada acción es un caso de uso propio.
3. **`_project_id` es selector de proyecto** (protocolo MCP/CLI) — NUNCA se propaga como `metadata.task_id` de un nodo. Si se necesita task_id, campo dedicado `task_id`.
4. **Result pattern**: las funciones públicas devuelven `Result[T, E]` de dry-python/returns (convention-001: sin `except: pass` silenciosos — loggear siempre).
5. **Acceso a la entidad por API pública**: `KnowledgeFact.to_dict()` — no atributos privados (`_data`, `_cg_instance`).

## Fallos conocidos (ya corregidos — no reintroducir)

- **Colisión de IDs en batch** (`cognitive_ckg_repo.save_nodes`): `next_id()` consultaba el índice persistido sin incluir el batch actual → todos los nodos del mismo tipo colisionaban y el dict sobrescribía silenciosamente. Uso obligatorio de `batch_counters` + `next_id(prefix, offset)`.
- **`nodes_created` mentiroso**: debe reportar `len(new_docs)` (lo que sobrevivió al dict), no los items iterados.
- **`_read_ckg_file`** y `GatherCkgContextUseCase`: nunca degradar a `None`/`[]` sin loggear la causa.

## Archivos

| Archivo | Rol |
|---|---|
| `application/use_cases.py` | `SaveToCkgUseCase`, `GatherCkgContextUseCase`, `CheckCkgHealthUseCase` |
| `application/ckg.py` | `CodeKnowledgeGraph` (fachada sobre el repo) |
| `infrastructure/cognitive_ckg_repo.py` | Repo sobre CognitiveGraph (índice `ckg`) |
| `infrastructure/adapters/walker_ckg_adapter.py` | Puente hacia task_execution (ICkgServicePort) |
| `mcp.py` | Registro de tools MCP del módulo (naming semántico) |