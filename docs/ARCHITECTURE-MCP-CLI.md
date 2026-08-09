# Walker Architecture: MCP + CLI

## Resumen

Walker es un sistema modular de conocimiento y workflow para proyectos de software.
Su arquitectura se divide en **capas** y **módulos composite** registrables.

## Capas

```
┌─────────────────────────────────────────┐
│           MCP Server (núcleo)           │
│  ModuleRegistry + ToolDefinitions       │
│  - cpg_analyze                          │
│  - ckg_analyze                          │
│  - delta_analyze                        │
│  - trace_push                           │
│  - agent_context                        │
│  - status                               │
│  - submit / transition                  │
└─────────────────────────────────────────┘
              ▲         ▲
              │         │
    ┌─────────┘         └─────────┐
    │                             │
┌───────┐                 ┌──────────────┐
│  CLI  │                 │  REST API    │
│(humano│                 │(integraciones│
│ + cols│                 │  externas)   │
└───────┘                 └──────────────┘
```

### MCP Server (núcleo)
- Es el **SSOT de capabilities**.
- Expone tools MCP con `inputSchema` JSON Schema.
- Cada módulo se auto-registra via `register_module()` en `ModuleRegistry`.
- No conoce el CLI ni REST; solo recibe tool calls y retorna datos estructurados.

### CLI (cliente MCP)
- Consume el MCP server internamente.
- Agrega **formato humano**: tablas, colores, mensajes, next_actions.
- Mantiene comandos de setup (`prepare`, `list`, `help`, `mcp-tools`, `mcp-routes`).
- No ejecuta lógica de dominio; delega en MCP.

### REST API
- Endpoints HTTP para integración externa.
- Algunos endpoints son proxies al MCP.
- No es el camino preferido para el agente; el agente debe usar MCP directo.

## Módulos registrados

| Módulo | Tool unificada | Tools legacy | Eventos dominio |
|--------|---------------|--------------|-----------------|
| `code_analysis` | `cpg_analyze` | — | ✅ |
| `cognitive_knowledge` | `ckg_analyze` | `kg_search`, `kg_store`, `kg_multi` | ✅ |
| `delta_tracking` | `delta_analyze` | `capture_task`, `import_legacy_history`, `detect_legacy`, `map_legacy_workflow`, `spring_analyze`, `spring_plan`, `spring_dashboard`, `spring_close`, `spring_validate` | ✅ |
| `event_tracing` | `trace_push` | — | — |
| `project` | `project_analyze` | `list_projects` | ✅ |
| `engine` | `agent_context` | `status`, `submit`, `transition`, `prepare`, `list`, `help` | — |

### SSOT del spring: delta_tracking

`delta_tracking` es el **SSOT y SRP** de todo lo relacionado con el spring:
- Entidades puras: `Spring`, `Spec` (`domain/entities.py`)
- Servicios de dominio: `SpecValidator`, `SpecBuilder`, `SpecStatusUpdater`, `SprintStatusUpdater` (`domain/services/spec_service.py`)
- Casos de uso: sprint, specs, progress (`application/use_cases/`, `application/use_cases_sprint.py`)
- Orquestador delgado: `SpringService` (`application/services/spring_service.py`)
- Síntesis de tareas: `task_synthesis.py` (`application/task_synthesis.py`)
- Ceremonias: `ceremonies.py` (`application/ceremonies.py`)
- MCP: tools y rutas de spring expuestas por `delta_tracking/mcp.py`

No existe un módulo `spring` separado. Toda la responsabilidad del spring vive en `delta_tracking`.

## Cómo funciona el registro

1. Cada módulo implementa `register_module(registry)` en su `mcp.py`.
2. `features/engine/bootstrap.py` descubre todos los `features/*/mcp.py` automáticamente (sin lista hardcodeada).
3. `mcp_adapter` sincroniza el `McpServer` con el `ModuleRegistry`.
4. El CLI consulta el `ModuleRegistry` para `mcp-tools` y `mcp-routes`.

## API del CLI vs API del MCP

### CLI commands (formato humano)

| Comando | Propósito |
|---------|-----------|
| `walker prepare <path>` | Inicializar proyecto (crea `.cognitive/`) |
| `walker list` | Listar proyectos registrados |
| `walker help` | Ayuda del protocolo |
| `walker mcp-tools` | Listar tools MCP |
| `walker mcp-routes` | Listar rutas REST |
| `walker <project>/status` | Estado del proyecto (formato humano) |
| `walker <project>/submit '{"field":"value"}'` | Submit workflow (formato humano) |
| `walker <project>/transition <gate>` | Transición workflow (formato humano) |

### MCP tools (datos estructurados)

| Tool | Uso |
|------|-----|
| `cpg_analyze` | Análisis unificado del CPG |
| `ckg_analyze` | Análisis unificado del CKG |
| `delta_analyze` | Análisis unificado del DeltaGraph |
| `spring_analyze` | Análisis unificado del spring (plan, dashboard, close, validate, edit) |
| `project_analyze` | Análisis unificado del proyecto (list, detect, init, health) |
| `agent_context` | Contexto consolidado del proyecto |
| `trace_push` | Registrar eventos de trazabilidad |
| `status` | Estado del proyecto (datos estructurados) |
| `submit` | Submit workflow (datos estructurados) |
| `transition` | Transición workflow (datos estructurados) |

## Reglas de uso

1. **El agente IA DEBE usar MCP tools** para operaciones de dominio.
2. **El CLI es para humanos**; el agente solo lo usa para `prepare`, `list`, `help`, `mcp-tools`, `mcp-routes`.
3. **No hay lógica duplicada**: el CLI consume el MCP, no ejecuta la lógica directamente.
4. **El MCP es el SSOT**: toda capacidad de dominio vive en tools MCP con `inputSchema`.

## Ejemplo: flujo de trabajo

```bash
# 1. Inicializar proyecto (CLI-only)
walker prepare /ruta/proyecto

# 2. Listar proyectos (MCP directo)
walker <project>/project_analyze '{"action":"list"}'

# 3. Crear sprint (MCP directo desde delta_tracking)
walker <project>/spring_analyze '{"action":"plan","goal":"Mi sprint","tasks":[...]}'

# 4. Ver dashboard (MCP directo desde delta_tracking)
walker <project>/spring_dashboard

# 5. Analizar código (MCP directo)
walker <project>/cpg_analyze '{"scope":"src","depth":"business"}'

# 6. Guardar conocimiento (MCP directo)
walker <project>/ckg_analyze '{"action":"save","nodes":[...]}'
```

## Analogía git

| Git | Walker |
|-----|--------|
| `git status` | `walker <project>/status` |
| `git commit` | `walker <project>/submit` |
| `git push` | `walker <project>/transition` |
| `git branch` | `walker <project>/delta_analyze` |
| `git log` | `walker <project>/kg_search` |
| `git diff` | `walker <project>/cpg_analyze` |
| `git sprint plan` | `walker <project>/spring_analyze` |
| `git project list` | `walker <project>/project_analyze` |

El MCP es el equivalente a la API interna de git; el CLI es el equivalente a los comandos `git` con formato humano.
