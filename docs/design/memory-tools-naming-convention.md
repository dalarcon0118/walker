# Convención de naming de tools públicas de memorias

> **Status:** Acordado (sesión 2026-08-13 — revisión de `walker_ckg_analyze`/`kg_search`).
> **Complementa:** ADR-WALKER-023 (memorias = casos de uso por fase), ADR-WALKER-043 (SSOT del protocolo del agente), ADR-WALKER-016 (bounded context protocol).
> **Ámbito:** names de las tools públicas MCP/CLI que leen/escriben las memorias (CPG, CKG, DeltaGraph, TraceGraph).

---

## 1. Problema

Las tools públicas de las memorias tenían nombres **técnicos de infraestructura**, no semánticos:

- `save_to_ckg` — nombre técnico (prefijo de índice `_to_ckg`), no dice la operación de negocio.
- `kg_search` / `kg_multi` — prefijo técnico `kg_`; `kg_multi` es la "Elasticsearch API" genérica que el ADR-023 §3 descarta como caso de negocio ("es kg_multi con otro nombre, no un caso de negocio").
- `ckg_analyze` — dispatcher stringly-typed (`action=save/search/active/...`) que duplica a los casos de uso; su `_project_id` se colaba como metadata de nodo.

**Regla violada:** las tasks públicas de las memorias deben llamarse **a través de sus use cases** (ADR-023) y los nombres deben tener **analogía con lo que hacen según la operación y el momento del proyecto** (fase del workflow).

## 2. La convención

1. **El nombre de la tool = la operación de negocio**, no la infraestructura (`ckg`/`kg`/`cpg` internos quedan escondidos detrás del caso de uso).
2. **Analogía por operación y momento**: cada capa de memoria tiene una responsabilidad según el estado del proyecto:
   - **documentar** → escribir conocimiento/decisiones del proyecto (CKG).
   - **recordar/consultar** → recuperar conocimiento/contexto (búsqueda puntual y por fase).
3. **Sin dispatchers stringly-typed**: una tool por caso de uso (nada de `action=...`).
4. **Sin prefijos técnicos de índice** en el nombre expuesto (no `kg_`, no `_to_ckg`).
5. Las tools de fase del workflow (`task_context`, `failure_diagnosis`, `qa_evidence`, `sprint_evidence`) mantienen el nombre del **caso de uso** del ADR-023 §3.

## 3. Mapa actual → objetivo

| Tool pública actual | Nombre objetivo (semántico) | Caso de uso / momento |
|---|---|---|
| `save_to_ckg` | `documenting_knowledge` | escribir decisiones/convenciones del proyecto (SaveToCkgUseCase) |
| `kg_search` | `recalling_knowledge` | consulta puntual de memoria (GatherCkgContextUseCase) |
| `kg_multi` | **eliminar** | es la "Elasticsearch API" genérica — no es caso de negocio (ADR-023 §3/§9) |
| `ckg_analyze` | **eliminar** | dispatcher que duplica a los casos de uso |
| `kg_task_context` | `task_context` | Fase IMPLEMENT (task_file_intelligence) |
| `kg_diagnose` | `failure_diagnosis` | Fase REFINE (failure_diagnosis) |
| `kg_qa_evidence` | `qa_evidence` | Fase VERIFY (qa_coverage) |
| `kg_sprint_evidence` | `sprint_evidence` | Fase SPRINT.PLANNING (sprint_evidence) |

## 4. Reglas de implementación

- **Alias por fases**: registrar el nombre nuevo semántico + mantener el viejo como alias deprecado (funcional) para no romper clients/tests de golpe.
- Actualizar en orden: registro MCP (`mcp.py`/módulos), tasks (`tasks/*.py` register), plugin `.opencode/plugins/walker-session.js`, prompts (`protocol_builder.py`, templates), tests.
- Una tool por caso de uso: nunca reintroducir `action=`.
- `_project_id` sigue siendo el **selector de proyecto** del protocolo MCP/CLI: NO debe propagarse a `metadata.task_id` de ningún nodo.
- Los nombres NO se tocan en el contrato REST routes (`{project_id}/...`) sin migrar antes el plugin (el agente llama por nombre de tool, no por ruta).

## 5. Fallos que ya se corrigieron (contexto)

- `cognitive_ckg_repo.save_nodes` perdía nodos silenciosamente por colisión de IDs en batch (`next_id` consultaba el índice persistido sin offset local). Fix: `batch_counters` + `next_id(prefix, offset)`.
- `nodes_created` reportaba los items iterados, no los que sobrevivían al dict. Fix: `len(new_docs)`.
- `ckg_analyze` fallback usaba `_project_id` como `task_id`. Fix: `data.get("task_id", "")`.

## 6. Estado del arte actual (qué sigue técnico)

| Capa | Puerto | Repo infra | Pregunta que responde | Naming expuesto hoy |
|---|---|---|---|---|
| CPG (código) | `CpgRepository` | `cognitive_cpg_repo.py` | ¿Cómo está construido? | `cpg_analyze` → fase |
| CKG (conocimiento) | `CkgRepository` | `cognitive_ckg_repo.py` | ¿Qué convenciones/decisiones aplican? | `save_to_ckg` → `documenting_knowledge`; `kg_search` → `recalling_knowledge` |
| Delta (sprint) | `DeltaRepository` | `cognitive_delta_repo.py` | ¿Qué tareas/specs lo tocan? | phases (`sprint_evidence`...) |
| Trace (actividad) | `TraceRepository` | `repository.py` | ¿Qué se hizo y cuándo? | `trace_push` |