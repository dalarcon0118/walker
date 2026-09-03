# Walker Project Constitution

> **NOTA (SPEC-005, aprobada):** Este documento es una **proyección derivada** del
> DeltaGraph (índice delta — specs `approved` con `invariants`/`project_snapshot`).
> **No es la fuente de verdad**: la constitución del proyecto vive en
> `features/delta_tracking` (SSOT, ADR-WALKER-013/016) y este archivo puede
> regenerarse desde esos datos en cualquier momento.

## Invariantes del sistema

Estos principios son **inmutables** a menos que se apruebe un ADR explícito que los modifique.

| ID | Invariante | Razón |
|----|------------|-------|
| INV-001 | WorkflowState nunca se pierde entre submits/transitions | Es el stock central del sprint; sin él, el agente no tiene memoria del progreso |
| INV-002 | JSON-RPC 2.0 es el transporte MCP obligatorio | El protocolo MCP es la interfaz canónica con agentes LLM; cambiarlo rompe compatibilidad |
| INV-003 | `_handle_request` es la entrada MCP única | Toda tool MCP pasa por aquí; no hay atajos |
| INV-004 | delta_tracking es el SSOT del sprint y specs | ADR-WALKER-036; toda mutación de estado de sprint/spec pasa por delta |
| INV-005 | Side effects son atómicos: o se ejecutan todos o ninguno | Si `transform_spec` falla a mitad de una cascada, el sistema queda zombie |
| INV-006 | Task names en TaskRegistry son la interfaz LLM pura | No se expone CPG/CKG/directamente al LLM; solo via tasks |
| INV-007 | Los tests E2E usan `tmp_path` para aislar datos | Garantiza reproducibilidad; no hay estado compartido entre tests |
| INV-008 | El workflow engine auto-advance respeta `on_success` | Es la única forma de avanzar sin gates explícitas; mantenerlo predecible |
| INV-009 | ContextComposer respeta `context_budget` por nodo | Evita tragedia de los comunes en contexto LLM |
| INV-010 | ModuleRegistry y TaskRegistry son SSOT separados | ModuleRegistry = tools MCP; TaskRegistry = handlers. No hay registro paralelo |
| INV-011 | Toda operación retorna `Result[T, E]` de `returns` | Evita excepciones como control de flujo; hace el fallo explícito y testeable |
| INV-012 | Las funciones públicas deben ser puras o casi-puras | Sin efectos secundarios ocultos; todo IO se declara en la firma (port/repository/side_effect) |
| INV-013 | Use Cases son implementación interna del feature; Application Services son la fachada pública (ADR-WALKER-048) | Nadie fuera del feature importa Use Cases directos; el composition root registra solo Application Services y Domain Services; tasks y side-effects consumen fachadas |
| INV-014 | features/main NO es un feature: es composition root + cross-feature orchestrator (ADR-WALKER-050) | main no tiene Use Cases propios, pero sí Application Services cross-feature (orquestación multi-feature); los tasks son thin adapters sin lógica de negocio |
| INV-015 | El paquete distribuible es **`shared`**; sus componentes internos (`core.engine`, `libs.*`, `common.*`) son autónomos y no saben quién los consume; `shared` se AUTOREGISTRA por id en el contenedor DI único del proceso al importarse, para ser consumido por DI (id+contrato, ADR-062 / ADR-WALKER-067) | `engine` (`shared.core.engine`) y sus hermanos son módulos internos: NO re-exportan clases concretas de componentes hermanos (p.ej. `engine/__init__` NO re-exporta `shared.libs.module_system`; los consumidores resuelven `shared.engine` por id en el `ModuleRegistry`), NO importan `features/*` ni el host, NO asumen el root del repo. `shared` se autoregistra en `module_registry` (el `ModuleRegistry` único del proceso, en `shared.libs.module_system.registry`; `shared_registry` es alias de legibilidad) al importarse: registra el módulo "shared.engine" (por id, con contratos en `metadata`) + adaptadores por defecto host-agnósticos (por contrato, `register_port`). El composition root (app) NO registra `shared`: usa ese mismo singleton y solo añade los providers del host, declarados en `app/module.py` como módulo NestJS (factories lazy por id+contrato, ADR-WALKER-067 §2.4 — sin glue manual ni monkeypatch). **El engine NO consume conceptos de negocio** (IProjectContext, IConflictResolver, ISprintService, IPromptCollector): su contrato de entrada es `EngineBundle` (solo mecánica: repos, IO, tasks, side-effects, events, tracer; ADR-WALKER-067 §2.3). Las reglas de negocio (trabajo sin capturar, springs informales, placeholders de prompts, conflictos de specs) las aplica el HOST externamente, antes/después de submit/transition, jamás dentro del engine. Así "llevarse shared" funciona: basta `import shared`. Consumidores resuelven: `module_registry.get_module("shared.engine")` / `resolve_port(contrato)` / `get_service("shared.engine")`. Regla de oro: "un componente interno no sabe quién lo usa; `shared` sí, porque es quien se autoregistra en el contenedor DI". |
| INV-016 | El código **funciona o genera un error** — fail-fast, sin fallbacks implícitos (ADR-WALKER-061/063) | No hay backends de fallback, no hay defaults silenciosos, no hay degradación silenciosa. El `backend` de `CognitiveGraph` es OBLIGATORIO (nunca se construye FileBackend implícito; `sqlite` es el único backend runtime). `build_cognitive_graph()` es el único constructor canónico (resuelve backend por config). Los repos reciben `data_dir` resuelto por el caller (nunca computan rutas internas). Todo componente no resuelto → `ValueError`/`StorageError` accionable. Un error nos dice que algo falló; la ausencia de error nos dice que funcionó. |

---

## Configuración del workflow: GLOBAL del motor, no por-proyecto

> **Decisión constitucional** — define qué es configuración del motor y qué es
> memoria de proyecto. Refuerza INV-016 (decidir explícitamente el layout).

### Principio

**El workflow (`workflow.json` + `nodes/*.node.json` + `edges.json`) es UNO solo:
configuración global del motor**, que vive en el **root del walker**
(`mcp/data/workflow/`; legacy: `data/workflow/`). **Todos los agentes y todas las
instancias del walker que operan hoy usan ESTA MISMA configuración.** No existe
"el workflow de un proyecto": cada proyecto **no** define su propio flujo.

Lo que **sí es por-proyecto** es el **contexto del propio proyecto**: su memoria
cognitiva — **CPG** (índice del código), **CKG** (conocimiento: decisiones,
convenciones, patrones), **DeltaGraph** (sprints, specs, tasks, springs) y
**TraceGraph** (eventos de sesión) — más el **estado del workflow** (`state`) y
las sesiones. Todo ello vive aislado por proyecto (directorio cognitivo del
proyecto).

En una línea: **el flujo, las tasks y los prompts son idénticos para todos los
proyectos; lo único que cambia por proyecto es su CONTEXTO, guardado en las
memorias (CPG, CKG, DeltaGraph, TraceGraph).**

Por eso el workflow está en el root (`walker/mcp/data/workflow/`) y **nunca dentro
del directorio cognitivo del proyecto**: el workflow es **configuración del motor**
(compartida, versionada, una sola fuente), mientras que la memoria cognitiva es
**contexto por proyecto** (mutante, aislado por proyecto).

### Topología de datos

**GLOBAL — configuración del motor (idéntica en cualquier proyecto, compartida por todos los agentes):**

| Componente | Contenido |
|-----------|-----------|
| `mcp/data/workflow/` | `workflow.json`, `nodes/*.node.json`, `edges.json`, `schema/` — SSOT del flujo BPMN |
| Templates de prompts | `fast_mcp/features/workflow/application/prompts/templates/` — prompts por nodo/fase (parte del workflow) |
| TaskRegistry | las tasks del agente (`fast_mcp/features/tasks/handlers/`: implement, verify, observe, state_construction, info_acquisition, monitoring, …) — las mismas tareas en cualquier proyecto |
| `config/config.json` | claves de comportamiento del motor (`workflow.manifest`, quality, scan…) |
| `data/projects.json` | catálogo global de proyectos gestionados |

**POR-PROYECTO — contexto del proyecto (memoria cognitiva aislada por proyecto):**

| Componente | Contenido |
|-----------|-----------|
| **CPG** | índice del código del proyecto (símbolos, llamadas, estructura real) |
| **CKG** | conocimiento del proyecto (decisiones, convenciones, patrones) |
| **DeltaGraph** | sprints, specs, tasks, springs del proyecto |
| **TraceGraph** | eventos de sesión del proyecto |
| workflow `state` | posición del proyecto en el flujo (nodo/subestado, submits, historia) |

### Implicaciones de diseño

1. **`chain_dir` identifica el proyecto para ESTADO y MEMORIA** (dónde leer/escribir
   su `.cognitive/`), **nunca para cargar el workflow**. La resolución del workflow
   es siempre la del motor: `PathManager.engine_manifest()` —
   `<walker_root>/mcp/data/workflow/workflow.json` — con override explícito
   (`env WALKER_WORKFLOW_MANIFEST` / config `workflow.manifest`) y fail-fast
   (INV-016) si no existe.
2. Un proyecto target **no debe llevar su propio `workflow.json`**: si lo llevara,
   sería duplicación de configuración global (dos SSOT en conflicto, anti-patrón
   ADR-047). El hook de guardas puede asumir que el workflow vivirá en el root.
3. La parametrización por-proyecto del flujo (si algún día se necesita variar el
   flujo por proyecto) se resolverá con **perfiles de workflow declarados en
   config global** (`workflow.profile: <variante>`), no con workflow.json
   copiados dentro de cada proyecto.

### Walker se gestiona a sí mismo como proyecto

Walker es **dogfooding total**: el propio walker está registrado y gestionado
**como un proyecto más** por el walker. Su memoria cognitiva (su CPG, su CKG,
su DeltaGraph con sus sprints/specs, su TraceGraph) vive en su propio root,
y **usa exactamente el mismo workflow global y las mismas tasks que cualquier
otro proyecto** — sin casos especiales ni ramas de código "walker sobre walker".
Consecuencia directa: cualquier cambio en `mcp/data/workflow/` o en el
TaskRegistry afecta por igual a walker-sobre-sí y a walker-sobre-cualquier-
otro-proyecto; y la memoria de walker (su contexto como proyecto) NO se
confunde con la configuración del motor, aunque compartan repositorio.

### Deuda registrada (alineación de código con esta decisión)

La implementación actual `PathManager.resolve_for_project(chain_dir)` y
`features/workflow/context.py::_find_workflow_path(chain_dir)` buscan el workflow
**dentro del chain_dir del proyecto** — herencia del modelo per-proyecto. Con esta
decisión, el modelo correcto es: `_find_workflow_path` debe resolver SIEMPRE
`engine_manifest()` (global del motor) y **descartar** los candidatos dentro del
`chain_dir` (`{chain_dir}/mcp/data/workflow/`, `{chain_dir}/workflow.json`), que
hoy existen como candidatos legacy. Pendiente de corregir con sus tests (el
live-walk que copia el SSOT a `tmp_path` debe migrar a apuntar el override
`WALKER_WORKFLOW_MANIFEST` al SSOT del motor o a un fixture de workflow de test).

---

## Decisiones arquitectónicas

| ID | Decisión | Estado |
|----|----------|--------|
| ADR-WALKER-032 | `delta_analyze` unifica tools fragmentadas de delta | Aprobada |
| ADR-WALKER-033 | Domain events en `delta_tracking` para lifecycle | Aprobada |
| ADR-WALKER-036 | `delta_tracking` es SSOT y SRP del spring | Aprobada |
| ADR-WALKER-043 | Schemas de tools definidos una sola vez (ToolRegistry) | Aprobada |
| ADR-WALKER-016 | DIP: task_execution define puertos, code_analysis implementa | Aprobada |
| ADR-WALKER-048 | Patrón canonical de capa de aplicación: Use Cases + Application Services + Domain Services (SPEC-011) | Aprobada |
| ADR-WALKER-050 | Boundaries de `features/main`: composition root, cross-feature orchestrators, tasks thin adapters | Aprobada |

### Capa de aplicación canonical (ADR-WALKER-048 / SPEC-011)

Todo feature adopta la estructura `application/use_cases/ + application/services/ + domain/services/`:

| Capa | Ubicación | Responsabilidad | Visibilidad |
|------|-----------|-----------------|-------------|
| **Use Cases** | `features/<feature>/application/use_cases/` | Lógica de negocio atómica. Una clase = una acción. Recibe puertos, devuelve entidades o `Result[T, E]`. No sabe de HTTP/MCP/CLI/dicts | Interna al módulo (INV-013) |
| **Application Services** | `features/<feature>/application/services/` | Fachada pública: orquesta use cases, traduce entidades ↔ dicts. Punto de entrada para MCP/CLI/engine. Sin lógica de negocio | Pública (la registra el composition root) |
| **Domain Services** | `features/<feature>/domain/services/` | Lógica pura sin IO: invariantes, validaciones, transiciones de estado | Pública |
| **App Services cross-feature** | `features/main/cli_interface/application/` y `features/main/task_execution/application/` | Orquestan múltiples features (ej: `PrepareProjectService`, `CheckHealthService`, `TaskIntelligenceService`) | Pública |

Reglas de consumo:
- Use Cases NUNCA se importan desde fuera del feature (INV-013) y NUNCA se registran en el composition root.
- Tasks y side-effects de `features/main/task_execution/` consumen Application Services (0 imports de `use_cases`).
- El composition root (`app/composition/`) registra solo Application Services, Domain Services e infrastructure.

---

## Metodología de diseño

### Segundo orden primero

Antes de aprobar cualquier cambio, responder:
1. ¿Qué módulos importan este?
2. ¿Qué side effects dispara?
3. ¿Qué eventos de dominio publica?
4. ¿Qué tests dependen de este comportamiento?
5. ¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?

Si la respuesta a alguna es "no sé", el cambio tiene **riesgo de segunda orden no controlada**.

### Pre-mortem obligatorio

Toda spec/ADR nueva debe incluir una sección `failure_scenarios`:
```json
"failure_scenarios": [
  "Si delta_tracking se corrompe, toda la trazabilidad se pierde → mitigación: backup diario",
  "Si _SERVER se cachea y el workflow cambia, tests ven estado viejo → mitigación: reloader + tests con tmp_path"
]
```

### Métricas de calidad por fase (checklists de fase)

Cada **fase** de una spec/ADR incorpora **dentro de su checklist** las métricas
aplicables a las áreas que toca (mínimo **3 métricas** por fase). Las métricas
son pares **patrón-obligatorio / anti-patrón-bloqueante**: un anti-patrón
detectado en la implementación de la fase impide declarar el checklist en verde.

| ID | Área | Prácticas a vigilar (métricas) | Patrón de referencia | Anti-patrones (bloqueo) |
|----|------|-------------------------------|----------------------|--------------------------|
| M-01 | Integridad de escritura | Transaccionalidad todo-o-nada (commit atómico), eventos **post-commit** (nunca dentro de la transacción), logging de auditoría | Validación temprana; sin efectos ocultos | Transaction Script (secuencia sin transacción), Hidden Dependency |
| M-02 | Variabilidad de estrategias | SSOT de estrategias (una fuente por decisión), precedencia clara, caché con invalidación explícita | Strategy pattern, Context Builder | Magic Numbers, Shotgun Surgery, Duplicate Code |
| M-03 | Modelo de dominio | Una responsabilidad por entidad, idempotencia de operaciones, audit trail | Dataclasses, update de campos específicos, bulk updates | Anemic Domain Model, Data Clumps, Boolean Trap |
| M-04 | Eventos / acoplamiento | Eventos como contrato, desacoplamiento, resiliencia | TEA pattern (Transaction→Event→Audit, SPEC-009/ADR-033), idempotencia por diseño | Distributed Big Ball of Mud, Eventual Consistency sin safeguards |
| M-05 | Concurrencia / validación | Transacción explícita, lock pesimista, auditoría | Separación vista/servicio, validación en capas | Anemic Domain Model, God Class, Magic Numbers |
| M-06 | Extensibilidad / configuración | Factory pattern, polimorfismo, configuración externa | Open/Closed, SRP, Composition over Inheritance | Not Invented Here, Accidental Complexity |

Mecánica de integración en cada fase:

1. Al redactar una fase, identificar las áreas que toca (escritura, eventos,
   concurrencia, dominio, estrategias, configuración).
2. Añadir al checklist de la fase un ítem por área seleccionada, con criterio
   medible PARA ESA FASE:
   `- [ ] M-0X <criterio de la fase> — anti-patrón a evitar: <...>`
3. Regla de bloqueo: mínimo 3 métricas por fase; un anti-patrón de un área
   seleccionada detectado en la revisión del código = checklist NO verde hasta
   corregir. Las métricas no aplicables se declaran como tal (no se omiten en
   silencio).

### Presupuesto de contexto

El `workflow.json` define un `context_budget` por nodo. El `ContextComposer` lo respeta.
Sin presupuesto, el sistema sufre la tragedia de los comunes.

### Documentación

- `project.md`: constitución del proyecto, invariantes, decisiones.
- `docs/ARCHITECTURE-MODELS.md`: catálogo de modelos de pensamiento.
- `AGENTS.md`: proceso de contribución, checklist, invariantes.

---

## Estado del proyecto

| Capa | Estado | Cobertura E2E |
|------|--------|---------------|
| MCP Tools (28 tools) | Estable | 12/12 tests MCP E2E pasan |
| CLI (6 comandos) | Estable | 6/6 tests pasan |
| RAG Pipeline (CPG→CKG→Delta→Trace) | Estable | 4/4 tests pasan |
| Agent Session | Estable | 5/5 tests pasan |
| Workflow Engine | Estable | Usado por 27 tests E2E |
| Delta Tracking | Estable | SSOT del spring |
| TaskRegistry | Refactorizado | DomainTaskRegistry + SystemTaskRegistry separados |
| Context Budget | Implementado | Presupuesto por nodo en workflow.json |
| Arquitectura | Documentada | SPEC-004 + docs/ARCHITECTURE-MODELS.md |
| Proceso | Actualizado | AGENTS.md con pre-mortem + segundo orden |

---

## Análisis crítico: teoría del modelo delta vs. implementación

> **Fecha:** 2026-09-02 | **Última verificación:** suite fast_mcp 172 passed, 0 failed

### El propósito real de Walker (marco de análisis)

La teoría en `docs/teoria.md` define el contrato que Walker debe cumplir para ser
útil: **G_objetivo = G_actual + Δ**. Cada tarea es un subdelta `{qué, cómo, validación}`
que debe **reducir de forma verificable la distancia a G_objetivo**. No basta con
"avanzar en el workflow": un submit solo importa si mueve el sistema hacia el
objetivo y eso se puede comprobar.

Este análisis contrasta la implementación real (evidencia en código, no especulación)
contra ese contrato.

> **Refinamiento del modelo (ADR-WALKER-072):** la descomposición de G_objetivo se da en
> **sprints** (deltas intermedios, cuya suma de goals = estado deseado), y cada **submit** es
> progreso *local* dentro del sprint en curso — **no** incrementa G_actual. G_actual se
> actualiza solo al **completar un sprint** (`G_actual += Δᵢ`). Ver `docs/ADR-WALKER-072-*` y
> `docs/teoria.md` §5.

---

### (a) Qué de la teoría SE hace realmente

| Contrato teórico | Implementación | Evidencia |
|------------------|----------------|-----------|
| Persistir cada avance sin perder estado | `submit` persiste el estado + revision bump (optimistic concurrency) | `orchestrator.py` `_submit_impl` pasos 5–8 |
| Ciclo plan→spec→implement→verify→retro | El workflow define 8 nodos con sub-estados por fase | `mcp/data/workflow/nodes/*.node.json` |
| Verificación tipada de predicates (>alucinación) | `VERIFY.PREDICATE_CHECK` es `execution: auto`, `action: verify_predicates` | `verify.node.json` línea 64 |
| Motor real de verificación con cache | `verify_engine.py` (predicates: symbol_exists, test_pass, grep, ...) | `mcp/shared/tasks/infrastructure/verify_engine.py` |
| Design by Contract | `ContractValidator.verify_pre/post/invariant` | `mcp/shared/core/engine/domain/contract_validator.py` |
| Trazabilidad completa del avance | `node_executions` graba cada submit/transition | `status` en orchestrator |

### (b) Qué de la teoría FALTA (la brecha real)

El port fast_mcp **trunca el engine a un "contador avanzado de estados"**: el
`submit` registra datos y guarda, pero **nunca ejecuta la transformación del
sub-delta**.

1. **El `submit` NO ejecuta `sub_state.action`.** El legacy `SubmitDataUseCase._execute_task`
   (submit.py línea 285) ejecutaba la acción real (`plan_sprint`, `spec_task`,
   `verify_predicates`, `run_tests`, ...) y solo entonces registraba. El port fast_mcp
   (`orchestrator.py`) escribe directamente `task_ok=True` (línea 196) **sin ejecutar
   nada**. Un sub-estado `llm_task` con `action: spec_task` **no genera spec**.

2. **`verify_predicates` no puede funcionar en el port.** El `submit` fast_mcp no
   inyecta predicates (el legacy `_inject_spec_predicates`, submit.py línea 330/381,
   está ausente en fast_mcp) y no hay ActionCatalog que dispare `verify_predicates`.
   El nodo la declara (`verify.node.json` línea 64) pero el engine **no la ejecuta**.

3. **No se mide la distancia a G_objetivo.** El contador disponible,
   `graph_progress.distance_to_terminal`, es **hops BFS del grafo** (`GraphAnalyzer`),
   no una métrica semántica de `G_actual vs G_objetivo`. No existe ningún cálculo que
   diga "cuánto acercó este submit al objetivo del sprint".

4. **No hay sub-delta formal.** La teoría exige `{qué, cómo, validación}` +
   `impacted_artifacts`, `risks (curvature/pre_churn/trust)`, `refinement
   (attempts/dead_ends)` y `validation`. **Nada de esto existe en la implementación**:
   no hay WIP limits, ni risks, ni attempts/dead_ends, ni validación de
   output_contract (`verify_post`).

5. **No hay side effects ni auto-advance.** El legacy tenía `_execute_side_effects`
   (Saga/Outbox) y `_auto_advance` (submit.py línea 514). El port no tiene ninguno de
   ambos: el submit **no avanza** a pesar de que muchos sub-estados son `execution:
   auto` con `on_success`.

### (c) Qué puede mejorar para cumplir el propósito

1. **Ejecutar la acción del sub-estado en `submit`** (el mayor déficit). Reintroducir
   ActionCatalog/TaskRegistry en el port: `submit` debe disparar la acción declarada
   y usar su resultado como `task_ok`, no hardcodear `True`.
2. **Reintroducir `_auto_advance` + side effects.** Los sub-estados `auto` con la
   acción resuelta deben avanzar solos; esto es lo que hace que el workflow "fluya"
   hacia el objetivo en lugar de ser una secuencia manual de clics.
3. **Restaurar `verify_predicates` vía `_inject_spec_predicates`.** Es el único
   componente anti-alucinación del ciclo; sin él, la acceptación de tareas no
   comprueba que el trabajo entregado cumple los predicates.
4. **Medida de progreso hacia el objetivo.** Sustituir/aumentar `distance_to_terminal`
   (hops) con una evaluación de cuántos predicates de la spec activa quedan por
   satisfacer: eso hace la "reducción de distancia a G_objetivo" medible.

### Herramientas fast_mcp registradas (estado)

| Fase | Tool | Estado | Notas |
|------|------|--------|-------|
| F1 | `prepare` / `list` / `status` / `list_projects` | ✅ | Gestión de proyectos + estado |
| F2 | `inspect_codebase`, `knowledge_*`, `read_code`, `code_*`, `index_repository`, `extract_knowledge` | ✅ | CPG + CKG (superficie de conocimiento) |
| F3 | `submit` / `transition` / `help` | ✅ | Workflow — **anémico** (no ejecuta acción) |

### Tools de ejecución pendientes de portar (la brecha central)

| Tool | Ubicación legacy | Por qué importa |
|------|------------------|-----------------|
| `implement_task` | `shared/tasks/actions/implement.py` | La transformación real del código |
| `verify_predicates` | `features/delta_tracking/tasks/verification.py` | Validación anti-alucinación del entregable |
| `run_tests` | `shared/tasks/actions/verify.py` | `VERIFY.QA_RUN` la declara (`verify.node.json` línea 13) |
| `spec_task` / `plan_sprint` / `refine_*` | `shared/tasks/actions/` | Acompañan cada fase del ciclo |
| El resto de acciones con `action:` en `*.node.json` | `shared/tasks/actions/` | Sin ActionCatalog, `action` es decorativo |

### Resumen ejecutivo

Walker hoy es una **máquina de registrar y avanzar estados**, no una máquina de
**aplicar sub-deltas verificables**. La infraestructura (workflow, persistencia,
trace, CPG/CKG, verificación tipada) está sólida; lo que falta es **conectar el
engine con la ejecución real de las acciones declaradas en el grafo**. Mientras `submit`
no ejecute `sub_state.action` ni mida la proximidad a G_objetivo, Walker sabe *dónde
está* el agente pero no *qué logró* ni *cuánto acercó al objetivo**.

---

## Próximas mejoras (backlog)

| ID | Mejora | Prioridad | Estado |
|----|--------|-----------|--------|
| SPEC-003 | Cobertura E2E MCP + CLI + RAG | Alta | ✅ Implementada (27/27 tests) |
| SPEC-004 | Modelos de pensamiento para diseño y evolución | Alta | ✅ Documentada |
| SPEC-005 | Separar TaskRegistry en DomainTaskRegistry + SystemTaskRegistry | Alta | ✅ Implementada |
| SPEC-006 | Implementar context_budget en workflow.json y ContextComposer | Alta | ✅ Implementada |
| SPEC-007 | Documentar invariantes (este documento) | Alta | ✅ Completada |
| SPEC-008 | Crear `docs/ARCHITECTURE-MODELS.md` | Media | ✅ Completada |
| SPEC-009 | Side effects atómicos (Outbox/Transaction pattern) | Media | ✅ Implementada |
| SPEC-010 | Ritual pre-mortem en AGENTS.md | Media | ✅ Completada |
| SPEC-001 | Unificar protocolo MCP como SSOT dinámico | Alta | ✅ Implementada |
| SPEC-002 | Traza verificable multi-canal (cpg_indexed, side_effect_execute, channel) | Media | ✅ Implementada |
| ADR-072 | Modelo delta refinado: descomposición G_objetivo→sprints + regla temporal de G_actual (submit≠incremento; completar sprint → G_actual+=Δᵢ) | Alta | 📝 Documentada (ADR en revisión; implementación pendiente) |
