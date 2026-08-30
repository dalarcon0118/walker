# ADR-WALKER-062 — Arquitectura: Clean Architecture + Hexagonal (Puertos y Adaptadores) con Service Locator por Contrato

- **Status:** Aceptado (en ejecución)
- **Date:** 2026-08-26
- **Implementado:** `shared/core/engine` (`IModuleRegistry`, `InMemoryModuleRegistry`, `AppContainer`), `features/*/ports`, `features/*/infrastructure/adapters`, `features/main/composition`
- **Autor:** Walker coding agent (hy3-free) + product owner
- **Complementa:** ADR-WALKER-059 (dispatcher con registry inyectado, sin service locator en el dispatcher), ADR-WALKER-061 (storage como puerto), ADR-WALKER-060 (guards/interceptors), SPEC-004 (modelos de pensamiento), SPEC-005 (#5 dispatch único)

## 1. Contexto / Problema

Walker creció como un monolito donde las dependencias se resolvían de forma ad-hoc. El objetivo
**no** es eliminar el service locator, sino usarlo *sabiamente*: **Clean Architecture** (regla de
dependencia hacia adentro) + **Hexagonal / Puertos y Adaptadores**, con un *services manager* que
resuelve por **id + contrato** (idiom NestJS `ModuleRef.get(id)`). Así el consumidor se acopla al
id y al contrato (puerto), no a la clase concreta: quien provee el servicio es intercambiable.

### 1.1 Estado actual (ya parcialmente alineado — se codifica, no se inventa)

- `IModuleRegistry` (`shared/core/engine/interface/contracts.py:74`) es el **contrato del services
  manager**; `InMemoryModuleRegistry` (`shared/core/engine/infrastructure/module_registry.py:24`) lo
  implementa. Su doc dice literalmente: *"los features resuelven por clave (get_service) en vez de
  importar implementaciones concretas de shared/ directo (inversión de control)"*.
- `AppContainer` (`features/main/composition/container.py`) es el **composition root**: recibe los
  servicios core por constructor (`task_registry`, `workflow`, `side_effect_engine`) y registra el
  resto por clave en el `module_registry` (ids: `task_registry`, `workflow`, `side_effect_engine`,
  `module_registry`, `action_catalog`, `cpg`, `ckg`, `delta`, `trace`, `state`, `project`, `task`, …).
- Ya existen **puertos**: `ICpgAnalysisPort` (+ `WalkerCpgAnalysisAdapter`), `ICkgServicePort`,
  `TaskRegistry(ABC)`, `ContextProviderRegistry`, `DeltaRepository`, y ports del BC "Protocol"
  (`features/main/cli_interface/domain/ports.py`).
- Ya hay un **resolver híbrido DI ↔ locator**: `resolve_cpg_port(chain_dir, port: ICpgAnalysisPort | None = None)`
  acepta el puerto inyectado o lo resuelve.

### 1.2 Anti-patrones a erradicar (NO el locator, sino el acoplamiento concreto)

| ID  | Anti-patrón                                          | Dónde                                                                | Impacto                                          |
|-----|------------------------------------------------------|----------------------------------------------------------------------|--------------------------------------------------|
| AP-1| Importar la clase concreta del adapter desde app     | `from features.x.infrastructure import WalkerCpgAnalysisAdapter`     | rompe regla de dependencia; ciclos               |
| AP-2| `get_service(id)` usado como `Any` y acoplado a impl | castear a métodos de la impl concreta                                | el provider deja de ser swappable                |
| AP-3| Inyectar/guardar la instancia concreta en el call-site | `handle_task(req, task_registry=<objeto concreto>)` (cambio revertido) | acopla al servicio en sí, no al id+contrato   |
| AP-4| `shared/core/engine` importa `features/`             | —                                                                    | ciclo de composición                             |
| AP-5| Sin gobernanza: la deuda se re-introduce             | sin CI                                                               | la regla no se verifica                          |

**Gap real:** `get_service(name) -> Any` resuelve por id pero **no valida el contrato**. "Through a
contract" hoy es una convención, no un chequeo. Eso es lo que P1 endurece.

## 2. Decisión

Sobre el **Modular Monolith** existente, adoptar **Clean Architecture + Hexagonal**. Tres mecanismos
coexisten, cada uno con su lugar:

1. **Regla de dependencia (Clean Architecture):** las dependencias apuntan siempre hacia adentro.
   `shared/core/engine` y el dominio/app de `features/*` **NO** importan `infrastructure` concreta;
   dependen de **puertos** (`*Port`, `TaskRegistry`, `IModuleRegistry`). `infrastructure/` (adaptadores)
   es la capa externa y SÍ puede importar puertos e implementarlos.
2. **Puertos y Adaptadores (Hexagonal):** todo servicio que el dominio/app necesita se define como
   **puerto** (`ABC`/interface). Los **adaptadores** viven en `infrastructure/` y se registran en el
   root bajo un **id de servicio** ligado a ese puerto.
3. **Services manager por id + contrato (el locator, usado sabiamente):** `IModuleRegistry.get_service(id)`
   es el mecanismo sancionado de resolución en runtime (NestJS `ModuleRef.get(id)`). El consumidor se
   acopla al **id + contrato (puerto)**, no a la clase. Para hacer "through a contract" real:
   `get_service(id, contract=Port)` (o `register_service(id, inst, contract=Port)`) **valida
   `isinstance(inst, contract)`** y falla fuerte si no cumple → el provider es intercambiable mientras
   cumpla el contrato.
4. **DI en el composition root:** `AppContainer.build()` construye los adaptadores y los registra;
   donde es factible, inyecta el **puerto resuelto** (no el manager completo ni el objeto suelto) en
   el consumidor (constructor injection) — ver `resolve_cpg_port(port: ICpgAnalysisPort | None = None)`.
   El manager se inyecta solo cuando el consumidor necesita resolver múltiples ids en runtime
   (handlers de módulo).

### 2.1 Reglas de frontera (invariantes)

- `shared/core/engine` **NO** importa `features/`. Los servicios cross-cutting del root se resuelven
  vía la fachada neutral `_SERVICE_LOCATOR` (no `features.main`).
- Dominio/app (`features/*/domain`, `features/*/application`) depende solo de puertos, nunca de
  `infrastructure` concreta.
- `get_service(id)` es la única forma de resolver un servicio por id en runtime; valida contrato
  cuando se pasa `contract=`.
- Nunca se inyecta/guarda la instancia concreta del adapter en un call-site de app/dominio.

## 3. Consecuencias

- **+** Testeabilidad (mockeas el puerto, no el monolito), **+** swappability (cambias el adapter, no
  el consumidor), **+** fronteras claras.
- **−** Algo más de boilerplate (puertos), pero acotado; el root sigue siendo el único lugar de cableado.

## 4. Mapeo a Walker (concreto)

| Concepto                       | En Walker                                                                 |
|--------------------------------|---------------------------------------------------------------------------|
| Services manager / ModuleRef   | `IModuleRegistry` → `InMemoryModuleRegistry`; `app.module_registry.get_service(id)` |
| Composition root               | `AppContainer.build()` (`features/main/composition/container.py`)         |
| Puertos                        | `ICpgAnalysisPort`, `ICkgServicePort`, `TaskRegistry(ABC)`, `ContextProviderRegistry`, `DeltaRepository`, `IModuleRegistry`, ports de `cli_interface/domain/ports.py` |
| Adaptadores                    | `WalkerCpgAnalysisAdapter`, `walker_ckg_adapter`, `DomainTaskRegistry`/`SystemTaskRegistry`, `*Provider` en `features/*/infrastructure` y `context/providers.py` |
| Registro por id                | `register_service("task_registry", …)`, `register_service("cpg", …)`, …  |
| Resolución por id+contrato     | `resolve_cpg_port(…, port=ICpgAnalysisPort)`, `get_service("cognitive_graph")(paths)` |
| DI híbrida                     | `AppContainer.__init__(task_registry=…, workflow=…)` + `module_registry` para el resto |

## 5. Plan de ejecución (P0–P4)

- **P0 — Gobernanza dura:** CI con (a) gate DAG de composición (hard fail si hay ciclo nuevo; corregir
  primero el bug de `import_graph.module_deps` que da falsos negativos); (b) import-linter que prohíba
  `features/*/application|domain` → `features/*/infrastructure` y `shared/core/engine` → `features`;
  (c) `radon mi` umbral + trend; (d) cobertura `pytest --cov` con threshold de trend.
- **P1 — Contratos first-class:** (1) `get_service(id, contract=None)` valida `isinstance`;
  `register_service(id, inst, contract=Port)` lo exige. (2) Cada servicio registrado declara su puerto.
  (3) **Revertido** el cambio que inyectaba el objeto concreto `task_registry` en `handle_task`
  (vuelve a `app.module_registry.get_service("task_registry")`). (4) Documentar ids+contratos en `project.md`.
- **P2 — Adaptadores en `infrastructure/`:** mover implementaciones concretas fuera de
  `application`/`domain`; el dominio depende solo de puertos.
- **P3 — Guards/Interceptors** (ADR-060 F1) como middleware de tools/handlers.
- **P4 — Zero-legacy + arch tests:** tests arquitectónicos (import-linter en repo) como gate;
  completitud del root (`EXPECTED - available`).

## 6. failure_scenarios (AGENTS.md: min 3, cada uno fallo / causa raíz / mitigación / detección)

- **FS-1 — Un consumidor importa la clase concreta del adapter (rompe regla de dependencia).**
  - *Fallo:* `from features.x.infrastructure import WalkerCpgAnalysisAdapter` en `application` → ciclo / acoplamiento.
  - *Causa raíz:* el dev resolvió importando la impl en vez de usar el puerto + `get_service`.
  - *Mitigación:* import-linter `features.*.application` ✗ `features.*.infrastructure`; el consumidor depende de `ICpgAnalysisPort` y resuelve por id.
  - *Detección:* import-linter en CI; test arquitectónico que liste imports prohibidos.

- **FS-2 — `get_service` devuelve un objeto que NO cumple el contrato (swap silencioso roto).**
  - *Fallo:* se registra un adapter bajo `id` pero no implementa el `Port`; el consumidor castea y falla en runtime.
  - *Causa raíz:* `get_service(name) -> Any` no valida tipo.
  - *Mitigación:* P1 — `get_service(id, contract=Port)` / `register_service(id, inst, contract=Port)` validan `isinstance`.
  - *Detección:* test de completitud del root que resuelve cada id con su contrato y aserta `isinstance`.

- **FS-3 — Un id no está registrado en un call-site → `KeyError`.**
  - *Fallo:* handler resuelve `get_service("x")` y el root no lo registró.
  - *Causa raíz:* olvido en `AppContainer.build()` o id mal escrito.
  - *Mitigación:* test de completitud del root (patrón AGENTS.md) `EXPECTED - available` falla en arranque.
  - *Detección:* smoke test que resuelve todos los ids al iniciar; `test_container_completeness_bdd.py`.

- **FS-4 — Ciclo de composición al cablear un adapter.**
  - *Fallo:* adapter importa `features.main` (o `shared` importa `features`) cerrando ciclo.
  - *Causa raíz:* capa externa depende hacia afuera.
  - *Mitigación:* fachada neutral `_SERVICE_LOCATOR` para servicios cross-cutting del root; adapters solo dependen de puertos.
  - *Detección:* gate DAG en CI.

- **FS-5 — Inyectar el manager completo imposibilita testear el consumidor aislado.**
  - *Fallo:* pasan `module_registry` entero y el test debe arrancar todo el root.
  - *Causa raíz:* inyectar el container en vez del puerto.
  - *Mitigación:* inyectar el **puerto** (mock fácil) donde se pueda; el manager solo para resolución por id en runtime.
  - *Detección:* revisión + `test_phase_intelligence_bdd.py::FakeRegistry` como ejemplo de mock de puerto.

- **FS-6 — Colisión de ids (dos adapters bajo la misma clave).**
  - *Fallo:* behavior equivocado porque el último registro pisa al anterior.
  - *Causa raíz:* ids no namespace-ados / registro duplicado silencioso.
  - *Mitigación:* `register_service` warn/rechaza duplicados en modo estricto; ids con namespace por BC.
  - *Detección:* test de registro que aserte unicidad (`list_services()`).

## 7. Checklist de segundo orden (AGENTS.md)

1. **¿Qué módulos importan este?** `shared/core/engine` (contrato + impl), `features/main/composition` (root), todos los `application`/`domain` que resuelven por id, `infrastructure` de cada BC.
2. **¿Qué side effects dispara?** ninguno nuevo; solo formaliza cómo se resuelven dependencias (id+contrato).
3. **¿Qué eventos de dominio publica?** igual que hoy (trace del dispatch); sin cambios de contrato.
4. **¿Qué tests dependen de este comportamiento?** `test_module_registry_di_bdd.py`, `test_container_completeness_bdd.py`, `test_registry_completeness_bdd.py`, `test_composition_root_d1.py`, `test_workflow_production_di_bdd.py`, `test_trace_event_publisher_bdd.py`, `test_phase_intelligence_bdd.py` (FakeRegistry), `test_scope_guard.py` (StubRegistry).
5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?** solo la forma de resolución (por id+contrato vía `IModuleRegistry`); MCP/CKG/Delta/Trace sin cambios de contrato. El cambio concreto de `handle_task` quedó revertido en P1.

## 8. Estado de implementación

- **Base existente (buena):** `IModuleRegistry`/`InMemoryModuleRegistry`, `AppContainer` como root,
  puertos `ICpgAnalysisPort`/`ICkgServicePort`/`TaskRegistry`/`ContextProviderRegistry`/`DeltaRepository`,
  resolver híbrido `resolve_cpg_port`. Ciclo `cli↔mcp` ya roto.
- **Pendiente / a corregir:** (a) `get_service` no valida contrato → P1; (b) ya revertido el cambio
  concreto de `handle_task`; (c) mover adapters a `infrastructure/`; (d) CI gates (P0).
- **P2–P4:** pendientes.
