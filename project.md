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
