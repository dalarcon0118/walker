# Plan: Validación del protocolo MCP — El agente como validador

## Resumen

El agente IA se conecta a Walker vía MCP, ejecuta 5 fases de validación, y
genera un reporte. Cada fase es un **prompt que el agente puede ejecutar**.

## Conexión MCP

> **⚠️ ACTUALIZACIÓN DE MÉTODO (2026-09-02, ADR-WALKER-073):** el snippet de abajo
> (`subprocess.Popen` + `mcp_call` raw JSON-RPC, encarnado en `tests/test_mcp_validation_live.py`)
> fue **abandonado como anti-patrón** — buffering frágil de pipes y duplica el protocolo que el
> agente ya habla nativamente. La decisión vigente (memoria 11842) es **agent-as-validator**:
> el agente se conecta a los servers MCP ya expuestos para él y llama las tools directamente
> (`walker_list` / `walker_status` / `walker_prepare`, `memora_*`), interpretando cada respuesta.
> El reporte consolidado con resultados reales vive en [`MCP_VALIDATION_REPORT.md`](MCP_VALIDATION_REPORT.md).
> Lo que sigue se conserva como contexto histórico de por qué se descartó.

### ❌ Método abandonado — subprocess + JSON-RPC a mano (anti-patrón)

```python
import subprocess, json, sys, os

proc = subprocess.Popen(
    [sys.executable, "bin/walker-mcp"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, cwd="/path/to/walker",
    env={**os.environ}
)

def mcp_call(method, params=None, call_id=1):
    """Envía un JSON-RPC y retorna la respuesta."""
    msg = {"jsonrpc": "2.0", "id": call_id, "method": method}
    if params is not None:
        msg["params"] = params
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    return json.loads(line) if line else None
```

### Ejemplo de uso

```python
# Initialize
init = mcp_call("initialize", {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "agent-validator", "version": "1.0"}
})
print(init["result"]["serverInfo"]["name"])  # "walker"

# List tools
tools = mcp_call("tools/list", {}, call_id=2)
print(len(tools["result"]["tools"]))  # 28

# Call a tool
status = mcp_call("tools/call", {
    "name": "status",
    "arguments": {"_project_id": "walker"}
}, call_id=3)
print(json.loads(status["result"]["content"][0]["text"]))
```

---

## Fase 0: El agente descubre el server

### Prompt

```
Necesito validar que puedo trabajar con Walker. Haz lo siguiente:

1. Abre una conexión a Walker ejecutando: python3 bin/walker-mcp
2. Envía initialize con protocolVersion "2024-11-05"
3. Lee la respuesta y verifica:
   - El campo "result.serverInfo.name" es "walker"
   - El campo "result.capabilities.tools" existe
4. Envía tools/list
5. Cuenta las tools en la respuesta
6. Verifica que hay exactamente 28 tools
7. Para las primeras 5 tools, verifica que tienen name, description y inputSchema

Reporta:
- ¿Pudiste conectarte? (sí/no)
- ¿Cuántas tools hay? (número)
- ¿Todas tienen schema? (sí/no)
- ¿Algún tool te parece sospechoso? (descripción)
```

### Qué debe ver el agente

**Initialize response:**
```json
{
  "jsonrpc": "2.0", "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {"listChanged": false}},
    "serverInfo": {"name": "walker", "version": "1.23.3"}
  }
}
```

**Tools/list response:**
```json
{
  "jsonrpc": "2.0", "id": 2,
  "result": {
    "tools": [
      {"name": "prepare", "description": "Crea un proyecto Walker", "inputSchema": {"type": "object", "properties": {...}}},
      {"name": "list", "description": "Lista proyectos", "inputSchema": {"type": "object", "properties": {}}},
      ...
    ]
  }
}
```

### Checks del agente

| Check | Esperado | Cómo valida |
|-------|----------|-------------|
| Conexión | Server responde | Lee stdout, parsea JSON |
| Server name | "walker" | Compara `result.serverInfo.name` |
| Tool count | 28 | Cuenta `result.tools` |
| Schemas | `type: "object"` | Lee `inputSchema.type` |
| Descriptions | No vacías | Lee `description` |

---

## Fase 1: El agente prueba cada tool

### Prompt

```
Ahora prueba cada tool de Walker. Para cada una:

1. Envía tools/call con el nombre y params válidos
2. Lee la respuesta
3. Valida:
   a. ¿El JSON tiene "content" con al menos un elemento?
   b. ¿El "text" del primer elemento es JSON válido?
   c. ¿El JSON tiene campo "ok" o "status"?
   d. ¿La respuesta tiene sentido para lo que pediste?

Para cada tool reporta:
- ¿Funcionó? (sí/no)
- ¿Qué devolvió? (resumen en 1 línea)
- ¿La respuesta tiene sentido? (sí/no, por qué)
```

### Tools y qué espera el agente

| Tool | Params | Qué espera | Qué valida |
|------|--------|-----------|------------|
| `prepare` | `{"project_name": "test", "repo_path": "/tmp"}` | `{"ok": true, "project_id": "..."}` | project_id no vacío |
| `list` | `{}` | `{"ok": true, "projects": [...]}` | projects es lista |
| `status` | `{"_project_id": "walker"}` | `{"ok": true, ...}` | project_id = "walker" |
| `help` | `{}` | Lista de tools | Contiene 28 tools |
| `list_projects` | `{}` | `{"ok": true, "projects": [...]}` | projects es lista |
| `sprint_planning` | `{"_project_id": "walker", "goal": "test"}` | `{"ok": true, "sprint_id": "..."}` | sprint_id no vacío |
| `resolve_project_sprint` | `{"_project_id": "walker"}` | `{"ok": true, "chain_dir": "..."}` | chain_dir es path |
| `trace_push` | `{"_project_id": "walker", "event_type": "test", "data": {}}` | `{"ok": true}` | ok = true |
| `implement_task` | `{"_project_id": "walker", "task_name": "test"}` | `{"ok": true, ...}` | resultado presente |
| `run_tests` | `{"_project_id": "walker"}` | `{"ok": true, ...}` | resultado presente |
| `verify_predicates` | `{"_project_id": "walker", "predicates": []}` | `{"ok": true, ...}` | respuesta válida |
| `capture` | `{"_project_id": "walker", "evidence_type": "test", "data": {}}` | `{"ok": true}` | ok = true |
| `build_code_index` | `{"_project_id": "walker"}` | `{"ok": true, ...}` | respuesta válida |
| `index_repo` | `{"_project_id": "walker"}` | `{"ok": true, ...}` | respuesta válida |
| `update_incremental_code` | `{"_project_id": "walker"}` | `{"ok": true, ...}` | respuesta válida |
| `extract_knowledge` | `{"_project_id": "walker"}` | `{"ok": true, ...stats...}` | stats presente |
| `knowledge_query` | `{"_project_id": "walker", "query": "test"}` | `{"ok": true, ...results...}` | results es lista |
| `memory_schema` | `{"_project_id": "walker"}` | `{"ok": true, ...schema...}` | schema válido |
| `recall_knowledge` | `{"_project_id": "walker", "query": "test"}` | `{"ok": true, ...results...}` | results presente |
| `documenting_knowledge` | `{"_project_id": "walker", "doc": "test"}` | `{"ok": true}` | ok = true |
| `search_memories` | `{"_project_id": "walker", "query": "test"}` | `{"ok": true, ...results...}` | results presente |
| `agent_context` | `{"_project_id": "walker"}` | `{"ok": true, ...context...}` | context presente |
| `code_context` | `{"_project_id": "walker", "file_path": "fast_mcp/main.py"}` | `{"ok": true, ...context...}` | context contiene código |
| `inspect_codebase` | `{"_project_id": "walker"}` | `{"ok": true, ...info...}` | info presente |
| `read_code` | `{"_project_id": "walker", "file_path": "fast_mcp/main.py"}` | `{"ok": true, "content": "..."}` | content es Python |
| `code_search` | `{"_project_id": "walker", "pattern": "def"}` | `{"ok": true, ...results...}` | results con matches |
| `submit` | `{"_project_id": "walker", "chain_dir": "/tmp"}` | `{"ok": true/false, ...}` | respuesta válida |
| `transition` | `{"_project_id": "walker", "chain_dir": "/tmp", "from_node": "A", "to_node": "B"}` | `{"ok": true/false, ...}` | respuesta válida |

---

## Fase 2: El agente ejecuta un workflow completo

### Prompt

```
Ahora ejecuta un workflow completo con Walker. Este es el flujo que usarías
en una sesión real de trabajo:

PASO 1: Crea un sprint — llama sprint_planning con goal "Validación MCP"
PASO 2: Resuelve el chain_dir — llama resolve_project_sprint
PASO 3: Envía el workflow — llama submit con el chain_dir
PASO 4: Avanza el estado — llama transition (PLANNING → ACTIVE)
PASO 5: Verifica el estado — llama status, verifica ACTIVE
PASO 6: Registra traza — llama trace_push
PASO 7: Lista proyectos — llama list_projects, verifica "walker" presente

Después de cada paso reporta qué viste y si continuas.
```

### Flujo esperado

```
sprint_planning(_project_id="walker", goal="Validación MCP")
  → {"ok": true, "sprint_id": "sprint-e2e-001"}

resolve_project_sprint(_project_id="walker")
  → {"ok": true, "chain_dir": "/.../spring/sprint-e2e-001"}

submit(_project_id="walker", chain_dir="/.../spring/sprint-e2e-001")
  → {"ok": true}

transition(_project_id="walker", chain_dir="/.../spring/sprint-e2e-001",
           from_node="PLANNING", to_node="ACTIVE")
  → {"ok": true}

status(_project_id="walker")
  → {"ok": true, "current_sub_state": "ACTIVE"}

trace_push(_project_id="walker", event_type="e2e_complete", data={})
  → {"ok": true}

list_projects()
  → {"ok": true, "projects": ["walker", ...]}
```

### Validación del agente

El agente verifica **coherencia del ciclo**:
1. ¿El sprint_id del paso 1 aparece en el chain_dir del paso 2?
2. ¿El submit del paso 3 fue para el sprint correcto?
3. ¿La transition del paso 4 movió el estado?
4. ¿El status del paso 5 refleja la transition?
5. ¿El trace del paso 6 se registró?
6. ¿"walker" sigue visible en list_projects?

---

## Fase 3: El agente prueba errores

### Prompt

```
Prueba qué pasa cuando algo sale mal. Walker debe manejar errores
graciosamente — sin traceback, sin crashear, con mensajes claros.

PRUEBA 1: Llama "nonexistent_tool" → espera error JSON-RPC
PRUEBA 2: Llama "status" sin _project_id → espera error de validación
PRUEBA 3: Llama "status" con _project_id "fake" → espera "not found"
PRUEBA 4: Después de cada error, llama "help" → verifica que server sigue vivo
```

### Qué debe ver el agente

| Prueba | Envía | Espera | No espera |
|--------|-------|--------|-----------|
| Tool inexistente | `tools/call "nonexistent"` | JSON-RPC error | Traceback, crash |
| Params faltantes | `tools/call "status" {}` | Error de validación | KeyError |
| Project fake | `tools/call "status" {"_project_id": "fake"}` | "not found" | Crash |
| Post-error | `tools/call "help" {}` | Lista de tools | Error |

---

## Fase 4: El agente valida production readiness

### Prompt

```
Valida que Walker está listo para trabajo real:

PRUEBA 1: Performance — llama "help" 10 veces, mide latencia
PRUEBA 2: Consistencia — llama "status" 5 veces, verifica mismas respuestas
PRUEBA 3: Reconexión — cierra y reabre conexión, verifica funcionalidad
PRUEBA 4: Estado post-errores — ejecuta errores de Fase 3, luego verifica status

Reporta veredicto: ¿Walker está listo para producción?
```

### Veredicto del agente

```markdown
VEREDICTO: Walker MCP está listo para producción ✅
- 28/28 tools funcionan correctamente
- Workflow E2E completo: 7/7 pasos OK
- Manejo de errores: 4/4 pruebas OK
- Performance: 45ms promedio
- Consistencia: 5/5 idénticas
- Reconexión: OK

RECOMENDACIÓN: Proceder a usar Walker para trabajo real.
```

---

## Ejecución

### Como prompt al agente

```
Antes de empezar a trabajar, valida que Walker funciona:
1. Conéctate a Walker vía MCP (python3 bin/walker-mcp)
2. Ejecuta las 5 fases de validación
3. Genera un reporte con veredicto
4. Si todo está OK, procede a trabajar
5. Si hay problemas, reporta y no continúes
```

### Como pytest (automatizado)

```bash
# Vía el cliente MCP OFICIAL (mcp.client.stdio + ClientSession) — NO un cliente a mano:
python3 -m pytest tests/test_fast_mcp_stdio_validation_bdd.py tests/test_fast_mcp_workflow_application_bdd.py -v
```

> Nota: `tests/test_mcp_validation_live.py` (cliente custom subprocess) fue **gutteado a un
> placeholder documental** (decisión del usuario, 2026-09-03): ahora es un set de prompts por fase
> del método agent-as-validator, sin cliente MCP ejecutable (0 tests pytest — intencional).
> El runner `MCPClient` subprocess/raw-JSON-RPC quedó eliminado.

### Como pre-session check

El agente valida que Walker responde antes de trabajar, llamando las tools MCP
directamente (no con `subprocess`/`mcp_call`, método abandonado):

```
Antes de empezar, valida que Walker funciona:
1. Llama walker_list({}) → verifica status_code 200 y proyectos registrados
2. Llama walker_status({"project_id": "walker"}) → verifica health OK
3. Si responden correctamente, procede
4. Si fallan, reporta el problema y no continúes
```
