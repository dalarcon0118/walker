# ADR-WALKER-048: Recuperación determinista de memoria (prompt context) vs tools instruidas — el contexto de lo que el agente hace se inyecta, no se pide

> **Status:** Propuesta (pendiente de aprobación)
> **Fecha:** 2026-08-13
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-018 (context gates — enforcement de memoria), ADR-WALKER-023 (use cases esconden infraestructura), ADR-WALKER-043/044 (SSOT del protocolo), ADR-WALKER-047 (confusión de registries — deuda de names hardcodeados), SPEC-WALKER-019 (agent context gates)
> **Evoluciona:** ADR-WALKER-018 — los gates del hook ejecutaban la consulta de memoria; este ADR mueve la recuperación DENTRO del engine (ContextComposer), determinista y trazada, e integra los joins de `TaskIntelligenceService`.

---

## 1. Problema

Walker vive dos problemas distintos que la auditoría del ADR-047 dejó en evidencia:

**P1 — Deuda técnica: nombres de tools hardcodeados en system prompts y en el hook (técnica de prompting débil).**
El protocolo instruye al agente a llamar tools de memoria por nombre en múltiples copias de texto:
- `features/main/prompts/templates/protocol/mcp.md:13,21` — "kg_search with _index:cpg...", "Use save_to_ckg to REGISTER decisions".
- `features/main/prompts/templates/protocol/rest.md:9-15` — documenta `kg_search`, `kg_task_context`, `kg_diagnose`, `kg_qa_evidence`, `kg_sprint_evidence`.
- `features/main/prompts/protocol_builder.py:21-24` — mapea `kg_*` de fase a descripciones.
- Templates de nodos del workflow (design-analyze, design-gather, qa, po-accept, work, report-gate, user-gate, plan-approval, task-review, task-spec) — todos instruyen `walker <project>/kg_multi` / `kg_search`.
- `.opencode/plugins/walker-session.js:30-32` (ALLOWED_TOOLS con nombres legacy+canónicos) y líneas 98/121/138/152/248 (gates `kg_multi` por archivo).

La técnica de prompting "MUST consult ..." es **consejo, no contrato**: el modelo puede omitir, olvidar o malformar la llamada, y el enforcement recae en texto. Además, cada evolución de walker (rename de tools, Fase 2 del naming) rompe N copias de texto acopladas a nombres — la deuda observada en ADR-047 B-7.

**P2 — El problema de fondo: la búsqueda de memoria es RAG, y en RAG la recuperación es parte del pipeline, no una decisión del modelo.**
Las llamadas a la memoria son la búsqueda de contexto sobre **lo que está haciendo el agente**. Si la recuperación depende de que el LLM decida llamar una tool, el modelo puede actuar sin contexto → alucinación por omisión. La técnica correcta (search-then-generate): la recuperación ocurre **fuera del LLM**, antes/durante el ensamblado del prompt, **incondicionalmente**, y se inyecta como capas. El LLM consume el contexto; no decide recuperarlo.

En walker esto tiene dos caras:
1. **El canal determinista existe** (`ContextComposer` + providers + `QueryManager`): inyección incondicional por nodo (`context.sources` + `context.focus`), budget (`ContextWindowManager`), caché para refine loops, y trazado en TraceGraph (`kg_query`) de cada consulta de contexto.
2. **Pero ese canal inyecta capas RAW** (`CKGProvider.collect` → `query_manager.query_layer("ckg", filters)` → `format_ckg_results`, ver `features/cognitive_knowledge/context/providers.py:36-48`), mientras que la **inteligencia con joins** — la recuperación con propósito "voy a tocar `src/auth.py`, dame lo que necesito saber" — vive SOLO en `TaskIntelligenceService` (`task_file_intelligence`, `failure_diagnosis`, `qa_coverage`, `sprint_evidence`) y se expone únicamente como **tools** (`phase_intelligence.py`) que el agente debe recordar llamar.

**La brecha:** la parte valiosa del contexto (joins cpg × ckg × delta × trace, síntesis, risk, insights) depende del canal débil. El prompt context (determinista) aún no se integra con la tarea de intelligence context (los joins).

---

## 2. Decisión

**El contexto de lo que el agente hace se INYECTA de forma determinista vía el prompt context del engine, integrado con los joins de `TaskIntelligenceService`; los prompts y el hook dejan de nombrar tools de memoria y dejan de ejecutar la recuperación.**

Decisiones de diseño:

1. **Nuevo provider de contexto `task_intelligence`** en el `ContextProviderRegistry`: `collect()` construye `build_intelligence_service(chain_dir)` y ejecuta el JOIN según la fase declarada en el nodo (`task_file_intelligence`, `failure_diagnosis`, `qa_coverage`, `sprint_evidence`), alimentado con `file`/`task_id` **del ExecutionContext** (qué está haciendo el agente). `output_schema()` = `DTO.to_schema()` (SSOT del contrato, ADR-043) — sin schemas escritos a mano.
2. **Los nodos IMPLEMENT/REFINE/VERIFY/SPRINT declaran `task_intelligence` en `context.sources`** → inyección incondicional del DTO como capa `<< task_intelligence >>`.
3. **Los prompts referencian capas estables, nunca tool names**: «La capa `<< task_intelligence >>` contiene el contexto de tu tarea; consúmela». Los renames de tools (Fase 2 del naming) dejan de tocar prompts. Las tools de memoria quedan como **override ad-hoc** para consultas nuevas del agente, no como mecanismo primario; en línea con el modelo refinado del ADR-047 (§1), las tools son un concepto del agente inyectadas dinámicamente desde el TaskRegistry de main — el contexto de memoria se consume por capas, no por invocación.
4. **El hook deja de portar la recuperación** (evolución de ADR-018): los gates `kg_multi` por archivo se eliminan del plugin; la recuperación vive en el composer (determinista y trazada). El hook conserva su rol de enforcement/annotate del resto del protocolo y su whitelist deja de listar tools de memoria.
5. **Trazabilidad inalterada**: cada consulta de contexto del provider se registra como `kg_query` en el TraceGraph asociada al `task_id`/`session_id` del contexto en curso (mecanismo existente de `QueryManager._trace_query`).

---

## 3. Consecuencias

### 3.1 Positivas

- **El agente SIEMPRE recibe el contexto de lo que está haciendo**: incondicional (canal determinista), con joins (multi-source fusion), con propósito (síntesis por fase), y trazado. Elimina la alucinación por omisión de contexto (P2).
- **Mata la deuda de names (P1)**: los prompts y el hook referencian `<< capas >>` (conjunto estable) — los renames de tools ya no rompen copias de texto; habilita la Fase 2 real del naming (ADR-047 B-7) con riesgo casi nulo.
- **El plugin se simplifica**: sin gates `kg_multi` ni whitelist de memoria; deja de ser el carrier de recuperación y de names.
- **Cierra la brecha ADR-018 → engine**: el enforcement deja de ser "texto/gates del hook" y pasa a ser estructura del motor (la inyección no requiere que nadie la ejecute).
- **Contrato tipado**: `output_schema` derivado del DTO; el agente conoce la forma de la capa (SSOT, ADR-043).

### 3.2 Negativas / Trade-offs

- **Costo de tokens**: inyectar el DTO por fase suma contexto a cada request del nodo. Mitigación: `ContextWindowManager` + niveles (compact/summary vs full) que ya existen, y caché del composer para refine loops.
- **Latencia por nodo**: 4 consultas + joins por provider. Mitigación: caché de `_collect_cache` (refine loops no re-consultan) y los repos por DI ya resueltos.
- **Doble camino durante la transición**: las tools de fase seguirán existiendo (override) mientras los prompts migran a capas; riesgo de que un template viejo siga "pidiendo" una tool que ya se inyecta (duplicación de contexto) — mitigado en failure_scenarios.
- **El hook pierde una función**: la ejecución de gates de memoria por archivo (ADR-018) se traslada al engine; si el provider falla, el fallback debe ser explícito (log + degradación) para no enmascarar la pérdida de contexto.

---

## 4. Arquitectura objetivo

```
        nodo del workflow (IMPLEMENT/REFINE/VERIFY/SPRINT)
              │  context.sources: [... task_intelligence ...]   (declarativo, por fase)
              ▼
┌──────────────────────────────────────────────────────────────┐
│  ContextComposer (core/engine/context/composer.py)            │
│  _collect_pieces → provider.collect(config, ctx, qm)         │
│                                                            │
│  provider task_intelligence (NUEVO — features/...)            │
│    build_intelligence_service(chain_dir)                      │
│    TaskIntelligenceService.get_task_intelligence(             │
│        file=<ctx.file>, task_id=<ctx.task_id>)                │
│    → JOIN con propósito: cpg × ckg × delta × trace            │
│    → DTO de fase (TaskFileIntelligenceDTO / FailureDiagnosisDTO│
│       / QaCoverageDTO / SprintEvidenceDTO)                    │
│    → output_schema = DTO.to_schema() (SSOT)                   │
└──────────────────────┬───────────────────────────────────────┘
                       │  inyección INCONDICIONAL (capa << task_intelligence >>)
                       ▼
              prompt del LLM (sin names de tools de memoria)
                       ▲
              /tools de memoria = override ad-hoc (consultas nuevas),
              inyectadas dinámicamente desde el TaskRegistry de main (ADR-047 Fase 4)
              /hook = enforcement del resto del protocolo (sin gates kg_multi)
```

**Reglas de dependencia:**
1. La recuperación de memoria NO depende de la iniciativa del LLM — el composer la ejecuta por declaración del nodo.
2. Los prompts y el hook referencian **capas** (`<< task_intelligence >>`, `<< ckg >>`...), nunca tool names.
3. `TaskIntelligenceService` sigue siendo la capa que orquesta repos por DI (ADR-023): el provider no toca CognitiveGraph directo.
4. Cada consulta del provider queda trazada en TraceGraph (`kg_query` con `task_id`/`session_id` del contexto).

---

## 5. Mapa de archivos afectados (implementación futura)

| Archivo | Cambio |
|---|---|
| `features/cognitive_knowledge/context/providers.py` (o `features/main/task_execution/context/`) | Nuevo provider `task_intelligence` (o registry de providers propio de task_execution) |
| `core/engine/context/registry.py` + `build_context_registry()` | Registrar el provider; test de completitud (patrón AGENTS.md) |
| `core/engine/workflow/*.node.json` (nodos IMPLEMENT/REFINE/VERIFY/SPRINT) | Declarar `task_intelligence` en `context.sources` con `phase`/`file`/`task_id` |
| `features/main/prompts/protocol_builder.py`, `templates/protocol/mcp.md`, `rest.md` | Sustituir names de tools por referencias a capas |
| `features/main/prompts/templates/nodes/**` | Quitar `walker <project>/kg_multi`/`kg_search` de las instrucciones; el contexto ya viene inyectado |
| `.opencode/plugins/walker-session.js` | Eliminar gates `kg_multi` (98/121/138/152/248) y names de memoria de ALLOWED_TOOLS |
| `features/main/task_execution/tasks/phase_intelligence.py` | Las tools de fase quedan como override; con ADR-047 Fase 4 pasan a inyección dinámica desde el TaskRegistry de main (sin cambio de semántica del JOIN) |
| `core/engine/context/contracts.py` | `CONTEXT_SCHEMAS` += `task_intelligence` (fallback SSOT) |

---

## 6. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | El agente recibe contexto duplicado: el template de fase sigue instruyendo `task_context`/`kg_multi` MIENTRAS la capa `<< task_intelligence >>` ya se inyecta | Migración incompleta de prompts (se hace el provider pero no se limpian los templates) | Orden de implementación fijo: 1) provider, 2) declarar en nodos, 3) limpiar prompts, 4) hook; linter rule: prohibir strings `kg_multi`/`kg_search`/`save_to_ckg` en `features/main/prompts/` | CI grep: `grep -rn "kg_multi\|kg_search\|save_to_ckg\|kg_task_context" features/main/prompts/` → 0 resultados |
| 2 | El provider falla (repos no indexados / chain_dir sin proyecto) y el nodo sigue: el agente actúa SIN contexto de memoria (regresión P2) | `collect()` degrada silenciosamente como los providers raw actuales | El provider loggea y marca la capa como `ok=False`/excluded en `provenance` (mecanismo existente del composer); el nodo puede declarar la capa como `required` → el workflow no avanza sin contexto | Test: nodo IMPLEMENT con chain_dir vacío → el ComposedContext reporta `task_intelligence` excluded con error visible; log warning |
| 3 | Un rename de tools (Fase 2 naming) rompe el hook o un prompt que aún lista el nombre viejo | Migración incompleta (P1): un consumidor quedó hardcodeando el name viejo | La regla de capas estables aplica a prompts Y hook; el CI grep del escenario 1 se extiende a `.opencode/plugins/` | `grep -rn "kg_multi\|kg_search\|save_to_ckg\|ckg_analyze" .opencode/plugins/` → 0 resultados |
| 4 | El contexto inyectado excede el presupuesto y el composer recorta la capa de inteligencia (la valiosa) antes que las raw | Budget mal configurado por nodo; la capa de joins tiene más tokens que las capas raw | `ContextWindowManager` asigna prioridad alta al provider `task_intelligence` (como los providers actuales tienen `priority`, ver CKGProvider.priority=30); test de asignación confirma que la capa de inteligencia siempre entra | Test: nodo con budget mínimo → `<< task_intelligence >>` presente en `included`, alguna capa raw en `excluded` |
| 5 | La caché del composer sirve contexto viejo tras un side effect (ej. update_code_index) en el mismo nodo | `_collect_cache` keyed por `(provider, config, task_id, chain_dir)` no invalida al cambiar el índice | Reusar `invalidate_cache()` existente en los side effects que mutan memorias (update_code_index, build_project_graph, save_to_ckg) | Test: save_to_ckg → recomponer el nodo → `task_intelligence` refleja el nodo nuevo |
| 6 | La tool de fase (override) y el provider divergen en semántica: el agente obtiene respuestas distintas según el canal | Dos implementaciones del mismo JOIN para dos canales | El provider DELEGA en los mismos métodos de `TaskIntelligenceService` que las tools (una sola implementación del JOIN); los DTOs son el contrato único | Test de equivalencia: provider.collect(phase=IMPLEMENT) == task_context(ctx, payload) para el mismo file/task_id |

---

## 7. Checklist de segundo orden

1. **¿Qué módulos importan esto?**
   - `core/engine/context/composer.py` (los providers se disparan por declaración), `core/engine/context/registry.py` (registro), `core/engine/context/contracts.py` (schema fallback).
   - `features/main/task_execution/application/intelligence_service.py` (JOINs reutilizados por el provider), `features/main/task_execution/application/contracts.py` (DTOs → `to_schema()`).
   - `core/engine/workflow/*.node.json` (declaración de `sources`), `features/main/prompts/**` (capas en vez de names), `.opencode/plugins/walker-session.js` (sin gates de memoria).

2. **¿Qué side effects dispara?**
   - El provider ejecuta consultas de las 4 memorias por nodo (lectura pura; ya trazadas por `QueryManager`).
   - No registra tasks ni side effects nuevos; no muta estados.
   - La caché del composer se invalida con los side effects existentes de indexación (sin cambio de comportamiento).

3. **¿Qué eventos de dominio publica?**
   - Ninguno nuevo. El TraceGraph sigue recibiendo `kg_query` (ahora también para la capa de inteligencia, con el `task_id` del contexto).
   - Cambio indirecto: las consultas que hoy hace el hook vía `kg_multi` pasan a ser `kg_query` del composer → el trace queda más completo y asociado al nodo.

4. **¿Qué tests dependen de este comportamiento?**
   - Tests de composición de contexto (providers/composer) → uno nuevo por provider `task_intelligence` (equivalencia con las tools de fase, escenario 6).
   - Tests de prompts/protocolo (test_prompts_ssot_bdd, f4_help_rest, protocol_contracts) → los prompts cambian de names a capas; deben migrar.
   - Tests E2E de workflow (IMPLEMENT/REFINE) → ahora reciben la capa inyectada; verifican que el nodo avanza solo si el contexto está (escenario 2).
   - Tests del plugin (si existen) → sin gates de memoria.

5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?**
   - MCP adapter/CLI: sin cambios de dispatch; las tools de fase siguen disponibles como override.
   - CKG/DeltaGraph/CPG/Trace: sin cambios de interfaz; consumidos vía `TaskIntelligenceService` por DI (ADR-023).
   - Trace: mayor visibilidad de la recuperación de contexto (consultas del composer asociadas al nodo).

---

## 8. Implementación por fases

### Fase 1 — Provider `task_intelligence` + declaración en nodos (medio)

1. Implementar el provider (delega en `TaskIntelligenceService`; `output_schema` = `DTO.to_schema()`; `priority` alta).
2. Registrarlo en `build_context_registry()` con test de completitud (patrón AGENTS.md).
3. Declarar `task_intelligence` en `context.sources` de los nodos IMPLEMENT/REFINE/VERIFY/SPRINT (`phase`/`file`/`task_id` desde el ctx).
4. Test de equivalencia provider ↔ tool de fase (escenario 6) y de exclusión por budget (escenario 4).

### Fase 2 — Prompts a capas estables (medio)

1. `protocol_builder.py`, `mcp.md`, `rest.md`: sustituir names de tools de memoria por referencias a `<< capas >>`.
2. Templates de nodos: quitar `walker <project>/kg_multi`/`kg_search` de las instrucciones.
3. CI grep de 0 resultados de names legacy en `features/main/prompts/` (escenario 1).

### Fase 3 — Hook sin recuperación (grande)

1. Eliminar los gates `kg_multi` del plugin (98/121/138/152/248) y los names de memoria de ALLOWED_TOOLS.
2. El hook conserva enforcement del resto del protocolo (ADR-018) y anotación.
3. CI grep de 0 resultados en `.opencode/plugins/` (escenario 3).

### Fase 4 — Fase 2 real del naming (ADR-047 B-7)

1. Con prompts y hook libres de names, retirar los aliases legacy del TaskRegistry y los routes REST legacy.
2. Con suite verde y plugin funcionando con capas.

---

## 9. Referencias

- ADR-WALKER-018 — context gates: este ADR **evoluciona** su decisión (la recuperación pasa del hook al composer del engine; el principio "la memoria es el SSOT del contexto; el hook enforcement" se mantiene y se refuerza con inyección estructural).
- ADR-WALKER-023 — los use cases y `TaskIntelligenceService` son la capa que orquesta las memorias; el provider no toca infra directa.
- ADR-WALKER-043/044 — SSOT del protocolo y schemas (`DTO.to_schema()` como contrato de la capa).
- ADR-WALKER-047 — deuda de names hardcodeados (B-7) y la confusión de registries (B-1..B-5); modelo refinado (§1): las tools son concepto del agente, inyectadas dinámicamente desde el TaskRegistry de main. Este ADR deja de depender de names porque la recuperación pasa al composer; los names solo importan para el override ad-hoc.
- SPEC-WALKER-019 / ADR-018 §3 — gates del hook y `kg_agent_context` (consulta consolidada de sesión; se conserva como tool/contenido).
- Técnica RAG: *search-then-generate* — la recuperación es un paso del pipeline, no una decisión del modelo (retrieval determinista; el LLM consume, no recupera).
- `core/engine/context/composer.py` (mecanismo determinista existente), `features/cognitive_knowledge/context/providers.py` (patrón de provider raw → el nuevo provider sube el nivel a joins), `features/main/task_execution/application/intelligence_service.py` (JOINs con propósito por fase).