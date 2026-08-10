# ADR-WALKER-044: Protocolo MCP como SSOT dinámico — eliminación de fallbacks estáticos y listas hardcodeadas

> **Status:** Proposed
> **Fecha:** 2026-08-10
> **Autor:** Kilo + PO
> **Complementa:** ADR-WALKER-043 (contrato SSOT), ADR-WALKER-018 (context gates), ADR-WALKER-016 (protocol BC)

---

## 1. Problema

El protocolo MCP y sus consumidores mantienen **múltiples fuentes de verdad paralelas** que pueden desincronizarse cuando el registry evoluciona:

| Gap | Ubicación | Síntoma |
|---|---|---|
| G1 | `mcp_adapter.py:_PROTOCOL_FALLBACK` | Texto duplicado del protocolo. Si el template dinámico cambia, este fallback queda obsoleto. |
| G2 | `protocol/rest.md` | Template REST estático que no consulta el registry. Hay que actualizarlo a mano al agregar tools. |
| G3 | `.opencode/plugins/walker-session.js` | `ALLOWED_TOOLS`, memory queries y phase tools hardcodeados. El hook no refleja automáticamente las tools nuevas. |
| G4 | `features/engine/mcp.py:reserved` set | Lista paralela de tools excluidas del discovery. Si el TaskRegistry agrega una tool, hay que recordar actualizar este set. |

**Impacto:** drift silencioso entre lo que el MCP expone (28 tools dinámicas) y lo que los consumidores humanos/CLI/hook creen que existe. Baja severidad para el agente LLM (usa `tools/list`), pero alta para el CLI y el hook ADR-018.

---

## 2. Decisión

**Unificar toda la narrativa del protocolo en un generador dinámico y eliminar las listas hardcodeadas de los consumidores.**

El principio rector es: **si la tool existe en el registry, el protocolo la menciona; si no, no**. Ningún consumidor mantiene su propia lista.

### 2.1 Generador único de protocolo

`protocol_builder.py` expone secciones individuales reutilizables:

```python
_build_tools_section()          # Available tools (todas)
_build_analysis_section()       # Available analysis tools (cpg_/kg_/analyze_)
_build_workflow_section(wf_map) # Workflow map
_build_capture_task_section(wf_map) # Session work log
_build_rest_section()           # REST protocol commands + public tools
```

El template `protocol/mcp.md` usa marcadores (`<!-- ANALYSIS_TOOLS -->`) y el builder los reemplaza en runtime.

### 2.2 Eliminación de fallback estático

`_PROTOCOL_FALLBACK` se elimina como texto hardcodeado. La lógica pasa a:

```python
def _load_protocol_text() -> str:
    dynamic = build_mcp_protocol()
    return dynamic or _load_static_template()
```

Si el builder falla, el fallback es el template estático base, **no una copia del protocolo completo**.

### 2.3 REST dinámico

`protocol/rest.md` se convierte en template con marcador `<!-- PUBLIC_TOOLS -->`. El handler `help` y el recurso REST consumen `protocol_builder._build_rest_section()` para inyectar las tools públicas disponibles.

### 2.4 Hook dinámico

El hook `.opencode/plugins/walker-session.js` consume un endpoint auxiliar `walker --tools-json` (o `tools/list` MCP) para armar dinámicamente:
- `ALLOWED_TOOLS`
- Lista de memory queries
- Phase tools

Si el endpoint no está disponible, mantiene fallback hardcodeado.

### 2.5 Eliminación del `reserved` set

El `reserved` set en `features/engine/mcp.py:_register_public_tasks` se elimina. En su lugar, las tools que deben ser excluidas del discovery público llevan metadata `"internal": true` en su registro del TaskRegistry. El filtro pasa a ser:

```python
for t in public_tasks:
    if t.get("metadata", {}).get("internal"):
        continue
```

---

## 3. Consecuencias

### 3.1 Positivas

- **SSOT real**: el registry es la única fuente de verdad; todo lo demás se deriva.
- **Cero mantenimiento manual**: al agregar una tool, el protocolo, el REST help y el hook la reflejan automáticamente.
- **Drift cero por diseño**: no hay listas paralelas que sincronizar.
- **Consistencia ADR-043**: el contrato se define una sola vez en el registro.

### 3.2 Negativas / Riesgos

| Riesgo | Mitigación |
|---|---|
| `build_mcp_protocol()` falla en runtime y el template base pierde secciones dinámicas | El fallback es el template estático base, que sigue siendo legible. Se agrega log de warning. |
| El hook OpenCode no puede resolver `--tools-json` (entorno sin CLI) | Fallback a listas hardcodeadas existentes. Se mantiene backward compatibility. |
| Cambio de comportamiento en `reserved` set: alguna tool que antes se excluía ahora aparece | Auditamos el `reserved` set actual: solo contiene `prepare`, `list`, `status`, `submit`, `transition`, `help`, `kg_*`, `cpg_*`, `analyze_code`, `trace_push`, etc. Esas tools están registradas como públicas pero deben excluirse del discovery MCP porque el MCP ya las expone por otro medio (module registry o REST tools). La solución es marcarlas como `"internal": true` en su metadata, no eliminarlas del registry. |
| Performance: el builder se ejecuta en cada request de protocolo | Cachear el protocolo generado en memoria (`_PROTOCOL_CACHE`) con invalidación por TTL o evento. |

---

## 4. Implementación

### Fase 1 — Eliminar fallback estático duplicado

- [ ] `mcp_adapter.py`: `_PROTOCOL_FALLBACK` se reduce a template base vacío o se elimina.
- [ ] `_load_protocol_text()` usa `build_mcp_protocol()` y fallback a `_load_static_template()`.
- [ ] Test: `test_prompts_ssot_bdd.py` actualiza escenario de fallback.

### Fase 2 — REST dinámico

- [ ] `protocol/rest.md`: agregar marcador `<!-- PUBLIC_TOOLS -->`.
- [ ] `protocol_builder.py`: agregar `_build_rest_section()`.
- [ ] `handlers.py:_load_protocol_rest()` consume el builder.
- [ ] Test: `test_f4_help_rest_bdd.py` valida que REST incluya public tools.

### Fase 3 — Hook dinámico

- [ ] CLI: agregar flag `--tools-json` que exporta `list_tools()` como JSON.
- [ ] `walker-session.js`: al iniciar, consultar `walker --tools-json` y armar `ALLOWED_TOOLS`, memory queries y phase tools dinámicamente.
- [ ] Test: verificar en `.opencode` que el hook arma correctamente la lista desde el registry.

### Fase 4 — Eliminar `reserved` set

- [ ] `features/task_execution/tasks/`: agregar `"internal": true` en metadata de tasks que deben excluirse del discovery MCP (workflow tools, kg_*, etc.).
- [ ] `features/engine/mcp.py:_register_public_tasks`: filtrar por `metadata.internal` en vez de `reserved` set.
- [ ] Test: `test_mcp_tools_list_equivalence_bdd.py` valida que tools internas no aparecen en `tools/list`.

---

## 5. Checklist de validación (BDD ejecutable)

Cada item es un escenario BDD con comando ejecutable y resultado esperado.

- [ ] `tools/list MCP` ≡ `registry.list_tasks` (sin drift)
  - Comando: `pytest tests/test_mcp_tools_list_equivalence_bdd.py -k 'toolslist_mcp_coincide_con_registrylist_tasks'`
  - Esperado: `PASSED`
- [ ] Protocolo MCP dinámico contiene todas las tools públicas registradas
  - Comando: `pytest tests/test_prompts_ssot_bdd.py -k 'el_protocolo_mcp_dinamico_se_sirve_del_template_ssot'`
  - Esperado: `PASSED`
- [ ] REST help muestra tools públicas dinámicamente
  - Comando: `pytest tests/test_f4_help_rest_bdd.py -k 'rest_usar_el_outputSchema_de_la_task'`
  - Esperado: `PASSED`
- [ ] No hay `reserved` set paralelo; filtro usa `metadata.internal`
  - Comando: `grep -n 'reserved' features/engine/mcp.py`
  - Esperado: `No output (sin matches)`
- [ ] Suite completa pasa
  - Comando: `pytest`
  - Esperado: `0 failures`
