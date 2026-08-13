# ADR-WALKER-046: `AppContainer` — Ciclo de Vida Explícito del Composition Root

> **Status:** Aprobada
> **Fecha:** 2026-08-13
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-045 (composition root obligatorio), ADR-WALKER-016 (DIP), `capa_rout.md` (D1: 1 composition root), ADR-WALKER-036 (delta_tracking SSOT)

---

## 1. Problema

ADR-WALKER-045 creó **un** composition root (`features/main.bootstrap.compose_app()`) — el objetivo D1 de `capa_rout.md` se cumplió en forma, pero no en sustancia. La composición sigue siendo llamada **desde la capa de aplicación, por request**, y su ciclo de vida es gestionado por **dos globals desincronizados**.

### 1.1 Síntomas observados

| Síntoma | Ubicación | Impacto |
|---|---|---|
| `compose_app()` llamado en 13 call sites de la capa de aplicación | `handlers.py:520/612/689/1131`, `project_use_cases.py:495`, `prompts/collector.py:56`, `mcp_adapter.py:58/222`, `bootstrap.py:62`, `protocol_builder.py:41` | Service Locator: la capa de aplicación busca sus dependencias en vez de recibirlas inyectadas |
| Dos estados globales de composición | `_COMPOSITION_SINGLETON` (features/main) + `_SERVICE_LOCATOR` (core/engine) | El reloader invalida **solo** el locator → el singleton retorna el registry viejo → el código recargado no se ve |
| El cold start del boot cae **dentro** del request | `compose_app()` construye: pkgutil discovery + `importlib.import_module` de tasks/side effects/providers/MCP modules + registries + service locator | Timeout MCP -32001: el cliente corta a los 30s mientras el request paga el boot completo |
| Registro no idempotente | `_discover_and_register_providers` re-registra en cada recomposición | Warnings constantes `Provider 'diff' already registered — overwriting`, `Provider 'test_results' already registered — overwriting` |
| `importlib.reload` muta `sys.modules` en el camino del request | `mcp_adapter.py:serve_stdio` → `_reload_features_core` | Estado a medias entre módulos recargados y otros viejos; races con el registro |

### 1.2 Historia de fallos

El bug del timeout del MCP se ha repetido **tres veces**: 6-ago, 8-ago y 12-ago del 2026. La causa raíz compartida: la invalidación del singleton (test, server stale, o reconstrucción) hace que el siguiente `tools/call` pague el arranque completo **dentro** del camino del request. Cada vez se parcheó el síntoma (chain_dir, reloader, guard de huérfanos); el **patrón estructural** — composición llamada desde la aplicación con ciclo de vida global desincronizado — permanece.

---

## 2. Decisión

**Introducir un `AppContainer` con ciclo de vida explícito, compuesto UNA sola vez en el entry point (CLI/MCP), inyectado a la capa de aplicación, con recarga por build diferido entre requests y registro declarativo idempotente.**

Es el patrón canónico de las arquitecturas empresariales (Composition Root de Seemann): *el request solo resuelve; nunca construye infraestructura*.

Decisiones de diseño tomadas en revisión (2026-08-13):
1. **Un solo tipo:** `AppContainer` es `AppComposition` evolucionada (métodos + ciclo de vida), no un wrapper. No coexisten dos tipos de composición.
2. **`WalkerApp` se conserva** como facade estable de la API pública; solo cambia *quién lo construye* (el entry point, una vez).
3. **Warm-swap = build diferido entre requests** (sincrónico, secuencial, sin hilos). El reloader marca `dirty`; el rebuild ocurre entre requests con el throttle de 1s existente como amortiguador.

### 2.1 `AppContainer` (nuevo, en `features/main`)

Evoluciona `AppComposition` (dataclass pasiva) + absorbe `_COMPOSITION_SINGLETON` (global) como **única** fuente de la composición — un solo tipo:

```python
# features/main/container.py (nuevo)
class AppContainer:
    """Composition root con ciclo de vida explícito (evolución de AppComposition).

    - Se construye UNA vez por proceso (entry point) o en un build diferido.
    - resolve(name): SOLO lectura, devuelve instancias ya construidas en build().
    - child(chain_dir): scope de un request (ctx, chain_dir, Request).
    """

    # ── Infraestructura (singletons del proceso, construidos en build()) ──
    workflow: OrchestrationService
    task_registry: InMemoryTaskRegistry
    side_effect_engine: SideEffectEngine
    module_registry: Any = None

    # ── DI por clave lógica (reemplaza get_module_service) ──
    def resolve(self, name: str) -> Any:
        """SOLO lectura: _services ya contiene las instancias de build().
        Se prohíben factories lazy aquí (la construcción es exclusiva de
        build()) — el contrato 'resolve no construye' es literal."""
        try:
            return self._services[name]
        except KeyError:
            raise KeyError(f"Servicio no registrado: {name}") from None

    # ── Scope de request (contexto efímero) ──
    def child(self, chain_dir: str = "", task_id: str = "") -> "RequestScope":
        return RequestScope(ctx=..., chain_dir=chain_dir, task_id=task_id)

    @classmethod
    def build(cls, chain_dir: str = "") -> "AppContainer":
        """ÚNICA construcción del grafo (cold start, fuera del request)."""
        # … el cuerpo actual de compose_app(), más los servicios DI
        # consolidados en un solo dict _services …
        ...
```

**Reglas del container:**
1. `AppContainer.build()` es el **único** constructor del grafo. `compose_app()` deja de existir como API pública y se convierte en su implementación interna.
2. `resolve()` no crea nada: `_services` contiene **solo instancias** construidas en `build()`. No hay factories lazy.
3. `child()` es el único objeto efímero por request; los singletons del container no se tocan.

### 2.2 Composición en el entry point (una sola vez)

```python
# features/main/cli_interface/mcp.py — servidor MCP
def main() -> int:
    if "--self-test" in sys.argv:
        return _self_test()
    _kill_previous_instances()
    container = AppContainer.build()          # ← UNA VEZ, al boot
    return serve_stdio(container=container)   # ← se inyecta
```

```python
# features/main/cli_interface/__init__.py — CLI
def main(args: list[str] | None = None) -> int:
    container = AppContainer.build()          # ← UNA VEZ, al boot del CLI
    ...
```

**Regla:** ningún handler, use case, collector o adapter llama `AppContainer.build()` o `compose_app()`. Reciben el **`WalkerApp`** (facade estable que se conserva) construido una sola vez por el entry point:

```python
# features/main/front_controller.py — FUTURO
class FrontController:
    def __init__(self, app: WalkerApp) -> None:
        self._app = app   # WalkerApp(container) — construido UNA vez en el entry point
        ...

    def status(self, project_id, repo_path="", sprint_id="") -> dict:
        return self._app.status(project_id, repo_path, sprint_id)
```

El entry point se convierte en el ÚNICO lugar que hace `WalkerApp(AppContainer.build())`. Los handlers dejan de llamar `WalkerApp(compose_app())` (hoy lo hacen 4 veces).

### 2.3 Recarga por build diferido (reemplaza `importlib.reload` en el request)

Sin hilos: el reloader **marca `dirty`** cuando detecta cambios (throttle de 1s que ya existe) y el container nuevo se construye **entre requests**, secuencialmente:

```python
# features/main/cli_interface/infrastructure/reloader.py — FUTURO
# 1. El throttle detecta cambio (1 scan/segundo, ya existe) → marca dirty=True.
# 2. Entre requests: si dirty y no hay build en curso:
#      nuevo = AppContainer.build()     # sincrónico, fuera del loop de parseo
#      if build OK: swap atómico _CONTAINER = nuevo
#      else: se conserva el actual y se loggea (nunca degrade)
# 3. El request siguiente ya usa el container nuevo.
```

**Reglas:**
- `sys.modules` y el discovery NO se tocan desde el camino del request. El único que importa módulos es `AppContainer.build()`, entre requests.
- Swap atómico solo si `build()` es `Success`; en fallo, se conserva el container actual.
- Sin hilos → sin races de import ni de swap; el cold start queda amortizado por el throttle (máximo 1 rebuild por segundo).

### 2.4 Registro declarativo idempotente

Los cuatro `_discover_and_register_*` existentes se consolidan en un único `register_all(container)` **puro**: cada módulo declara qué aporta (`register_module(registry)`, `register_*_tasks`, `register_*_side_effects`, `register_providers` — patrones que YA existen), y el registro es aditivo sin side effects.

- Los warnings `Provider 'diff' already registered — overwriting` desaparecen: el registro conoce el conjunto esperado y verifica duplicados **antes** de registrar (valida contra el test de completitud, no los pisa).
- El test de completitud de `build_context_registry()` (patrón AGENTS.md) pasa a verificar **el container completo**: `EXPECTED = {"workflow", "task_registry", "side_effect_engine", "cpg", "ckg", "delta", "trace", "state", "project", "task", ...}`.

### 2.5 Unificación de los dos globals

`_SERVICE_LOCATOR` (core/engine) es una ventana al container: `get_module_service(name)` delega en `get_container().resolve(name)` — el resolver del container se registra UNA vez en `build()` y el reloader **ya no lo invalida por separado**. El ciclo de vida del servicio queda en manos de UNA estructura: `AppContainer`.

---

## 3. Consecuencias

### 3.1 Positivas

- **El request nunca paga el boot.** `AppContainer.build()` es cold start aislado (boot del entry point o build diferido entre requests). Elimina la causa estructural del timeout MCP -32001 (historia de 3 fallos).
- **Un solo ciclo de vida de composición.** Desaparece la desincronización `_COMPOSITION_SINGLETON` vs `_SERVICE_LOCATOR`.
- **Capa de aplicación limpia.** Handlers/use cases reciben dependencias inyectadas; desaparecen los 13 call sites de `compose_app()`.
- **Recarga fiable.** Warm-swap atómico: el código recargado realmente se ve (hoy el singleton retorna el registry viejo) y sin estado a medias.
- **Registro predecible.** Idempotente, verificable por completitud, sin "already registered".
- **Testeable.** El container se puede construir con fixtures (punta a punta) sin tocar globals; los tests dejan de depender del orden de reset.

### 3.2 Negativas / Trade-offs

- **Refactor amplio:** los 13 call sites de `compose_app()` + `_SERVICE_LOCATOR` + reloader + registro. Debe hacerse por fases (sección 8) para no romper la suite completa de una vez.
- **Dos APIs durante la transición:** `compose_app()` y `AppContainer.build()` coexistirán hasta migrar todos los call sites; riesgo de que un call site viejo reintroduzca un cold start en el request (mitigado en failure_scenarios).
- **Build diferido añade latencia puntual:** el primer request tras un cambio de código paga (o espera) el rebuild del container. Amortiguado por el throttle de 1s y porque es un evento de desarrollo, no de producción.
- **Mover la construcción de `_SERVICE_LOCATOR`:** cambios en `core/engine/bootstrap.py` (que es infraestructura pura) para delegar en el container — cuidado de no acoplar core/engine a features/main (el resolver se inyecta, no se importa).

---

## 4. Arquitectura objetivo

```
                       entry points (proceso)
   CLI main()  ──┐                        ┌── MCP serve_stdio()
                 ▼                        ▼
        ┌────────────────────────────────────────────┐
        │           AppContainer.build()  (UNA VEZ)   │
        │   discovery + registries + services DI      │
        │   (cold start AISLADO del request)          │
        └────────────────────┬───────────────────────┘
                             │ WalkerApp(container)   (facade estable)
                             ▼
        ┌────────────────────────────────────────────┐
        │  FrontController(WalkerApp)                 │
        │  handle_status / submit / transition / ...  │
        └────────────────────┬───────────────────────┘
                             │ por request: child() scope
                             ▼
        Router / RequestScope(ctx, chain_dir, task_id)
        workflow.submit/transition/status/prepare (resuelve, NO construye)

reloader → detecta cambio (throttle) → marca dirty → build diferido entre requests
         → swap atómico _CONTAINER (solo si build OK; en fallo conserva el actual)
```

**Reglas de dependencia (heredadas de ADR-045, sin cambios):**
1. `core/engine/` NO importa `features/`.
2. `features/main` es el único que integra `core/engine` y todos los features.
3. La capa de aplicación (handlers/use cases) NO llama a `build()`/`compose_app()`: recibe `WalkerApp` construido por el entry point.
4. El request solo resuelve (singletons) y crea scopes efímeros (`child()`).

---

## 5. Mapa de archivos afectados

### Nuevos archivos

| Archivo | Responsabilidad |
|---|---|
| `features/main/container.py` | `AppContainer` (fusión de `AppComposition`) + `RequestScope` (ciclo de vida explícito) |
| `features/main/composition.py` (opcional) | `register_all(container)` — consolidación idempotente de los 4 `_discover_and_register_*` |
| `tests/test_container_lifetime_bdd.py` | Verifica: build una vez; resolve no construye; child() es efímero; build diferido conserva el container en fallo |

### Archivos que se modifican

| Archivo | Cambio |
|---|---|
| `features/main/bootstrap.py` | `compose_app()` → implementación interna de `AppContainer.build()`; `AppComposition` se fusiona en `AppContainer` (un solo tipo) |
| `features/main/cli_interface/mcp.py` | `main()` construye `AppContainer.build()` UNA vez, crea `WalkerApp(container)` y lo inyecta a `serve_stdio` |
| `features/main/cli_interface/mcp_adapter.py` | `serve_stdio` recibe `WalkerApp`; elimina `compose_app()` de `_call_tool` y registro de módulos |
| `features/main/cli_interface/__init__.py` | `main()` del CLI construye `WalkerApp(AppContainer.build())` una vez |
| `features/main/cli_interface/application/handlers.py` | Elimina los 4 `WalkerApp(compose_app())`; recibe el `WalkerApp` inyectado |
| `features/main/cli_interface/application/project_use_cases.py` | `compose_app()` → `WalkerApp` inyectado |
| `features/main/prompts/collector.py`, `features/main/prompts/protocol_builder.py` | `compose_app()` → `WalkerApp` inyectado (o facade) |
| `features/main/front_controller.py` | Constructor ya recibe `WalkerApp` (no cambia API); el entry point es quien lo construye una vez |
| `features/main/cli_interface/infrastructure/reloader.py` | Build diferido: detecta cambio → marca `dirty` → rebuild entre requests → swap atómico; ya no toca `sys.modules` ni el locator desde el request |
| `core/engine/bootstrap.py` | `get_module_service` delega en el resolver del container (inyectado en `build()`); el reloader no lo invalida por separado |

### Archivos que se eliminan / dejan de usarse

| Archivo | Acción |
|---|---|
| `_COMPOSITION_SINGLETON` global | Eliminar (absorbido por `AppContainer` como único tipo) |
| `_SERVICE_LOCATOR["fn"]` como segundo global | Pasa a ser el resolver del container, inyectado |

---

## 6. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | Un call site viejo `compose_app()`/`AppContainer.build()` reintroduce un cold start en el request | Migración incompleta de los 13 call sites; un handler nuevo usa `build()` por costumbre | Linter rule: prohibir `compose_app()` y `AppContainer.build()` fuera de `mcp.py`/`cli_interface/__init__.py` | CI grep: `grep -rn "compose_app()\|AppContainer.build()" features/main/cli_interface/application/` |
| 2 | Build diferido entrega un container a medias (registries parciales) | El build falla a mitad (import error en un módulo nuevo) | Swap atómico solo si `build()` retorna Success; si falla, se conserva el container actual y se loggea (el módulo roto se ve en el log) | Test: `test_build_diferido_keeps_old_on_failure_bdd.py` + log de error en el reloader |
| 3 | El código recargado no se ve (server stale persistente) — regresión del comportamiento actual | El reloader vuelve a invalidar solo el locator y no el container, o el container cachea módulos viejos | El reloader construye un container NUEVO enteramente (imports frescos) entre requests; el swap reemplaza TODOS los singletons | Test E2E: editar un `.py` de `features/`, esperar 2s, invocar la tool y verificar el cambio de comportamiento |
| 4 | `get_module_service` se queda sin resolver (KeyError "Servicio no registrado") | `build()` no pobló `_services` o el registro idempotente omitió un servicio | Test de completitud del container (patrón AGENTS.md): `EXPECTED` incluye workflow/task_registry/side_effect_engine/cpg/ckg/delta/trace/state/project/task | `test_container_completeness_bdd.py` |
| 5 | Un request observa referencias mixtas entre el container viejo y el nuevo | El swap de `_CONTAINER` ocurre en medio de un request que ya tomó referencias de ambos | El request toma TODAS sus referencias del container al inicio (snapshot de `container` al entrar) y el swap solo cambia `_CONTAINER` para requests futuros; el build diferido es secuencial (entre requests), sin concurrencia | Test de secuencia: request 1 usa container viejo completo; tras el swap, request 2 usa container nuevo completo; ningún request mezcla |
| 6 | `core/engine/bootstrap.py` se acopla a `features/main` al delegar el resolver | Import directo de features/main desde core/engine viola ADR-045 | El resolver se **inyecta** (parámetro/`set_container`), nunca se importa; core/engine sigue sin conocer features/ | CI lint: `grep -r "features\." core/engine/` (heredado de ADR-045) |
| 7 | Los warnings `Provider 'diff' already registered — overwriting` vuelven | El registro idempotente se relaja y vuelve a pisar providers | El registro verifica duplicados contra el conjunto esperado y FALLA (no over-write) | Test: `test_registration_idempotent_bdd.py` — registrar el mismo provider 2 veces no genera warning |
| 8 | Un request crea un container por `child()` y lo pierde (memory leak) | `RequestScope` no liberado o reference cycle | `RequestScope` es ligero (ctx + chain_dir + task_id), sin referencias a registries; documentar su vida (un request) | Test: crear 10k scopes en bucle y verificar que la memoria no crece |
| 9 | `data/projects.json` contaminado: el CLI registra un repo temporal y el siguiente proceso (MCP) lo ve | Un test o un `walker prepare` corre contra un `repo_path` que apunta a un directorio temporal ya borrado | Documentar que `data/projects.json` es un registro global de procesos; el container no comparte estado entre procesos — cada proceso construye su container independiente (como hoy) | Test E2E: `walker list` desde un proceso nuevo no muestra proyectos cuyos `repo_path` no existen (entry con `repo_path` inexistente se marca y se salta) |
| 10 | Una suite de tests resetea el container a mitad de otro test (estado viejo visible) | Tests usan el container global sin fixture de reset | `reset_composition()` (ya existe) se extiende para resetear el container completo; los tests que necesitan container usan fixture `_container_fixture` (patrón conftest existente) | `test_container_reset_bdd.py` |

---

## 7. Checklist de segundo orden

1. **¿Qué módulos importan esto?**
   - `features/main/bootstrap.py` (implementa `AppContainer.build`)
   - `features/main/cli_interface/mcp.py` + `mcp_adapter.py` (boot del server)
   - `features/main/cli_interface/__init__.py` (boot del CLI)
   - `features/main/cli_interface/application/handlers.py`, `project_use_cases.py` (inyección en vez de `compose_app()`)
   - `features/main/prompts/collector.py`, `protocol_builder.py`
   - `core/engine/bootstrap.py` (resolver inyectado, sin acoplamiento)
   - `tests/` (fixtures de container, reset extendido)

2. **¿Qué side effects dispara?**
   - `AppContainer.build()` es el único punto que registra tasks, side effects, providers y módulos MCP (cold start).
   - Los side effects de dominio (transform_spec, create_adr_from_spec) se ejecutan en el `SideEffectEngine` poblado por `build()` — sin cambio de comportamiento.
   - Build diferido entre requests no dispara side effects: solo reemplaza la referencia del container (NUNCA re-ejecuta tasks).

3. **¿Qué eventos de dominio publica?**
   - Ninguno nuevo. Los eventos de sprint y de workflow siguen emitiéndose desde el engine vía los providers de contexto.
   - Cambio indirecto: el reloader ya no emite el evento "módulo recargado" durante el request; el swap es silencioso para el dominio.

4. **¿Qué tests dependen de este comportamiento?**
   - Todos los que hoy usan `compose_app()` directa o indirectamente (suites MCP, CLI, E2E y de composición): deben migrar al container o a fixtures.
   - `tests/test_container_lifetime_bdd.py`, `test_container_completeness_bdd.py`, `test_build_diferido_*` (nuevos).
   - Tests que dependen de `reset_module_registry()`: deben pasar a `reset_composition()` (container completo).

5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?**
   - MCP adapter y CLI: boot con container único; `_handle_request` y `_call_tool` no construyen nada (solo resuelven).
   - CKG/DeltaGraph/Trace: sin cambios de interfaz; siguen consumiendo el container vía providers resueltos por `resolve()`.
   - Trace: el reloader ya no toca módulos por request → menos ruido en el TraceGraph del proceso.

---

## 8. Implementación por fases

### Fase 1 — Container + inyección en entry points (chico, alto impacto, reversible)

1. Crear `features/main/container.py` con `AppContainer` (fusión de `AppComposition`) y `RequestScope` (sin tocar registros todavía).
2. `AppContainer.build()` delega en el cuerpo actual de `compose_app()` y expone `resolve()`/`child()`; `compose_app()` queda como alias temporal (deprecado).
3. `mcp.py:main()` y `cli_interface/__init__.py:main()` construyen `WalkerApp(AppContainer.build())` **una vez** y lo inyectan al `FrontController`.
4. Migrar los 13 call sites de `compose_app()` a recibir el `WalkerApp` inyectado (uno por uno, suite verde entre cada uno).
5. Eliminar `compose_app()` como API pública (queda solo `AppContainer.build()`).

### Fase 2 — Consolidación del registro idempotente (medio)

1. Consolidar `_discover_and_register_tasks/side_effects/providers/mcp_modules` en `register_all(container)`.
2. Hacer el registro aditivo y verificador: duplicados → fallo de completitud, no over-write.
3. Test de completitud del container completo (extender el patrón de `build_context_registry`).

### Fase 3 — Build diferido del reloader (grande)

1. El reloader detecta cambio (throttle existente) y **marca `dirty`** en vez de recargar módulos.
2. Entre requests: si `dirty` y no hay build en curso, construye `AppContainer.build()` sincrónico; swap atómico de `_CONTAINER` solo si `build()` es Success; en fallo, conserva el actual y loggea.
3. Eliminar `importlib.reload` y la invalidación del locator del camino del request.
4. Extender `reset_composition()` para resetear el container completo (tests).

---

## 9. Referencias

- ADR-WALKER-045: `features/main` como composition root final (base de este ADR).
- ADR-WALKER-016: DIP — la capa de aplicación define puertos; la composición los implementa.
- ADR-WALKER-043/044: protocolo y tools con SSOT único (la tool catalog resuelve desde el registry, no de `compose_app()`).
- `capa_rout.md`: D1 — 1 composition root (este ADR consolida: 1 container con ciclo de vida).
- Seemann, M. *Dependency Injection in .NET* — Composition Root, lifetimes (singleton/scoped), el anti-patrón Service Locator en el núcleo de la aplicación.
- Historia de fallos: timeout MCP 6-ago, 8-ago, 12-ago 2026 (mismo patrón estructural).