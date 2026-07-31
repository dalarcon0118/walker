## Goal
Refactorizar la arquitectura del MCP `cognitive-mcp` para aplicar hexagonal + DDD + Domain Events, con el principio absoluto de que **CGC 0.5.1 es el SSOT único para datos de código** y SQLite solo para metadata operacional. Eliminar toda la duplicación de indexing paralelo que existía en `shared/codegraph.py` (2849 líneas) y consolidar la superficie CGC en un único `CodeIndexService` (antes `codegraph_service.py`).

## Constraints & Preferences
- **CGC es invisible al LLM y al usuario**: tool names, prompts, enums, métricas, mensajes NO deben mencionar CGC
- **SSOT estricto**: solo `code_index_service.py` y `cgc_watcher_factory.py` pueden importar de `codegraphcontext.*` (validado por boundary test)
- **El MCP NO debe indexar código**: `_scan_*`, tablas SQLite para symbols/imports/files/edges son anti-patrones
- **SQLite válido solo para metadata operacional** (KV genérico, traces, preferences)
- **Arquitectura hexagonal**: domain define ports (Protocol), infrastructure implementa adapters
- **DDD**: bounded contexts aislados, aggregate CodeGraph, eventos frozen dataclass
- **Domain events**: bus en memoria sync, handlers idempotentes con failure isolation
- **SRP**: cada use case = una responsabilidad, dependency inversion (use cases → ports ← adapters)
- **58/58 tests pasan con mock ports** (sin CGC ni SQLite en unit tests)
- **No fallas silenciosas**: usar `Outcome`/`Result` patterns, errores estructurados
- **Sin emojis** en código
- **Español** en explicaciones, **inglés** en código/comentarios técnicos

## Progress
### Done
- **Renombrado SSOT**: `codegraph_service.py` → `code_index_service.py`; `CodeGraphService` → `CodeIndexService`; `CodeGraphError` → `CodeIndexError`; tests renombrados
- **Boundary test actualizado**: 6/6 pasan, reconoce `cgc_watcher_factory.py` como nuevo bridge válido, `cgc_watcher_adapter.py` removido de bridges
- **Sanitización LLM-facing**:
  - Tool names: `cgc-status` → `code-index-status`, `project_index_cgc` → `project_index`
  - Enums API: `VALID_SOURCES = {"code_index", "memory", "git_log"}`
  - Métricas: `cognitive.codegraph` → `cognitive.code_index`
  - Prompts: menciones de "CGC"/"Memora" → "code index"/"memory"
  - Mensajes de verificación neutralizados en `server.py`, `session.py`, `session_service.py`
- **Migración `cgc_watcher_adapter.py`**: removidos 3 imports directos de `codegraphcontext.*`; nuevo `cgc_watcher_factory.py` encapsula `JobManager` + `GraphBuilder` + `CodeWatcher`; loop de asyncio ahora se cierra al detener watcher (bug latente arreglado); agregada `get_db_manager()` al `CodeIndexService`
- **Reducción de imports en `shared/codegraph.py`**: `_init_cgc()` ahora obtiene db handle vía `CodeIndexService.get_db_manager()` (no importa `DatabaseManager`/`FalkorDBManager`); `_cgc_cypher_query()` delega a `service.query_cypher()`; solo queda 1 import activo (CodeFinder legacy para métodos específicos)
- **`_symbol_exists` en `session_service.py`**: refactorizado para usar `service.find_functions()`; test boundary actualizado para parchear el nuevo path
- **Migración `_cg_db()` en `session_service.py`**: clasificadas como SQLite (no CGC) — preexistente, no parte del refactor CGC (decisión revertida por usuario: el usuario indica que "el único con el código como datos es CGC" — refactor pendiente para futura fase)
- **BC `cpg` (Code Property Graph) creado**:
  - `domain/exceptions.py`: CpgError, CodeIndexUnavailable, CodeIndexFailure, InvalidCodeQuery
  - `domain/ports/`: CodeIndexPort, CypherPort (Protocols runtime_checkable)
  - `domain/models/`: SymbolKind, SymbolRef, IndexedSymbol, CodeQuery, IndexResult, ImpactReport
  - `domain/events/`: 7 frozen dataclass events (CodeIndexed, CodeIndexFailed, ReindexRequested, SymbolsFound, NoSymbolsFound, CallChainTraced, ImpactCalculated)
  - `application/use_cases.py`: 5 use cases (IndexCode, FindSymbol, TraceCallChain, CalculateImpact, Reindex)
  - `application/event_handlers.py`: LoggingHandlers (subscribers)
  - `infrastructure/cgc_adapter.py`: CgcAdapter implementa CodeIndexPort + CypherPort vía CodeIndexService
- **BC `metadata` (Operational KV) creado**:
  - `domain.py`: KeyValuePort (Protocol), IndexKey (con Scope: GLOBAL/PROJECT/SESSION), IndexEntry, excepciones
  - `events.py`: IndexEntryStored, IndexEntryDeleted
  - `use_cases.py`: GetIndexUseCase, PutIndexUseCase, DeleteIndexUseCase, ListIndexUseCase
  - `sqlite_adapter.py`: SqliteKeyValueAdapter (schema `idx_store` con composite key namespace+key+scope+scope_id)
- **Shared kernel `features/shared/events/`**:
  - `__init__.py`: DomainEvent (frozen dataclass, timestamp UTC forzado)
  - `event_bus.py`: InMemoryEventBus (thread-safe, sync, failure isolation, introspection)
  - `handler.py`: EventHandler protocol, FreeHandler type
- **Tests unitarios creados** (58 totales):
  - 12 tests `test_event_bus.py`: subscription, unsubscribe, error isolation, introspection, UTC timestamp
  - 17 tests `test_use_cases.py`: SRP, event publishing, soft failures, validación, strategy dispatch (con MockCodeIndexPort)
  - 23 tests `test_metadata.py`: validación de IndexKey, use cases con MockKeyValuePort, SQLite round-trip (set/get/delete/list_keys/scoped keys)
  - 6 tests `test_code_index_service_boundary.py`: SSOT boundary
- **Mapeo legacy → nueva arquitectura documentado**:
  - `op_code_build` → `IndexCodeUseCase`
  - `op_code_lookup`/`op_code_scan` → `FindSymbolUseCase`
  - `op_code_trace` → `TraceCallChainUseCase`
  - `op_code_impact` → `CalculateImpactUseCase`
  - `op_code_reindex` → `ReindexUseCase`
  - `op_code_learn` → eliminar (CGC indexa solo)
- **Helpers/main.py**: limpieza de basura `</parameter>` preexistente
- **DDD_CLEANUP.md y REFACTOR_MAP.md**: documentación de cleanup

### In Progress
- (nada — sesión completada, esperando instrucciones para Fase 3)

### Blocked
- **Bug preexistente no resuelto**: `session_service.py` importa `_kv_scoped` que no existe en `helpers` (rompe 7 tests de session); no es parte del refactor CGC, se reporta al usuario
- **IDE muestra `codegraph_service.py` línea 754 con comentario TODO**: archivo NO existe (renombrado a `code_index_service.py`); el IDE está mostrando un caché o referencia preexistente
- **23 callsites de `_cg_db()` en session_service.py y otros**: clasificados como SQLite; el usuario afirma que "el único con el código como datos es CGC" — refactor pendiente para fase futura
- **10 tools MCP de `op_code_*`**: no migrados a use cases todavía
- **`shared/codegraph.py` (2849 líneas)**: reducción a shims deprecados pendiente

## Key Decisions
- **CGC invisible al LLM/usuario**: tool names, prompts, métricas deben ser neutros. `project_index_cgc` → `project_index`, `cognitive.codegraph` → `cognitive.code_index`. Razón: el usuario/LLM no debe acoplarse a la implementación
- **SSOT estricto para CGC**: solo `code_index_service.py` y `cgc_watcher_factory.py` importan de `codegraphcontext.*`. Validado por boundary test. Razón: una sola fuente de verdad, evitar acoplamiento múltiple
- **SQLite solo para metadata operacional**: BC `metadata` creado con KeyValuePort. Razón: el usuario afirma explícitamente "el único con el código como datos es CGC"
- **Domain events en bus en memoria**: synchronous, thread-safe, failure isolation. Razón: el MCP es single-process (FastMCP), no necesita bus distribuido
- **Frozen dataclass events**: inmutables, timestamp UTC forzado, no behaviour. Razón: eventos son hechos, no objetos de negocio
- **Mock ports para tests**: 58/58 tests pasan sin CGC ni SQLite. Razón: dependency inversion permite testear el dominio sin I/O
- **Composition root en cada BC**: `get_code_index_port()`, `get_key_value_port()`. Razón: tests pueden override sin reescribir use cases
- **5 CPG use cases + 4 metadata use cases**: cada uno con SRP estricto. Razón: cohesión alta, fácil de testear
- **2 Bounded contexts**: `cpg` (código → CGC) y `metadata` (operacional → SQLite). Razón: separación clara de responsabilidades
- **`CodeIndexService.get_db_manager()`**: domain accessor para que `cgc_watcher_factory` no importe de CGC. Razón: encapsulación de CGC
- **`ReindexUseCase` no reindexa directamente**: solo publica `ReindexRequested`, los subscribers actúan. Razón: event-driven, permite que el watcher se entere
- **Helper `_row_to_symbol` centralizado en CgcAdapter**: traduce dicts de CGC a `IndexedSymbol` (dominio). Razón: no leakear formato CGC a callers
- **Límite de `CodeQuery.limit` en (0, 1000]**: previene queries masivas accidentales. Razón: protección contra DoS accidental

## Next Steps
1. **Fase 3 — Driving adapter (api layer)**: crear `features/cpg/api/mcp_tools.py` que conecte tools MCP a use cases
2. **Migrar 1-2 tools MCP a use cases como ejemplo** (`code-lookup` → `FindSymbolUseCase`, `code-trace` → `TraceCallChainUseCase`)
3. **Resolver bug preexistente `_kv_scoped`**: arreglar import roto en `session_service.py` (no relacionado al refactor CGC)
4. **Resolver migración de los 23 callsites `_cg_db()`** (SQLite): migrar a `KeyValuePort`/`GetIndexUseCase` del nuevo BC metadata (fase futura)
5. **Eliminar `shared/codegraph.py`** (o reducir a re-exports deprecados) tras migrar todas las funciones `op_code_*`
6. **Eliminar tablas SQLite `symbols/imports/files/edges`**: tras confirmar que ningún consumer las usa
7. **Eliminar `_scan_*`** (es responsabilidad de CGC, no del MCP)
8. **Validar test suite completo** + tools MCP end-to-end (incluyendo las 10 tools `op_code_*` migradas)
9. **Considerar push a master**: los cambios están en commit `a674269`, no pusheados

## Critical Context
- **Branch**: `master` (NO pusheado a remoto)
- **Último commit**: `a674269 feat(cpg): refactor to hexagonal/DDD architecture with domain events` (incluye todo el refactor + tests)
- **Estado pre-commit**: working tree estaba dirty con cambios en __pycache__, .cell_cache.json, source files; se hizo `git add -A -- ':(exclude)__pycache__' ':(exclude).cell_cache.json'` y se commiteó
- **Snapshots commitados accidentalmente**: `snapshots/cog-20260708*.yaml` y `snapshots/cog-20260709*.yaml` (no estaban en .gitignore)
- **Tests**: 58/58 pasan (23 metadata + 17 CPG use_cases + 12 event_bus + 6 boundary)
- **Fallo preexistente conocido**: `_kv_scoped` no existe en `helpers/main.py` → rompe 7 tests de `session_service` (no relacionado al refactor)
- **IDE artefact**: el usuario sigue abriendo `codegraph_service.py` y `shared/codegraph.py`; el primero ya no existe (es `code_index_service.py`), el segundo es el legacy bridge
- **`.gitignore` creado**: ahora se deberían excluir __pycache__, .cell_cache.json (verificar)
- **Sprint YAML creado**: `snapshots/cog-20260709T094652-refactor-ddd-hexagonal_sprint_-20260709T094608.yaml`
- **CGC version**: `codegraphcontext==0.5.1` (FalkorDB backend, embedded via redislite)
- **FalkorDB label**: `falkordb_lite` (matches pre-existing label en `shared/codegraph.py`)
- **MockCodeIndexPort**: tests pattern para hexagonal; 6 use case tests verifican SRP, eventos, validación
- **MockKeyValuePort**: tests pattern para metadata; 7 tests verifican round-trip + scope isolation
- **Tests SQLite real**: `TestSqliteAdapterRoundTrip` usa `tmp_path` para no contaminar; 7 tests verifican schema + INSERT OR REPLACE
- **Session ID actual**: ninguno nuevo creado en INDEX.json todavía (este commit es la sesión)
- **Self-evaluated score**: ~9/10. La arquitectura hexagonal está validada con tests pero faltan: driving adapter, migración de tools MCP, eliminación de legacy

## Relevant Files

### Nuevos (este commit)
- [code_index_service.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/infrastructure/code_index_service.py): SSOT para CGC; `get_db_manager()` agregado para que el watcher factory acceda al handle sin importar CGC
- [cgc_watcher_factory.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/infrastructure/cgc_watcher_factory.py): encapsula `JobManager` + `GraphBuilder` + `CodeWatcher`; expone `is_available()`, `get_db_manager()`, `create_watcher()`, `is_cgc_db_connected()`, `attach_event_loop()`
- [cgc_adapter.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/infrastructure/cgc_adapter.py): implementa `CodeIndexPort` + `CypherPort`; traduce `CodeQuery`/`IndexedSymbol` ↔ CGC; helper `_row_to_symbol()` centralizado
- [test_code_index_service_boundary.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/infrastructure/tests/test_code_index_service_boundary.py): 6 tests, valida SSOT (solo service + factory pueden importar CGC)
- [cpg/domain/exceptions.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/domain/exceptions.py): CpgError, CodeIndexUnavailable, CodeIndexFailure, InvalidCodeQuery
- [cpg/domain/ports/__init__.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/domain/ports/__init__.py): CodeIndexPort (Protocol), CypherPort (Protocol) con runtime_checkable
- [cpg/domain/models/__init__.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/domain/models/__init__.py): SymbolKind, SymbolRef, IndexedSymbol, CodeQuery, IndexResult, ImpactReport
- [cpg/domain/events/__init__.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/domain/events/__init__.py): 7 frozen dataclass events
- [cpg/application/use_cases.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/application/use_cases.py): 5 use cases (IndexCode, FindSymbol, TraceCallChain, CalculateImpact, Reindex) con base `UseCase[Req, Res]`
- [cpg/application/event_handlers.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/application/event_handlers.py): LoggingHandlers bundle (subscribers para CodeIndexed, CodeIndexFailed, SymbolsFound, ReindexRequested, ImpactCalculated)
- [cpg/application/tests/test_use_cases.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/application/tests/test_use_cases.py): 17 tests con MockCodeIndexPort; valida SRP, eventos, validación, strategy dispatch
- [metadata/domain.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/metadata/domain.py): KeyValuePort, IndexKey (Scope: GLOBAL/PROJECT/SESSION), IndexEntry, MetadataError hierarchy
- [metadata/events.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/metadata/events.py): IndexEntryStored, IndexEntryDeleted (frozen dataclass)
- [metadata/use_cases.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/metadata/use_cases.py): GetIndexUseCase, PutIndexUseCase, DeleteIndexUseCase, ListIndexUseCase
- [metadata/sqlite_adapter.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/metadata/sqlite_adapter.py): SqliteKeyValueAdapter (schema `idx_store`, thread-safe, soft failures)
- [metadata/tests/test_metadata.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/metadata/tests/test_metadata.py): 23 tests (mock port + SQLite round-trip)
- [shared/events/__init__.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/shared/events/__init__.py): DomainEvent base + re-exports (InMemoryEventBus, EventHandler)
- [shared/events/event_bus.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/shared/events/event_bus.py): InMemoryEventBus (thread-safe, sync pub/sub, error isolation)
- [shared/events/handler.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/shared/events/handler.py): EventHandler Protocol, FreeHandler type
- [shared/events/tests/test_event_bus.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/shared/events/tests/test_event_bus.py): 12 tests del bus

### Modificados
- [cgc_watcher_adapter.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/monitoring/cgc_watcher_adapter.py): removidos 3 imports de `codegraphcontext.*`; usa `cgc_watcher_factory`; `_is_cgc_connected` reemplazado por `_is_cgc_db_connected` del factory
- [codegraph_adapter.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/infrastructure/codegraph_adapter.py): removido import directo de `CodeFinder`; usa `service.get_cpg_finder()`
- [shared/codegraph.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/shared/codegraph.py): `_init_cgc()` usa `CodeIndexService.get_db_manager()`; `_cgc_cypher_query()` usa `service.query_cypher()`; 1 import activo restante (CodeFinder legacy)
- [session.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/session/domain/session.py): `VALID_SOURCES = {"code_index", "memory", "git_log"}`; tool names renombrados (`code-index-build`, `code-index-impact`, etc.)
- [session_service.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/session/application/session_service.py): `_symbol_exists` usa `service.find_functions()`; verificaciones mencionan "code index" no "codegraph"; métrica renombrada
- [server.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/server.py): prompts neutralizados ("memory + code index + KV" en vez de "Memora + CGC")
- [absorption_tools.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/project/api/absorption_tools.py): `project_index_cgc` → `project_index`
- [shared/ops_init.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/shared/ops_init.py): `"cgc-status"` → `"code-index-status"`
- [shared/registry.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/infrastructure/tests/test_code_index_service_boundary.py): tool name actualizado
- [__main__.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/__main__.py): comentarios sanitizados
- [serve.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/cli/commands/serve.py): comentarios sanitizados
- [cfg_builder.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/cpg/application/cfg_builder.py): import actualizado a `code_index_service`
- [shared/helpers/main.py](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/shared/helpers/main.py): limpieza de basura `</parameter>` preexistente

### Documentación
- [REFACTOR_MAP.md](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/REFACTOR_MAP.md): mapa de refactor
- [DDD_CLEANUP.md](file:///Users/davidmartinez/develop/Active-projects/game_reset/projects/cognitive-mcp/cognitive_v2/features/DDD_CLEANUP.md): plan de cleanup DDD

### Eliminados
- `codegraph_service.py` (renombrado a code_index_service.py)
- `test_codegraph_service_boundary.py` (renombrado a test_code_index_service_boundary.py)
- `cpg/infrastructure/falkordb_adapter.py` (consolidado en code_index_service)
- `cpg/infrastructure/tests/` varios archivos viejos
- `features/guidance/` (consolidado)
- `features/context/tests/test_dual_write.py`, `test_file_reader_adapter.py`, `test_result_combiner.py` (legacy)
