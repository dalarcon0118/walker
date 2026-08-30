# Estrategia domain-first (OBLIGATORIA) — Walker

- **Status:** Activa / Obligatoria para toda contribución
- **Date:** 2026-08-26
- **Autor:** Walker agents (con aprobación del usuario)
- **Complementa:** ADR-WALKER-062 (mantenibilidad monolith puertos DI CI), ADR-WALKER-045 (composition root obligatorio), ADR-WALKER-052 (DAG gate boot y ciclos feature), ADR-WALKER-056 (engine migration shared/core), ADR-WALKER-058 (eliminación anti-patrones engine), ADR-WALKER-060 (nestjs-inspired design), ADR-WALKER-061 (storage como puerto)
- **Hipótesis:** El modelo de dominio es la única fuente de verdad (SSOT). Si el dominio se define primero y la infraestructura es el último en aparecer, las reglas de negocio no se dispersan, los adaptadores son intercambiables y el costo de mantenimiento baja independientemente del tamaño del monolito.

---

## 1. Problema / Motivación

Walker creció impulsado por los adaptadores (CLI, MCP, tareas). Eso produjo
reglas de negocio duplicadas en la frontera, dependencias cíclicas y tipos de
dominio que viven en la infraestructura. La estrategia domain-first invierte el
orden: **el dominio viene primero; la infraestructura es lo último en aparecer**.

No es una moda: es el ciclo clásico de ingeniería de software (Análisis →
Diseño → Implementación) con el dominio como eje en lugar de la interfaz o la
base de datos. En la práctica moderna se concretiza como **Domain-Driven Design
(DDD)** para el modelado, montado sobre ese ciclo.

### 1.1 Anti-patrones identificados

| # | Anti-patrón | Dónde aparece | Consecuencia |
|---|---|---|---|
| A1 | Diseño dirigido por infraestructura | Empezar por el handler MCP/CLI | Reglas dispersas en 3 adaptadores; bug de transición en N lugares |
| A2 | Dominio anémico | Tipos planos sin invariantes | La lógica de negocio termina en services de infraestructura |
| A3 | IO dentro del dominio | `shared/core/engine/domain` toca fs/red | Dominio no testeable en aislamiento; tests flaky |
| A4 | Inyección de servicio concreto | handler importa la clase concreta | No se puede swappear el provider; acoplamiento duro |
| A5 | Capa 5 depende de capa 5 | `features/main` importado desde `shared/core` | Ciclo de dependencias; invariante de capas roto |

---

## 2. Fases 0–5 (domain-first)

| # | Fase | Ciclo clásico | Entrega | Capa en Walker (la dependencia apunta al dominio) |
|---|------|---------------|---------|---------------------------------------------------|
| 0 | **Mapa conceptual** | Análisis del dominio | Entidades + relaciones (Play 1→N Bet, Claim sobre `Bet.payout_status`, ciclo de vida) | `docs/` + grafo conceptual |
| 1 | **Dominio puro SSOT** | Diseño del modelo | Tipos + máquina de estados + invariantes, **sin IO** | `shared/core/engine/domain/` (puertos `ITracer`, `ICpgAnalysisPort`, `ICkgServicePort`, `TaskRegistry(ABC)`) |
| 2 | **Estructuras de datos** | Diseño de datos | DTOs backend + cliente derivados del dominio | `shared/core/engine/application/` (modelos), `libs/context/`, `libs/cognitive_graph/models.py` |
| 3 | **Reglas puras** | Diseño (lógica) | `canTransition`, ventanas, validaciones — funciones sin IO que devuelven `Result[T, E]` | `shared/core/engine/domain/` + `application/` (funciones puras con `returns`) |
| 4 | **Casos de uso / aplicación** | Implementación (orquestación) | Use-cases que orquestan el dominio a través de **puertos**; DI de los puertos, sin infra concreta | `features/main/engine/application/` (`OrchestrationService`, `tool_catalog`), `app/composition/` (`AppContainer`, registry) |
| 5 | **Infraestructura / adaptadores** | Implementación (concretos, **último**) | Adaptadores concretos: DB, MCP server, CLI, UI/HTTP — implementan los puertos de la fase 1 | `app/` (`bootstrap`, `container`), `features/main/mcp_adapter.py`, `features/main/cli_interface/`, `bin/walker-mcp`, `libs/router/`, `libs/cognitive_graph/` (backends) |

**Regla de oro:** las fases 4–5 **dependen de** 0–3, nunca al revés. El dominio
no sabe de MCP ni de CLI.

---

## 3. Regla de dependencias

```
features/main  (fase 5: adaptadores)  ─┐
features/.../cli_interface (fase 5)    ├─▶  features/engine/application (fase 4)
libs/cognitive_graph (fase 5)         ─┘         │
                                               ▼
                                  shared/core/engine/application (fase 2-3)
                                               │
                                               ▼
                                  shared/core/engine/domain (fase 1: SSOT, sin IO)
```

- `shared/core/engine/` **nunca** importa `features/`. (Invariante de capas, A5.)
- Los adaptadores de fase 5 **consumen puertos**, no reconstruyen el dominio.
- El cableado de fase 5 ocurre en el **composition root único** (ADR-045) y se
  resuelve por **id+contrato** (ADR-062), no por la clase concreta.

---

## 4. Mapeo concreto a Walker

- **Fase 1 (dominio puro):** `shared/core/engine/domain/` — `ports.py`
  (`ITracer`, `ICpgAnalysisPort`, `ICkgServicePort`), `TaskRegistry(ABC)`,
  entidades y máquina de estados. Cero imports de `libs/` o `features/`.
- **Fase 2 (datos):** `shared/core/engine/application/` (modelos de
  orquestación), `libs/context/` (context models), `libs/cognitive_graph/models.py`.
- **Fase 3 (reglas puras):** funciones en `shared/core/engine/domain/` y
  `application/` que no hacen IO y devuelven `Result[T, E]` (`returns`).
- **Fase 4 (aplicación):** `features/main/engine/application/` (`OrchestrationService`,
  `tool_catalog`), `app/composition/` (`AppContainer.build()`,
  `InMemoryModuleRegistry`).
- **Fase 5 (adaptadores):** `app/bootstrap.py`, `app/container.py`,
  `features/main/mcp_adapter.py`, `features/main/cli_interface/`, `bin/walker-mcp`, `libs/router/`,
  `libs/cognitive_graph/file_backend.py` (implementación concreta del puerto
  `StorageBackend`).

### 4.1 Ejemplo: F3 (TraceLogger) y la regla de fase 5

`TraceLogger` (fase 1, implementa `ITracer`) necesita `path_resolver` y
`cognitive_graph_factory`. El adaptador MCP (`mcp_adapter.py`, fase 5) **no debe**
resolverlos por su cuenta: el composition root los inyecta al construir el
`McpServer`. Así el adaptador consume el puerto ya cableado y el TraceGraph
recibe eventos también por MCP. (Ver `features/main/mcp_adapter.py:_build_server`.)

---

## 5. Relación con ADR-062 (Hexagonal + service locator por id+contrato)

Domain-first fases 0–3 son el núcleo; la fase 4 expone los casos de uso tras
**puertos**; la fase 5 los implementa como **adaptadores**. ADR-062 operacionaliza
esto:

- **Puertos en fase 1**, adaptadores en fase 5 (Hexagonal).
- El locator de servicios (`IModuleRegistry.get_service(id)`) resuelve por
  **id + contrato**, no por la clase concreta: es el wiring de fase 5 sin
  acoplar al implementador (idiom `ModuleRef.get(id)` de NestJS). El consumidor
  acopla al *services manager* + *contrato*, no al servicio concreto.
- DI cuando es factible = inyectar el **puerto**, no el objeto concreto.
- El locator no se elimina: se usa con sabiduría (resolución por id+contrato en
  el composition root, validación de contrato en el locator).

---

## 6. `failure_scenarios`

> Convención AGENTS.md: cada escenario incluye fallo / causa raíz / mitigación /
> detección.

**FS-1 — Saltar la fase 1 y empezar por infraestructura**
- **fallo:** reglas de negocio duplicadas en 3 adaptadores; un bug de transición
  aparece en N lugares.
- **causa raíz:** diseño dirigido por infraestructura en vez de por el modelo (A1).
- **mitigación:** todo tipo/invariante/máquina de estados vive en
  `shared/core/engine/domain`; los adaptadores solo lo consumen.
- **detección:** test arquitectónico que falla si una regla de dominio aparece en
  `features/main` o `cli_interface`.

**FS-2 — IO dentro del dominio (fase 1/3)**
- **fallo:** el dominio no es testeable en aislamiento; tests lentos y flaky.
- **causa raíz:** funciones puras tocan `CognitiveGraph`/fs/red directamente (A3).
- **mitigación:** IO solo detrás de puertos (`ICpgAnalysisPort`, `ITracer`,
  `StorageBackend`); el dominio recibe puertos.
- **detección:** grep en `shared/core/engine/domain` por imports de
  `libs/cognitive_graph`, `open(`, `requests`, `http`.

**FS-3 — Adaptador (fase 5) resuelve dependencia concreta en vez de id+contrato**
- **fallo:** acoplamiento a la implementación; no se puede swappear el provider.
- **causa raíz:** inyección del objeto concreto en vez de
  `module_registry.get_service(id)` (A4).
- **mitigación:** ADR-062 — resolver por id+contrato; validar contrato en el locator.
- **detección:** test que verifica que handlers/router usan `get_service(id)` y no
  importan la clase concreta.

**FS-4 — Capas 4–5 dependen de infra concreta (ciclo de dependencias)**
- **fallo:** ciclo `features/main → shared/core`; invariante de capas roto (A5).
- **causa raíz:** un use-case importa un adaptador concreto.
- **mitigación:** regla de dependencia (flechas hacia el dominio) + gate DAG.
- **detección:** gate DAG en CI (ADR-052) + test que prohíbe imports de
  `features/` dentro de `shared/core/engine`.

**FS-5 — Composition root no inyecta dependencias (caso F3: TraceLogger sin path_resolver)**
- **fallo:** el adaptador MCP no persiste trazas; TraceGraph incompleto o warning
  `path_resolver no inyectado`.
- **causa raíz:** el root construye el server con `TraceLogger()` sin inyectar
  `path_resolver`/`cognitive_graph_factory`.
- **mitigación:** composition root único (ADR-045) inyecta todas las dependencias
  del adaptador al construirlo.
- **detección:** test E2E que invoca una tool por MCP y verifica eventos en
  TraceGraph (sin warning `path_resolver no inyectado`).

---

## 7. Verificación / Enforcement

1. **Regla de no-import:** `shared/core/engine/` no importa `features/`. Test en CI.
2. **Gate DAG (ADR-052):** detecta ciclos de dependencia en el boot de features.
3. **Tests de completitud de registries (patrón AGENTS.md):** `{cpg, ckg, delta,
   trace, state, project, task}` presentes; aplica a `build_context_registry`,
   `bootstrap_tasks`, `bootstrap_modules`, `SideEffectEngine.register`.
4. **Test arquitectónico domain-first:** falla si una regla de dominio o IO aparece
   fuera de su fase (FS-1, FS-2, FS-4).
5. **Checklist de segundo orden (AGENTS.md):** obligatorio antes de mergear — qué
   importa esto, qué side effects dispara, qué eventos publica, qué tests dependen,
   qué cambia en MCP/CLI/CKG/Delta/Trace.

---

## 8. Checklist de segundo orden (referencia)

Antes de mergear cualquier cambio, responder (de AGENTS.md):
1. ¿Qué módulos importan este?
2. ¿Qué side effects dispara?
3. ¿Qué eventos de dominio publica?
4. ¿Qué tests dependen de este comportamiento?
5. ¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?

Si la respuesta a alguna es "no sé", detenerse y documentar.
