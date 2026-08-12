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
