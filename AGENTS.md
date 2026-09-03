# AGENTS.md — Walker

Guía para agentes LLM que contribuyen a Walker.

---

## Ritual de aprobación de specs/ADRs

Toda spec o ADR nueva debe incluir una sección `failure_scenarios` antes de ser aprobada.

```json
"failure_scenarios": [
  "Si delta_tracking se corrompe, toda la trazabilidad se pierde → mitigación: backup diario",
  "Si _SERVER se cachea y el workflow cambia, tests ven estado viejo → mitigación: reloader + tests con tmp_path"
]
```

Reglas:
1. Mínimo 3 escenarios de fallo por spec/ADR.
2. Cada escenario debe incluir: fallo, causa raíz, mitigación, detección.
3. Sin `failure_scenarios`, la spec/ADR no entra a revisión.

---

## Métricas de calidad por fase (obligatorio)

Cada **fase** de una spec/ADR incorpora **dentro de su checklist** las métricas
aplicables (tabla M-01..M-06 en `project.md` → "Métricas de calidad por fase").

Reglas:
1. Mínimo **3 métricas** por fase, seleccionadas por las áreas que toca la fase
   (escritura, estrategias, dominio, eventos, concurrencia, configuración).
2. Cada ítem usa el formato del checklist de la fase:
   `- [ ] M-0X <criterio medible para esta fase> — anti-patrón a evitar: <...>`
3. Un anti-patrón de un área seleccionada detectado en la implementación = el
   checklist de la fase NO se declara verde hasta corregir.
4. Las métricas no aplicables se declaran como tal; no se omiten en silencio.

---

## Checklist de segundo orden (obligatorio)

Antes de mergear cualquier cambio, responder:
1. ¿Qué módulos importan este?
2. ¿Qué side effects dispara?
3. ¿Qué eventos de dominio publica?
4. ¿Qué tests dependen de este comportamiento?
5. ¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?

Si la respuesta a alguna es "no sé", detenerse y documentar.

---

## Invariantes del sistema

Ver `project.md` para la lista completa de invariantes (INV-001 a INV-012).

Resumen:
- WorkflowState nunca se pierde entre submits/transitions.
- JSON-RPC 2.0 es el transporte MCP obligatorio.
- `_handle_request` es la entrada MCP única.
- delta_tracking es el SSOT del sprint y specs.
- Side effects son atómicos.
- TaskRegistry separado en DomainTaskRegistry y SystemTaskRegistry.
- Toda operación retorna `Result[T, E]` de `returns`.
- Las funciones públicas deben ser puras o casi-puras.

---

## Fail-fast, sin fallbacks implícitos (OBLIGATORIO)

**Principio:** el código **funciona o genera un error**. No hay backends de
fallback, no hay defaults silenciosos, no hay degradación silenciosa. Si algo
no está explícitamente resuelto, **falla con un error claro** — un error nos
dice que algo está mal; la ausencia de error nos dice que funcionó.

Reglas:
1. **Nada de fallback a FileBackend.** El `backend` de `CognitiveGraph` es
   OBLIGATORIO (fail-fast). `file` no es un backend runtime válido
   (ADR-WALKER-063); `sqlite` es el único backend registrado. Construir sin
   backend explícito → `ValueError` con instrucciones claras.
2. **`build_cognitive_graph()` es el único constructor canónico.** Resuelve
   el backend por configuración (`load_memory_connection` + `resolve_storage_backend`).
   No se instancia `CognitiveGraph(chain_dir)` directamente en producción.
3. **Los repos (CKG/CPG/Delta/Trace) reciben `data_dir` resuelto por el
   caller** vía `ProjectService.resolve_paths()` / `paths`. Nunca computan
   rutas internamente (`os.path.join(project_root, "data")`) ni asumen
   convenciones de layout.
4. **Todo uso de un puerto/backend no disponible → error, no `None` ni
   degradación.** Si un componente no se puede resolver, se lanza
   `ValueError`/`StorageError` con mensaje accionable.
5. **Excepción controlada:** los guards de "no project context" en el adapter
   MCP devuelven error estructurado (JSON-RPC error), no un fallback a un
   backend ficticio.

Este principio se aplica en todo el codebase, no solo en el storage: cualquier
path que antes "hacía algo por defecto" debe decidir explícitamente o fallar.

---

## Estrategia domain-first (OBLIGATORIA)

Walker se diseña **domain-first**: el modelo de dominio es SSOT y viene primero; la infraestructura (MCP, CLI, DB) es el último en aparecer. Fases 0–5 y su mapeo a capas reales en [`docs/domain-first.md`](docs/domain-first.md) — obligatoria para toda contribución.

Regla mínima:
- Las dependencias apuntan hacia el dominio (`shared/core/engine/domain`).
- `shared/core/engine/` **nunca** importa `features/`.
- Los adaptadores (fase 5) consumen puertos, no reconstruyen el dominio; se cablean en el composition root (ADR-045) y se resuelven por **id+contrato** (ADR-062).

---

## Estructura del proyecto

```
walker/
  features/
    cli_interface/     # CLI + MCP adapter
    engine/            # Workflow engine, orchestrator, use_cases
    delta_tracking/    # Spring/spec tracking (SSOT)
    cognitive_knowledge/ # CKG
    code_analysis/     # CPG
    event_tracing/     # Trace
    task_execution/    # Task handlers + side effects
  libs/
    cognitive_graph/   # CPG/CKG/Delta/Trace storage
    context/           # Context models
    router/            # REST router
  tests/
    test_*_e2e_bdd.py  # Tests E2E por nivel
  schemas/specs/       # SPECs en JSON
  docs/                # Documentación arquitectónica
  project.md           # Constitución del proyecto
```

---

## Comandos útiles

```bash
# Tests E2E completos
pytest tests/test_agent_e2e_bdd.py tests/test_cli_walker_bdd.py tests/test_rag_pipeline_bdd.py tests/test_agent_session_bdd.py -v

# Listar tools MCP
python -c "from features.engine.application.tool_catalog import list_tools; import json; print(json.dumps([t['name'] for t in list_tools()], indent=2))"

# Validar workflow
python -c "from features.engine.workflow import WorkflowRepository; wr = WorkflowRepository(); wf = wr.load('features/engine/workflow'); print('OK' if wf else 'FAIL')"
```

---

## Patrón: tests de completitud para registries/bootstrap

Todo registry construido por `bootstrap_*()` o `build_*()` debe tener un test de completitud que valide el conjunto esperado de componentes.

```python
EXPECTED = {"cpg", "ckg", "delta", "trace", "state", "project", "task"}
available = set(registry.available())
missing = EXPECTED - available
assert not missing, f"Faltan providers: {missing}"
```

Aplicar a:
- `build_context_registry()` → providers de contexto
- `bootstrap_tasks()` → tasks del TaskRegistry
- `bootstrap_modules()` → tools del ModuleRegistry
- `SideEffectEngine.register()` → side-effects declarados

Sin este patrón, omisiones como `task`/`state` faltando en el ContextProviderRegistry pasan desapercibidas hasta producción.

---

## Contribución

1. Leer SPEC-004 (modelos de pensamiento) y `project.md` (invariantes).
2. Escribir `failure_scenarios` en la spec/ADR.
3. Pasar checklist de segundo orden.
4. Implementar con `returns` y funciones puras.
5. Tests E2E en `tests/test_*_e2e_bdd.py`.
6. Merge solo después de verde.
