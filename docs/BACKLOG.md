
## BUG-BACKLOG-001 — TokenIndex del CPG se reconstruye en cada proceso (FTS lento)
- **Síntoma**: primera búsqueda fulltext sobre `_index:cpg` tarda minutos en cada
  proceso nuevo (CLI/MCP) tras habilitar `search.fulltext` en `cpg_index_config`
  (commit 8e64347). El rebuild G3 corre en memoria pero el searcher es
  read-only y nunca persiste el índice reconstruido.
- **Causa**: `Index._load()` reconstruye TokenIndex inline cuando cambia
  `schema_hash`, pero nadie llama a `save()` después (CQRS: lectura no escribe).
- **Impacto**: kg_search/recall_knowledge/code_search con texto = UX degradada
  para agentes; timeouts de cliente posibles.
- **Fix propuesto** (opciones):
  1. Persistir TokenIndex una vez al migrar (script one-shot que cargue y
     guarde con ti_data incluido) — ya probado manualmente, falta hacerlo
     idempotente y parte del pipeline de build.
  2. O bien: que `build_service.save()` incluya token_index siempre (probable
     ya lo hace) + ejecutar un save post-build en el flujo de build_code_index.
  3. O reducir campos fulltext a ["name"] para abaratar rebuild.
- **Estado**: workaround manual aplicado al store de react-native-frontend
  (verificar consistencia); pendiente fix estructural.
- **Descubierto**: 2026-08-24 sesión multi-proyecto MCP (ver ADR-WALKER-053).
