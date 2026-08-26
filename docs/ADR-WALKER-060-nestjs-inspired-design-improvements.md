# ADR-WALKER-060: NestJS-inspired design improvements — organisational patterns over framework dogma

> **Status:** Propuesto
> **Fecha:** 2026-08-25
> **Autor:** Walker (agente) + PO
> **Complementa:** ADR-WALKER-058 (anti-patterns elimination), ADR-WALKER-059 (DI migration), ADR-WALKER-045 (composition root único)

---

## 1. Contexto

Tras eliminar el service locator (ADR-WALKER-059) y el protocol coupling (ADR-WALKER-058), Walker tiene una DI limpia y un composition root único. Pero la arquitectura仍 tiene deuda técnica estructural:

- **Scope check inline** en `task_dispatcher.py` (no reutilizable)
- **Error mapping disconnected** del request pipeline
- **Middleware pipeline infrautilizado** (Router lo soporta pero nadie lo usa)
- **Module boundaries informales** (features son directorios sin declaración de exports)
- **Sin cross-cutting concerns** declarativos (logging, validation, caching)

### Qué NO queremos de NestJS

| Patrón NestJS | Por qué NO |
|---|---|
| IoC container con `@Injectable()` + metadata reflection | Complejidad innecesaria; ya tenemos DI paramétrico limpio |
| Decorators como mecanismo de wiring | Python tiene `@decorator` pero sin reflection metadata de TypeScript |
| `@nestjs/cqrs` (CommandBus + EventBus completo) | Walker ya tiene TaskRegistry (command bus lite) + EventBus (code_analysis) |
| Exception filters como clase | Funciones `@safe` + `classify_error` son más deterministas |

### Qué SÍ queremos de NestJS

| Patrón NestJS | Adaptación Walker | Principio |
|---|---|---|
| **Guards** | `ScopeGuard` — check de permisos declarativo, no inline | Separación de concerns |
| **Interceptors** | `TaskInterceptor` — logging + metrics + timeout antes/después de cada task | Cross-cutting concerns |
| **Module declaration** | `__module__.py` — exports explícitos por feature | Boundaries claros |
| **Lifecycle hooks** | `on_module_init()` / `on_module_destroy()` — init ordenado | Ciclo de vida explícito |
| **Pipes** | `InputValidator` — validación de schema antes de ejecutar task | Fail-fast determinista |

---

## 2. Decisiones

### D1 — ScopeGuard: extraer visibility check de task_dispatcher

**Actual:** `execute_public_task()` tiene el scope check inline (líneas 55-59).

```python
# ANTES (inline, acoplado):
info = registry.get_task_info(name)
if info is not None:
    scope = info.get("scope", "public")
    if scope in ("internal", "system") and channel != "system":
        return {"ok": False, "blocked": True, "name": name}
```

**Decisión:** Extraer a `ScopeGuard.check(task_name, channel, registry) → bool`.

```python
# DESPUÉS (guard reutilizable):
# shared/core/engine/domain/guards.py

class ScopeGuard:
    """Determinista: check de scope sin side effects ni try/catch."""

    BLOCKED_SCOPES = frozenset({"internal", "system"})
    ALLOWED_CHANNELS = frozenset({"system"})

    @staticmethod
    def check(task_name: str, channel: str, registry: Any) -> bool:
        info = registry.get_task_info(task_name)
        if info is None:
            return True  # task inexistente → deja que execute falle
        scope = info.get("scope", "public")
        if scope in ScopeGuard.BLOCKED_SCOPES and channel not in ScopeGuard.ALLOWED_CHANNELS:
            return False
        return True
```

**Ventaja:** El guard se puede usar en `task_dispatcher`, `mcp_adapter`, `engine_mcp` — cualquier punto de dispatch.

### D2 — TaskInterceptor: cross-cutting concerns en el pipeline de tasks

**Actual:** `InMemoryTaskRegistry.execute()` ejecuta la task directamente. Sin logging, sin timeout, sin metrics.

**Decisión:** Agregar pipeline de interceptors en `InMemoryTaskRegistry`:

```python
# shared/core/engine/infrastructure/task_registry.py

class TaskInterceptor(Protocol):
    """Protocol funcional: intercept(name, ctx, data, next) → TaskResult."""
    def intercept(self, name: str, ctx: Any, data: dict, next: Callable) -> TaskResult: ...

class LoggingInterceptor:
    """Determinista: logea duración y resultado sin side effects en error."""
    def intercept(self, name, ctx, data, next):
        start = time.monotonic()
        result = next()
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("task=%s dur=%dms ok=%s", name, duration_ms, result.ok)
        return result

class MetricsInterceptor:
    """Determinista: incrementa counters por task/status."""
    def __init__(self):
        self._counts: dict[str, int] = {}
    def intercept(self, name, ctx, data, next):
        result = next()
        key = f"{name}:{'ok' if result.ok else 'error'}"
        self._counts[key] = self._counts.get(key, 0) + 1
        return result
```

**Pipeline en `InMemoryTaskRegistry.execute()`:**

```python
def execute(self, name, ctx, data=None):
    task = self._tasks.get(name)
    if not task:
        return TaskResult.failure(...)

    def _run():
        return task(ctx, data or {})

    # Ejecutar interceptors (patrón onion, no try/catch)
    pipeline = _run
    for interceptor in reversed(self._interceptors):
        next_fn = pipeline
        pipeline = lambda n=next_fn: interceptor.intercept(name, ctx, data, n)

    return pipeline()
```

**Interceptors registrados:** `LoggingInterceptor` + `MetricsInterceptor` (global). Cualquier feature puede registrar interceptors específicos.

### D3 — ScopeGuard integrado en task_registry.execute()

**Decisión:** El scope check se ejecuta COMO interceptor de bajo nivel, no en `execute_public_task`:

```python
class ScopeGuardInterceptor:
    """Interceptor: bloquea tasks scope=internal/system desde channels externos."""
    def __init__(self, registry):
        self._registry = registry
    def intercept(self, name, ctx, data, next):
        channel = getattr(ctx, "channel", "cli")
        if not ScopeGuard.check(name, channel, self._registry):
            return TaskResult.blocked(name)
        return next()
```

`execute_public_task()` queda como thin dispatcher — sin lógica de permisos.

### D4 — Module declarations para features

**Actual:** features son directorios con `__init__.py` ad-hoc. No hay exports formales.

**Decisión:** Cada feature declara su módulo en `__module__.py`:

```python
# features/code_analysis/__module__.py

MODULE = {
    "name": "code_analysis",
    "description": "CPG analysis, dead code detection, security scanning",
    "exports": ["CpgService", "EventBus", "AnalysisService"],
    "providers": ["CpgRepository", "WalkerCpgAdapter"],
    "imports": ["shared.core.engine", "features.project"],
    "guards": [],          # ScopeGuard se aplica globalmente
    "interceptors": [],    # LoggingInterceptor se aplica globalmente
}
```

**No es enforceable en runtime** — es declaración + convention. El composition root puede leer `MODULE["exports"]` para validar que features no importen de exports no declarados.

### D5 — Lifecycle hooks: `on_module_init()` / `on_module_destroy()`

**Actual:** `_inject_service_locator()` se llama ad-hoc en `lifecycle.py`. Sin cleanup.

**Decisión:** Lifecycle explícito en `AppContainer`:

```python
# features/main/composition/container.py

class AppContainer:
    @classmethod
    def build(cls, chain_dir="", *, force=False):
        app = cls._build_graph(chain_dir)
        app.on_module_init()  # post-build hooks
        return app

    def on_module_init(self):
        """Post-build: inyecta service locator, registra watchers, etc."""
        _inject_service_locator(self)

    def on_module_destroy(self):
        """Cleanup: cierra watchers, connections, caches."""
        for name, service in self._services.items():
            if hasattr(service, 'close'):
                try:
                    service.close()
                except Exception:
                    logger.debug("cleanup failed for %s", name)
```

### D6 — InputValidator: validación de schema antes de task execution

**Actual:** `inputSchema` existe en tool catalog pero no se valida. Los errores llegan como `KeyError` en runtime.

**Decisión:** Validación declarativa en el registry, no en cada task:

```python
# shared/core/engine/infrastructure/task_registry.py

class InputValidator:
    """Validación JSON Schema determinista (sin try/catch)."""

    @staticmethod
    def validate(name: str, data: dict, schemas: dict) -> list[str]:
        """Valida data contra inputSchema registrada. Retorna lista de errores."""
        schema = schemas.get(name)
        if schema is None:
            return []  # sin schema → skip validation
        errors = []
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for field in required:
            if field not in data:
                errors.append(f"Campo requerido '{field}' no puede estar vacio")
        return errors
```

**Integración:** Se ejecuta ANTES de la task, no dentro. Fail-fast:

```python
def execute(self, name, ctx, data=None):
    # 1. Validate input (fail-fast)
    errors = InputValidator.validate(name, data or {}, self._input_schemas)
    if errors:
        return TaskResult.failure(f"Contract validation (input) failed — Errores: {'; '.join(errors)}")

    # 2. Run interceptors → task
    ...
```

---

## 3. Alternativas evaluadas

| Alternativa | Pros | Contras | Decisión |
|---|---|---|---|
| **A. Copiar NestJS completo** (@Injectable, @Module, reflection) | Paridad funcional | Complejidad extrema; Python no tiene TS reflection | Rechazada |
| **B. Usar `dependency-injector` lib** | Container maduro | Otra dependencia; no resuelve guards/interceptors | Rechazada |
| **C. Patterns funcionales + guard/interceptor lite (elegida)** | Determinista; sin magic; sin deps nuevas | Requiere discipline (no hay enforcement automático) | Aceptada |

---

## 4. Failure Scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | **ScopeGuard bloquea task válida** | `channel` no se pasa correctamente en el caller | `ScopeGuard.check()` es testable aislado (pure function); test unitario con cada combinación `scope × channel` | `test_scope_guard.py` — 6 escenarios |
| 2 | **Interceptor rompe pipeline** | Bug en `interceptor.intercept()` (no llama `next()`) | `TaskInterceptor` es `Protocol` — si no llama `next()`, el result_holder queda vacío → `TaskResult.failure("interceptor interrupted")` con timeout watchdog | `test_task_interceptor_bdd.py` — interceptor que no llama next |
| 3 | **Module declaration desincronizado** | feature agrega export olvida actualizar `__module__.py` | `test_module_exports_bdd.py` — valida que imports de features externos solo usen exports declarados | CI check: diff de imports vs MODULE["exports"] |
| 4 | **Lifecycle hook falla en on_module_init** | `_inject_service_locator()` falla (composition root no poblado) | `on_module_init()` se ejecuta DESPUÉS de build(); si falla, build retorna None (no singleton roto) | `test_composition_root_d1.py` — ya valida singleton |
| 5 | **InputValidator rechaza input válido** | Schema incompleto o field name mismatch | Schemas se generan desde `input_schema` del task metadata (SSOT); validator es additive (solo required fields) | Tests de cada task con input válido |

---

## 5. Consecuencias

### 5.1 Positivas
- **Scope check reutilizable** — se puede usar en cualquier punto de dispatch (MCP, CLI, engine interno)
- **Cross-cutting concerns centralizados** — logging, metrics, timeout se registran una vez y se aplican a todas las tasks
- **Module boundaries declarativos** — imports entre features son auditables
- **Lifecycle explícito** — init ordenado, cleanup garantizado
- **Fail-fast en input** — errores de validación antes de ejecutar la task

### 5.2 Negativas
- **Convention over enforcement** — module declarations son declarativas, no se fuerzan en runtime
- **Interceptors son síncronos** — no soportan async (Walker actual es sync anyway)
- **Más archivos** — `guards.py`, `interceptors.py`, `__module__.py` por feature

### 5.3 Déuda técnica eliminada
- ~~Scope check inline en task_dispatcher.py~~ → ScopeGuard
- ~~Sin logging de tasks~~ → LoggingInterceptor
- ~~Sin validación de input~~ → InputValidator
- ~~Sin lifecycle hooks~~ → on_module_init/on_module_destroy

---

## 6. Checklist de segundo orden

| Pregunta | Respuesta |
|---|---|
| ¿Qué módulos importan este? | `shared/core/engine/infrastructure/task_registry.py`, `shared/core/engine/application/task_dispatcher.py`, features/*/tasks/ |
| ¿Qué side effects dispara? | LoggingInterceptor escribe a logger; MetricsInterceptor muta dict interno |
| ¿Qué eventos de dominio publica? | Ninguno nuevo (interceptors son observadores, no emisores) |
| ¿Qué tests dependen de este comportamiento? | `test_module_registry_di_bdd.py`, `test_trace_event_publisher_bdd.py`, `test_composition_root_d1.py` |
| ¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace? | MCP adapter y CLI ganan scope check vía guard; CKG/DeltaGraph/Trace sin cambios |

---

## 7. Verificación

| Check | Resultado esperado |
|---|---|
| `ScopeGuard.check("trace_push", "mcp", registry)` → `False` | Task bloqueada desde MCP |
| `ScopeGuard.check("trace_push", "system", registry)` → `True` | Task permitida desde motor interno |
| `ScopeGuard.check("recall_knowledge", "cli", registry)` → `True` | Task pública siempre permitida |
| `LoggingInterceptor` loggea `task=X dur=Yms ok=True` | Cross-cutting concern funcional |
| `InputValidator.validate("submit", {}, schema)` → `["Campo requerido 'body'"]` | Fail-fast antes de ejecutar |
| `MODULE["exports"]` contiene los services públicos de cada feature | Module boundaries declarados |
| `on_module_init()` se ejecuta después de `build()` | Lifecycle ordenado |
| 57+ tests pasan | No regresiones |

---

## 8. Migration path

| Fase | Archivos | Esfuerzo |
|---|---|---|
| **Fase 1:** ScopeGuard | `guards.py` + `task_dispatcher.py` + tests | 1h |
| **Fase 2:** TaskInterceptor pipeline | `task_registry.py` + `LoggingInterceptor` + tests | 2h |
| **Fase 3:** InputValidator | `task_registry.py` + `InputValidator` + tests | 1h |
| **Fase 4:** Module declarations | `__module__.py` por feature | 30min |
| **Fase 5:** Lifecycle hooks | `container.py` + `lifecycle.py` | 1h |
| **Total** | | ~5.5h |
