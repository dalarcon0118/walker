# ADR-WALKER-016: cli_interface — Bounded Context "Protocol" (Clean Architecture + Hexagonal + DDD)

> **Status:** In Progress — F1-F8 + wiring del dominio al runtime
> **Fecha:** 2026-08-05
> **Contexto:** refactor de `features/cli_interface/` tras el análisis arquitectónico
> **Complementa:** ADR-WALKER-013 (DeltaGraph SSOT), ADR-WALKER-014 (plan.json eliminado), ADR-WALKER-015 (features/project DDD)

---

## 1. Contexto y problema

`features/cli_interface/` es la capa de entrada del walker: expone el sistema como
**CLI REST** (`__init__.py` → Router → handlers → output) y como **servidor MCP JSON-RPC**
(`mcp.py` → `mcp_adapter.py`). El análisis arquitectónico reveló:

| Problema | Severidad |
|---|---|
| `mcp_adapter.py` (983 líneas) hace 6 trabajos: build server + hot-reload + serialización + REST route map + orquestación `analyze_code` + JSON-RPC handler | 🔴 CRÍTICA |
| `handlers.py` (1114 líneas) mezcla orquestación con mapeo HTTP (error→status), messages/next_actions, y lectura directa de registry IO | 🔴 CRÍTICA |
| **Dependencia invertida**: los `mcp.py` de features internas (engine, cognitive, code_analysis, delta_tracking, event_tracing) importan `_run_public_task` DESDE `cli_interface.application.handlers` | 🔴 CRÍTICA |
| **Drift de schemas**: `_TOOLS` hardcodeado en mcp_adapter (137 líneas) vs los `server.tool()` registrados por cada feature — 2 fuentes de verdad → tools no se listan | 🔴 CRÍTICA |
| ~15 `except: pass` silenciosos | 🟠 |
| `parser.py` muerto, `_RESOURCES`/`main()` duplicados | 🟡 |

### Decisión clave de diseño

`cli_interface` NO es un CRUD trivial. Es un **servidor MCP consumido por agentes IA**:
los schemas que el agente ve (inputSchema de cada tool) son el **contrato de negocio**.
Por lo tanto el protocolo se modela como un **bounded context con dominio propio**:
el **Bounded Context "Protocol"**.

---

## 2. Bounded Context "Protocol"

### 2.1 Proposito

Gestionar la **sesión de entrada** (un cliente = agente IA o CLI): negotiate capacidades,
registrar tools, dispatch de llamadas, serialización de respuestas. No toca negocio
(proyectos, sprints, código) — traduce protocolo → servicios de las features internas.

### 2.2 Por qué SÍ modelar dominio aquí

| Necesidad | Problema real que resuelve |
|---|---|
| **DTOs como contrato del agente** | El LLM consume `inputSchema`. Si drift del handler real → agente llama tools inexistentes o con args inválidos |
| **Entidad `Tool`** | Unifica las 2 fuentes de schema (drift actual) en un SSOT |
| **VOs de protocolo** | `ToolName`, `RpcMethod`, `ProtocolVersion`, `SessionId` con validación real (duplicados, formato) |
| **Entidad `McpSession`** | Lifecycle del protocolo: handshake, capabilities, tools registrados |
| **Eventos del protocolo** | Consumibles por `event_tracing`/CKG (observabilidad real) |

### 2.3 Por qué NO ciertos patrones

| Patrón | Razón de rechazo |
|---|---|
| ~~`TaskBus`~~ | Proceso único — la inversión de dependencia se resuelve con inyección directa, no con bus distribuido |
| ~~VOs nominales sin validación~~ | Solo VOs con reglas reales |
| ~~Eventos sin consumidores~~ | Solo eventos que `event_tracing`/CKG consumen |

---

## 3. Estructura objetivo

```
features/cli_interface/
│
├── domain/                              ← EL DOMINIO DEL PROTOCOLO (SSOT)
│   ├── entities.py
│   │   ├── Tool            (name: ToolName, description, input_schema, handler)
│   │   ├── Resource        (uri_pattern: UriPattern, name, handler)
│   │   └── McpSession      (session_id, protocol_version, capabilities, tools: ToolRegistry)
│   ├── values.py
│   │   ├── SessionId, ToolName, RpcMethod, ProtocolVersion, UriPattern, JsonRpcId
│   ├── errors.py
│   │   ├── ProtocolError, UnknownToolError, HandshakeError, InvalidRequestError
│   ├── events.py
│   │   ├── SessionInitialized, ToolRegistered, ToolInvoked, ServerReloaded
│   └── services.py
│       └── ToolRegistry      (aggregate: registra tools, valida unicidad, genera schema SSOT)
│
├── contracts/                           ← DTOs (lo que el agente ve)
│   ├── tool.py                          ToolDefinition {name, description, input_schema}
│   ├── rpc.py                           InitializeRequest, ListToolsResult, ToolCallRequest/Result
│   └── response.py                      Response, NextAction, ErrorResponse
│
├── application/
│   ├── service.py                       ProtocolService (thin orchestrator)
│   ├── use_cases.py                     HandleInitialize, HandleListTools, HandleToolCall, HandleResourceRead
│   └── handlers.py                      Controllers (traducen DTO ↔ feature services)
│
├── infrastructure/
│   ├── mcp_adapter.py                   JSON-RPC transport (thin — wire → DTO)
│   ├── cli_adapter.py                   REST/CLI transport (thin)
│   ├── session_store.py                 InMemorySessionRepository (port impl)
│   ├── serializer.py                    JSON serialization
│   └── presenter.py                     output.py → ProtocolPresenter
│
└── __init__.py                          main() + composition root
```

---

## 4. Modelo de dominio

### 4.1 Value Objects

| VO | Validación | Regla |
|---|---|---|
| `SessionId` | UUID o derivado | identidad única de sesión |
| `ProtocolVersion` | `"2024-11-05"` | solo versiones soportadas |
| `ToolName` | `^[a-z_][a-z0-9_]*$`, no vacío | un tool un handler |
| `RpcMethod` | enum: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`, `resources/read`, `prompts/get`, `ping` | JSON-RPC |
| `UriPattern` | `walker://{scheme}/{path}` | recursos dinámicos |
| `JsonRpcId` | `str \| int \| None` | notificación = sin id |

### 4.2 Entidades

**`Tool`** — registro inmutable de un tool:
```python
@dataclass(frozen=True)
class Tool:
    name: ToolName
    description: str
    input_schema: dict          # SSOT del contrato del agente
    handler: Callable[[dict], dict]
```

**`McpSession`** — lifecycle del protocolo:
```python
@dataclass
class McpSession:
    session_id: SessionId
    protocol_version: ProtocolVersion
    capabilities: Capabilities       # tools/list, resources, prompts
    tools: ToolRegistry              # aggregate
    state: SessionState              # uninitialized → negotiated → active → closed
```

### 4.3 Aggregate: `ToolRegistry`

**Invarianzas:**
- tool names únicos (bloquea shadowing)
- todo tool registrado tiene handler
- `input_schema` generado UNA vez (SSOT) — `list_tools()` y `dispatch` leen del mismo registry
- registrar tool con nombre reservado (duplicado de feature module) → `ToolAlreadyExistsError`

```python
class ToolRegistry:
    def register(self, tool: Tool) -> list[str]:        # (ok, errors)
    def list_tools(self) -> list[ToolDefinition]:        # SSOT → tools/list
    def get_handler(self, name: str) -> Callable | None: # SSOT → dispatch
    def dispatch(self, name: str, args: dict) -> Result  # unico camino
```

### 4.4 Eventos del dominio

| Evento | Consumidor | Disparo |
|---|---|---|
| `SessionInitialized` | `event_tracing` | handshake `initialize` |
| `ToolInvoked` | `event_tracing` | `tools/call` |
| `ToolFailed` | `event_tracing` | error en dispatch |
| `ServerReloaded` | CKG (session_end) | hot-reload de features |
| `ToolRegistered` | log (opcional) | `register()` |

---

## 5. El fix de coherencia (schema SSOT)

**Hoy** — 2 fuentes de schema → drift → tools no listadas:
```
_TOOLS (mcp_adapter.py, 137 líneas hardcodeadas)   ← NO usada
server.tool() de cada feature mcp.py               ← la real
```

**Diseño** — `ToolRegistry` es el SSOT único:
```
features/*/mcp.py register(server)
    → server.tool(name, desc, handler)        # registra en ToolRegistry
tools/list                                     # lee ToolRegistry.list_tools()
tools/call                                     # lee ToolRegistry.get_handler()
                                                → schema y dispatch NUNCA drift
```

---

## 6. Ports (hexagonal)

El dominio define QUÉ necesita; infra implementa CÓMO:

```
ports.py
├── SessionRepository (ABC)     # in-memory en producción
│   ├── save(session)
│   ├── load(session_id)
│   └── close(session_id)
│
└── ProtocolPresenter (ABC)     # output.py implementa
    ├── render_response(response) -> str
    └── render_error(error) -> str
```

### Fix de dependencia invertida (sin bus) — IMPLEMENTADO (F5)

**Antes** (invertido):
```python
# features/engine/mcp.py
from features.cli_interface.application.handlers import _run_public_task  # ❌ outer→inner
```

**Implementado** (feature llama su propia aplicación):
```python
# features/engine/application/task_dispatcher.py  → el dispatcher neutral (SSOT)
def dispatch_public_task(name: str, body: dict) -> dict: ...

# features/engine/mcp.py
from features.engine.application.task_dispatcher import dispatch_public_task  # ✅ propia feature
return lambda args: dispatch_public_task(name, args.get("body", args))
```

`cli_interface` se vuelve solo: `_build_server()` (composición) + `_handle_request`
(traducción JSON-RPC) + dispatch a features. Sin negocio, sin orquestación cross-feature.

Resultado: **0 imports a `_run_public_task` desde cli_interface en features internas**;
`handlers._run_public_task` quedó como wrapper compat que delega al dispatcher.

---

## 7. Plan de refactor (fases)

### Estado real (2026-08-05)

| Fase | Alcance | Estado | Evidencia |
|---|---|---|---|
| **F1** | Domain: `entities.py`, `values.py`, `errors.py`, `events.py`, `services.py` (ToolRegistry) | ✅ COMPLETADO | `tests/test_protocol_domain.py` (18 tests) |
| **F2** | Contracts: `tool.py`, `rpc.py`, `response.py` (DTOs) | ✅ COMPLETADO | `tests/test_protocol_contracts.py` (16 tests) |
| **F3** | Integrar `ToolRegistry` → eliminar `_TOOLS`/`_RESOURCES` muertos | ✅ COMPLETADO | `_TOOLS`/`_RESOURCES`/`_public_task_tools`/`_RICH_SCHEMAS` eliminados (~340 líneas); `McpServer.tool()` con anti-shadowing + return pattern; `tests/test_mcp_server_registry.py` (4 tests); tools/list = 30 |
| **F4** | Thin adapters: `mcp_adapter.py`, `handlers.py` → presentación a `presenter.py` | ✅ COMPLETADO — `mcp_adapter.py` 983→554 (-429); `handlers.py` 1114→983; extraídos `serializer.py`, `reloader.py`, `error_mapping.py`, `presenter.py` (builders de coaching/next_actions). |
| **F5** | Fix inversión de dependencia: features llaman su propia app | ✅ COMPLETADO | `features/engine/application/task_dispatcher.py` creado; 0 imports a `_run_public_task` desde cli_interface en features; runtime `kg_schema`/`kg_search` → ok |
| **F6** | Return pattern en ~15 `except: pass` | ✅ COMPLETADO | 21 swallows → 0 silenciosos (errores → `warnings`/stderr) |

### Fases pendientes

| Fase | Alcance | Criterio de salida |
|---|---|---|
| **F7 (nuevo)** | Fix `status`/`submit`/`transition` via MCP — son endpoints REST, no tasks | ✅ COMPLETADO — tools REST registrados en `mcp_adapter._register_rest_tools` con handler `_call_tool` (Router→handlers); engine/mcp.py deja de exponerlos como tasks. `status`/`list` via MCP → 200 OK. |
| **F8 (nuevo)** | `session_store.py` (InMemorySessionRepository) | ✅ COMPLETADO — implementa port `SessionRepository` (save/load/close con return pattern); 6 tests. |
| **W1 (wiring)** | Conectar el dominio al runtime (DIP) | ✅ COMPLETADO — `McpServer` recibe `ToolRegistry` + `tracer` inyectados (libs NO importa features; duck-typing); `_build_server` es composition root; `_handle_request` usa `McpSession` (initialize → sessionId real) + `JsonRpcError` + `UnsupportedMethodError` (-32601 tipado); `ToolInvoked`/`ToolFailed` emitidos en `dispatch` y propagados al TraceGraph via tracer inyectado. Runtime verificado: handshake, 30 tools, status, error tipado. |

Después de cada fase: **suite verde** (`python3 -m pytest tests/ -q`). Suite actual: **266 passed**.

---

## 8. Reglas de diseño (non-negotiables)

1. **Domain puro**: sin IO, sin imports de infra/adapters. Solo stdlib + dataclasses.
2. **Contracts = DTOs**: sin lógica. El agente consume estos schemas.
3. **Adapter thin**: traduce wire → DTO → feature service. Sin orquestación.
4. **Sin bus**: inyección directa. Cada feature llama su propia aplicación.
5. **Sin drift**: `ToolRegistry` es el único que genera `input_schema` y hace dispatch.
6. **Return pattern**: cero `except: pass` silenciosos.
7. **Eventos con consumidores**: `event_tracing`/CKG, nunca estética.

---

## 9. Preguntas abiertas (a refinar)

- [ ] ¿`project_use_cases.py` (Prepare/CheckHealth cross-feature) se queda en cli_interface o vuelve a features/project con DI?
- [ ] ¿`mcp_config_service.py` es application o adapter (genera config de opencode/claude/cursor)?
- [ ] ¿El hot-reload (`_reload_features_if_changed`) vive en cli_interface o en un tooling aparte?
- [ ] ¿`Router` (libs/router) se mantiene como SSOT de dispatch REST o pasa al dominio?
- [ ] ¿Los eventos del protocolo se registran en `event_tracing` (trace) o en CKG (conocimiento)?
