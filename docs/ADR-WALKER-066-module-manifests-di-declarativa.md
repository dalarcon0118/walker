# ADR-WALKER-066: Module manifests (`.module.py`) como sistema de módulos tipo NestJS para DI declarativa

- **Status:** Aprobado
- **Aprobado:** 2026-08-28 (ritual de revisión: `failure_scenarios` FS-1..FS-5, métricas por fase M-01..M-06 y checklist de segundo orden presentes; subsistema reubicado a `shared/libs/module_system/`)
- **Fecha:** 2026-08-27
- **Autor:** Walker (agente) + PO
- **Complementa:** ADR-WALKER-065 (las 5 capas: motor/shared-libs/tasks/features/protocols), ADR-WALKER-062 (resolve_locator por id+contrato), ADR-WALKER-045 (composition root único), ADR-WALKER-051 (gaps de responsabilidades G1/G11), ADR-WALKER-060 (NestJS-inspired)
- **Amplía:** el ADR-065 en el mecanismo de cableado. El 065 define *dónde* viven las cosas; este ADR define *cómo* se declaran y enganchan para mejorar la inyección de dependencias.

---

## 1. Problema

Walker **ya tiene** los ladrillos de un sistema de módulos, pero están sub-usados y la DI real sigue cableada a mano:

- `shared/libs/module_system/contracts.py` define **`IModule`** (`name`, `tools()`, `routes()`, `metadata()`) y **`IModuleRegistry`** con `register_service`/`get_service` (container de DI por clave).
- `shared/libs/module_system/registry.py` tiene **`InMemoryModuleRegistry`** (módulos + servicios lazy).
- **Reubicado fuera de `shared/core/engine/`** (refinamiento de esta ADR): el sistema de módulos es infra de composición genérica, NO dominio de workflow. El motor queda como dominio puro (`IWorkflowEngine`/`ITaskDispatcher`, state machine, gates, `TaskRegistry`). `shared/core/engine/__init__.py` mantiene un re-export shim por compatibilidad.
- `shared/core/engine/bootstrap.py:50` tiene **`resolve_locator`** (ADR-062: resolución por id+contrato).
- `features/main/task_execution/bootstrap.py:14` tiene **`register_all`** (hook de registro de tasks, hoy es un `pass`).

**El gap:** `IModule` solo declara la superficie de protocolo (`tools`/`routes`). Todo lo demás —bindings de puertos, providers, imports entre capas— se hace **imperativamente** en `app/composition/registry.py` y `app/engine_ports.py`. Consecuencias observables:

1. **`app/engine_ports.py`** es pegamento manual: 4 adaptadores (`ProjectContextAdapter`, `ConflictResolverAdapter`, `SprintServiceAdapter`, `PromptCollectorAdapter`) que podrían declararse donde vive el feature.
2. **`_build_workflow_facade` duplicado** (docstring idéntico) en `app/bootstrap.py:320` y `app/composition/registry.py:576` — deuda de wiring centralizado.
3. **Sin acyclicidad automática:** el grafo de dependencias entre capas solo se chequea post-hoc con `test_import_acyclicity.py`; un ciclo no falla en arranque.
4. **`side_effects.py:526`** despacha handlers por ruta hardcodeada `features/main/task_execution/side_effects/` en vez de resolverlos vía el registro.

En NestJS cada módulo tiene un `.module.ts` que declara `imports/providers/controllers/exports` y el framework arma el grafo solo. Walker necesita el equivalente declarativo: **un `module.py` por path**.

---

## 2. Decisión

**Promover `IModule` a un `ModuleManifest` (dataclass) estilo NestJS que cada path de las 5 capas (ADR-065) declare en su `module.py`. El composition root se vuelve un agregador delgado: importa los manifests, ordena topológicamente por `imports`, aplica los `adapters` en `resolve_locator` + `register_service`, instancia `providers`, y dispara los hooks `register()`.**

### D1 — Contrato `ModuleManifest` (evolución de `IModule`)

```python
# shared/libs/module_system/contracts.py
@dataclass
class ModuleManifest:           # implementa IModule (tools/routes/metadata siguen existiendo)
    name: str
    imports:  list[str] = field(default_factory=list)   # módulos de los que depende → topo-sort + ciclos
    providers: list[type] = field(default_factory=list)  # clases inyectables a instanciar (singletons)
    ports:    list[type] = field(default_factory=list)   # interfaces que este módulo expone
    adapters: list[tuple[type, type]] = field(default_factory=list)  # (Port, Impl) → resolve_locator
    exports:  list[str] = field(default_factory=list)    # claves de servicio expuestas a otros módulos
    tools:    list[ToolDefinition] = field(default_factory=list)
    routes:   list[RestRoute] = field(default_factory=list)
    register: Callable | None = None                     # hook (p.ej. tasks.register_all)

    # IModule sigue satisfecho:
    def tools(self) -> list[ToolDefinition]: return self.tools
    def routes(self) -> list[RestRoute]:   return self.routes
    def metadata(self) -> dict:            return {"imports": self.imports, "exports": self.exports}
```

`InMemoryModuleRegistry.register_manifest(m)` (en `shared/libs/module_system/registry.py`) pasa a: validar `imports` (sin ciclos, FS-3), bindear `adapters` en `resolve_locator` y `register_service`, instanciar `providers` como singletons bajo las `exports`, y llamar `register()` si existe.

### D2 — Un `module.py` por capa (las 5 de ADR-065)

```python
# shared/libs/module.py  (módulo único "shared" — umbrella de librerías genéricas)
MODULE = ModuleManifest(
    name="shared", imports=[],
    providers=[CognitiveGraph, ContextResolver, ProjectionStore, PromptCollector],
    adapters=[(IPromptCollector, PromptCollector)],
    exports=["cognitive_graph", "context_resolver", "projection_store", "prompt_collector"],
)

# features/code_analysis/module.py
MODULE = ModuleManifest(name="code_analysis", imports=["shared"],
                        adapters=[(ICpgAnalysisPort, WalkerCpgAnalysisAdapter)],
                        exports=["cpg_analysis_service"])

# tasks/module.py
MODULE = ModuleManifest(name="tasks",
                        imports=["features.code_analysis", "features.delta_tracking", "shared"],
                        register=register_all, exports=["task_registry"])

# protocols/mcp/module.py
MODULE = ModuleManifest(name="mcp", imports=["shared.core.engine", "tasks"],
                        tools=derive_from(tool_catalog))   # mantiene contrato IModule actual

# app/module.py  (composition root declarative)
MODULE = ModuleManifest(name="app", imports=["tasks", "features.*", "protocols.*", "shared"],
                        providers=[WorkflowFacade], exports=["workflow_facade"])
```

### D3 — `register_module` aplica DI (no cableado manual)

El composition root (`app/composition/registry.py`) se reduce a:

```python
from shared.libs.module_system.registry import InMemoryModuleRegistry
from shared.libs.module import MODULE as m_shared
from features.code_analysis.module import MODULE as m_ca
from tasks.module import MODULE as m_tasks
from protocols.mcp.module import MODULE as m_mcp
from app.module import MODULE as m_app

reg = InMemoryModuleRegistry()
for m in [m_shared, m_ca, m_tasks, m_mcp, m_app]:
    reg.register_manifest(m)   # topo-sort + adapters → resolve_locator + register_service + register()
```

### D4 — `app/engine_ports.py` desaparece

Los 4 adaptadores pasan a `adapters=[(Port, Impl)]` en el `module.py` del feature correspondiente. El pegamento manual muere (cierra ADR-065 D3).

### D5 — El `_build_workflow_facade` duplicado muere

`WorkflowFacade` se vuelve `provider`/`export` del módulo `app` (D2, último bloque). Se instancia una sola vez y se exporta como `"workflow_facade"`; quien lo necesite lo resuelve por `get_service("workflow_facade")`. Fin al duplicado `bootstrap.py:320` vs `registry.py:576`.

### D6 — `side_effects` resuelve vía manifest

`shared/core/engine/infrastructure/side_effects.py:526` deja de hardcodear `features/main/task_execution/side_effects/` y resuelve los handlers por nombre desde el `TaskRegistry` (ya poblado por el hook `register()` del módulo `tasks`).

### D7 — Acyclicidad en tiempo de registro

El grafo de `imports` se valida al llamar `register_manifest`: un ciclo levanta excepción en arranque. El `test_import_acyclicity.py` queda como guarda extra, pero deja de ser la única defensa (cierra ADR-065 FS-3).

---

## 3. Alternativas evaluadas

| Alternativa | Pros | Contras | Decisión |
|---|---|---|---|
| **A. Mantener wiring imperativo en `app/composition/registry.py`** | Sin cambiar contratos | Repite el error de ADR-051 (estructural no de responsabilidades); `_build_workflow_facade` duplicado persiste | Rechazada |
| **B. Framework DI de 3rd party (pinject/dependency-injector)** | DI completa automática | ADR-060 ya rechazó framework dogma; añade dependencia y magia de reflection en Python | Rechazada |
| **C. Decoradores `@module`/`@injectable`** | Ergonomía tipo NestJS | Requiere scanner de clases + metadatos; más complejo que un dataclass declarativo | Rechazada (usamos dataclass) |
| **D. `ModuleManifest` dataclass + `module.py` por path** (elegida) | Reusa `IModule`/`InMemoryModuleRegistry`/`resolve_locator` ya existentes; declarativo, sin framework; acyclicidad automática | Migrar cada path a su manifest | **Aceptada** |

---

## 4. failure_scenarios

**FS-1 — Adapter duplicado sobre el mismo puerto**
- **fallo:** dos módulos declaran `adapters=[(ICpgAnalysisPort, X)]` y `register_module` sobre-escribe el binding silenciosamente.
- **causa raíz:** `register_manifest` no detecta colisión de `(Port,)`.
- **mitigación:** `register_module` levanta si un puerto ya tiene binding; el test de completitud del registry lo detecta.
- **detección:** `grep -rn 'adapters='` por puerto; test que registra dos impls del mismo puerto y espera excepción.

**FS-2 — Ciclo en `imports` no detectado**
- **fallo:** `features.a` importa `features.b` y viceversa; el topo-sort se cuelga o crea wiring circular.
- **causa raíz:** `register_manifest` no valida el grafo de `imports`.
- **mitigación:** validación de ciclos en `register_manifest` (FS-3/ADR-065 D7) antes de bindear.
- **detección:** arranque de `bin/walker` falla con "cycle detected"; `test_import_acyclicity.py` como respaldo.

**FS-3 — Hook `register()` nunca invocado**
- **fallo:** el módulo `tasks` se registra pero `register_all` no se llama → el bus queda vacío → `TaskNotFound`.
- **causa raíz:** el agregador olvida llamar `m.register()` tras `register_module`.
- **mitigación:** `register_manifest` invoca `register()` internamente si está presente; el test de completitud valida `implement`/`verify` registrados.
- **detección:** `test_container_completeness_bdd.py` (EXPECTED-set) + `bin/walker routes`.

**FS-4 — `ModuleManifest` no satisface `IModule`**
- **fallo:** código existente que consume `IModule.tools()` rompe porque el dataclass expone `tools` como atributo, no método.
- **causa raíz:** cambiar `IModule` de métodos a dataclass rompe implementaciones previas (`ModuleDefinition`).
- **mitigación:** `ModuleManifest` implementa `tools()`/`routes()`/`metadata()` como métodos que devuelven los atributos; `ModuleDefinition` se deprecia a favor de `ModuleManifest`.
- **detección:** `tests/test_module_registry_di_bdd.py` sigue pasando tras el cambio.

**FS-5 — `side_effects` resuelve handler inexistente tras mover task**
- **fallo:** tras mover `task_execution`→`tasks/`, `side_effects.py:526` busca en ruta vieja y falla.
- **causa raíz:** el dispatcher sigue hardcodeando la ruta en vez de usar el `TaskRegistry` poblado por el hook `register()`.
- **mitigación:** D6 — `side_effects` resuelve por nombre desde el registry; el manifest de `tasks` registra los handlers en el mismo registry.
- **detección:** `python -m protocols.cli.cli_interface.mcp --self-test` (31 tools) + smoke de side-effect.

---

## 5. Checklist de segundo orden

| Pregunta | Respuesta |
|---|---|
| ¿Qué módulos importan esto? | `app/composition/registry.py` (agregador), `shared/libs/module_system/registry.py`, todos los `module.py` de las 5 capas, `resolve_locator` |
| ¿Qué side effects dispara? | Migrar `IModule`→`ModuleManifest`; borrar `app/engine_ports.py`; unificar `_build_workflow_facade`; cambiar `side_effects.py:526` a resolución por registry |
| ¿Qué eventos de dominio publica? | Ninguno: es mecánica de cableado; el bus (`TaskRegistry`) ya es la fuente |
| ¿Qué tests dependen? | `tests/test_module_registry_di_bdd.py`, `test_import_acyclicity.py`, `test_container_completeness_bdd.py`, tests de tasks/MCP |
| ¿Qué cambia en MCP, CLI, CKG, Delta, Trace? | MCP/CLI: sin cambio funcional (mismo bus); CKG/Delta/Trace: sin cambio (resuelven por `resolve_locator`); cambian rutas de cableado y desaparece el glue manual |

---

## 6. Migración

| Fase | Acción | Esfuerzo |
|---|---|---|
| **F1** | Evolucionar `IModule`→`ModuleManifest` en `shared/libs/module_system/contracts.py`; `InMemoryModuleRegistry.register_manifest` aplica adapters/providers/`register()` + validación de ciclos | 2h |
| **F2** | **HECHO (2026-08-28):** `shared/libs/module.py` + `features/{code_analysis,delta_tracking,cognitive_knowledge,event_tracing,project}/module.py` + `features/main/prompts/module.py` creados con `adapters`/`exports`. Falta `features/engine` (opcional) | 3h |
| **F3** | **HECHO (2026-08-28):** `tasks/module.py` (`register=register_all`) y `protocols/mcp/module.py` (provider `McpServer`) creados. Nota: `tools=` no implementado aún (F5 define cómo derivar tools del catálogo) | 1h |
| **F4** | **D4 HECHO (2026-08-28):** `app/engine_ports.py` borrado; 4 adaptadores movidos a `features/{project,delta_tracking}/module.py` + `features/main/prompts/module.py` con `adapters=[(Port,Impl)]`. **D5 pendiente:** `WorkflowFacade`→`provider`/`export` de `app/module.py` (F5) | 1h |
| **F5** | **HECHO (2026-08-28):** `compose_app` registra todos los `module.py` vía `register_manifest` y resuelve los 4 puertos del engine con `resolve_port()` (DI por id+contrato). `bootstrap.py` ya no instancia adaptadores manualmente. `side_effects.py` ya resuelve handlers vía ActionCatalog (D6) | 2h |

---

### Métricas de calidad por fase (obligatorio, AGENTS.md)

Cada fase declara ≥3 métricas aplicables (M-01..M-06). Las no listadas por una fase se consideran
**no aplicables a esa fase** (reasignadas a la fase que sí las toca); no se omiten en silencio.
Formato: `- [ ] M-0X <criterio medible para esta fase> — anti-patrón a evitar: <...>`.

**F1 — `ModuleManifest` + `register_manifest` (contratos/registry en `shared/libs/module_system/`)**
- [ ] M-01 (escritura): `ModuleManifest` es dataclass puro sin efectos en `__init__`; `register_manifest` transaccional (valida ciclos antes de bindear) — anti-patrón a evitar: Hidden Dependency (binding parcial tras fallo).
- [ ] M-03 (dominio): `ModuleManifest` satisface `IModule` (tools/routes/metadata) sin romper implementaciones previas (`ModuleDefinition`) — anti-patrón a evitar: Anemic Domain Model.
- [ ] M-02 (estrategias): SSOT del manifest por módulo; `resolve_locator` es la única forma de bindear `adapters` — anti-patrón a evitar: Shotgun Surgery (wiring disperso).

**F2 — `shared/libs/module.py` + `features/*/module.py` (`adapters`/`exports`)**
- [ ] M-02 (estrategias): declaración de `adapters`/`exports` por módulo, sin cableado manual en `app/composition/registry.py` — anti-patrón a evitar: Duplicate Code.
- [ ] M-03 (dominio): cada feature expone una responsabilidad vía `exports` — anti-patrón a evitar: God Class.
- [ ] M-06 (configuración): módulo `shared` (umbrella) resoluble por id sin importar implementaciones concretas — anti-patrón a evitar: Accidental Complexity.

**F3 — `tasks/module.py` (`register=register_all`) + `protocols/mcp/module.py`**
- [ ] M-02 (estrategias): el hook `register()` del módulo `tasks` es el único caller de `register_all` — anti-patrón a evitar: Shotgun Surgery.
- [ ] M-04 (eventos): `tasks` registra handlers en el bus (`TaskRegistry`) y `side_effects` los resuelve por nombre, desacoplando dispatcher de ruta — anti-patrón a evitar: Distributed Big Ball of Mud.
- [ ] M-06 (configuración): `protocols/mcp/module.py` deriva `tools` del `tool_catalog` (vista), sin redefinir contratos — anti-patrón a evitar: Not Invented Here.

**F4 — borrar `app/engine_ports.py` + `WorkflowFacade`→provider/export**
- [ ] M-05 (concurrencia): `WorkflowFacade` instanciada una sola vez y exportada como `"workflow_facade"`; se resuelve por `get_service` — anti-patrón a evitar: Magic Numbers (factory duplicada bootstrap.py vs registry.py).
- [ ] M-03 (dominio): los 4 adaptadores viven en el `module.py` del feature dueño del puerto — anti-patrón a evitar: Anemic Domain Model.
- [ ] M-02 (estrategias): muerte de `app/engine_ports.py` como pegamento manual — anti-patrón a evitar: Shotgun Surgery.

**F5 — registry agregador delgado + `side_effects` por registry**
- [ ] M-04 (eventos): `side_effects.py` resuelve handlers por nombre desde el `TaskRegistry` poblado por el manifest — anti-patrón a evitar: Eventual Consistency sin safeguards (ruta hardcodeada stale).
- [ ] M-05 (concurrencia): validación de acyclicidad en `register_manifest` (falla en arranque) — anti-patrón a evitar: Magic Numbers.
- [ ] M-06 (configuración): tests de completitud + acyclicidad automatizados; sin chequeo manual — anti-patrón a evitar: Accidental Complexity.

## 7. Verificación

| Check | Resultado esperado |
|---|---|
| `tests/test_module_registry_di_bdd.py` | pasa (IModule sigue satisfecho) |
| `bin/walker routes` | 23 endpoints, exit 0 |
| `python -m protocols.cli.cli_interface.mcp --self-test` | 31 tools, exit 0 |
| `test_import_acyclicity.py` | 0 ciclos; `imports` del manifest coincide con el grafo |
| Registro de 2 impls del mismo puerto | excepción en `register_module` (FS-1) |
| Ciclo en `imports` | excepción en arranque (FS-2) |
| `_build_workflow_facade` | 1 definición (en `app/composition/registry.py`, unificada en F3; `app/module.py` pendiente de D5/F5) |

---

## 8. Addendum — Ejecución 2026-08-28 (D4/F2/F3/F4/F5 ejecutados)

**D4 ejecutado:** `app/engine_ports.py` eliminado. Sus 4 adaptadores se movieron al `module.py` del feature dueño del puerto:
- `ProjectContextAdapter` → `features/project/module.py`
- `ConflictResolverAdapter` → `features/delta_tracking/module.py`
- `SprintServiceAdapter` → `features/delta_tracking/module.py`
- `PromptCollectorAdapter` → `features/main/prompts/module.py`

Cada `module.py` declara `adapters=[(Port, Impl)]` en su `ModuleManifest`. `app/bootstrap.py` ahora importa los adaptadores desde esos módulos (el composition root sigue instanciándolos). Validado: `AppContainer.build()` OK, `bin/walker routes` OK, MCP self-test 31 tools.

**Mecanismo activado (F5, 2026-08-28):** `register_manifest` se invoca en runtime desde `compose_app` (`_register_module_manifests`). Los `adapters` declarados en cada `module.py` ya NO son inerte: `resolve_port(port)` instancia el adapter inyectándole sus dependencias por nombre desde el registry. `bootstrap.py` resuelve los 4 puertos del engine vía `module_registry.resolve_port(...)` en lugar de instanciar manualmente.

**D5 ejecutado (2026-08-28):** `app/module.py` declara `WorkflowFacade` como `provider`/`export`. El facade se construye en `compose_app` vía `_build_workflow_facade(orch)` y queda expuesto como servicio DI `workflow` (equivalente a resolverlo por registry).

**F2/F3 ejecutados (2026-08-28):** creados `tasks/module.py`, `protocols/mcp/module.py`, `features/cognitive_knowledge/module.py`, `features/event_tracing/module.py`, `app/module.py`. Todos validados (compile + import) y ahora **activos** en runtime vía F5.