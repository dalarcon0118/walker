# task_execution/tasks — guía para el agente

> Lee esto ANTES de tocar las tasks públicas que leen/escriben memorias.
> Convención global: `docs/design/memory-tools-naming-convention.md`.
> Fuente arquitectónica: ADR-WALKER-023 (memorias = casos de uso por fase),
> ADR-WALKER-043 (SSOT del protocolo del agente).

## Qué son

Tasks públicas = endpoints del agente (MCP tool `walker_<name>` + CLI
`walker <project>/<name>`). Son **adapters delgados**: validan el payload,
construyen el caso de uso / orquestador y serializan — NO tocan CognitiveGraph
directo (ADR-023 regla: la infra solo se instancia en `infrastructure/`).

## Convención de naming (operación + momento)

| Estado del proyecto | Operación | Task actual | Objetivo (semántico) |
|---|---|---|---|
| Momento de aprendizaje | **documentar** | `save_to_ckg` | `documenting_knowledge` |
| Consulta puntual | **recordar** | `kg_search` | `recalling_knowledge` |
| — | — | `kg_multi` | **eliminar** (API genérica, no caso de negocio) |
| — | — | `ckg_analyze` | **eliminar** (dispatcher `action=`) |
| Fase SPRINT.PLANNING | evidencia de sprint | `kg_sprint_evidence` | `sprint_evidence` |
| Fase IMPLEMENT | contexto de archivo | `kg_task_context` | `task_context` |
| Fase REPORT/REFINE | diagnóstico de fallo | `kg_diagnose` | `failure_diagnosis` |
| Fase VERIFY | cobertura QA | `kg_qa_evidence` | `qa_evidence` |

## Reglas

1. **Una task por caso de uso** — prohibido dispatchers `action=...` (el patrón
   `ckg_analyze` fue un error de diseño y está en vías de eliminación).
2. **Result pattern**: cores `@safe` con retorno `Result[T, E]` (returns);
   wrappers degradan con `is_successful` + log explícito (convention-001).
3. **`_project_id` es selector de proyecto** — nunca se escribe como metadata
   de nodo (`task_id` tiene campo dedicado).
4. **Contrato en el registro**: toda task de fase declara `input_schema` +
   `output_schema` derivado del DTO (`contracts.to_schema()`, ADR-043 F1).
5. Método de entrega: **alias por fases** — registrar el nombre semántico nuevo
   + mantener el viejo deprecado (funcional), luego migrar plugin/prompts/tests.

## Fallos conocidos (ya corregidos — no reintroducir)

- `kg_search` flags_builder descartaba `index` (fix: `_kg_search_flags` preserva
  el payload completo).
- `ckg_analyze` fallback usaba `_project_id` como `task_id` (fix: `data.get("task_id")`).
- Supersedes en batch: `save_nodes` escaneaba el índice 2× por nodo → una sola
  lectura `existing` por llamada.

## Archivos

| Archivo | Contiene |
|---|---|
| `kg.py` | `kg_search` (→ `recalling_knowledge`), `save_to_ckg` (→ `documenting_knowledge`), `kg_multi` (eliminar) |
| `ckg_analyze.py` | dispatcher `ckg_analyze` (eliminar; sus actions = casos de uso) |
| `phase_intelligence.py` | `kg_task_context`, `kg_diagnose`, `kg_qa_evidence`, `kg_sprint_evidence` (quitar sufijo `kg_`) |
| `knowledge.py` | tasks internas de knowledge (workflow) |
| `cpg_analyze.py` | análisis de código |
| `capture_task.py`, `kg_extract.py` | extracción/registro de conocimiento y contexto |