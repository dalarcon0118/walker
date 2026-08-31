# ADR-WALKER-070 — Separación CLI (lectura local) vs MCP (transformación server)

## 0. Contexto

Walker expone hoy dos caras de la misma moneda en `protocols/cli/cli_interface/`:
un **CLI** (`walker <verbo> <ruta>` tipo REST) y un **MCP** (`protocols/cli/cli_interface/mcp.py`
/ `protocols/mcp/mcp_server.py`). El propio código describe un **anti-patrón**:

> "el tool MCP 'status' internamente: el circuito CLI->MCP->REST->CLI"

Este circuito (el CLI llama al MCP que llama de vuelta al REST del mismo CLI) es el
síntoma de que **dos responsabilidades distintas están mezcladas** en el mismo bloque:

1. **Leer estado** desde el path local (filesystem + CKG/Delta en disco).
2. **Transformar estado** vía workflow/tasks (submit, transition, advance, task).

Esto conecta con el estudio del ecosistema MCP documentado en ADR-069 §8: los MCP
servers reales separan **tools thin** (que cablean en un composition root) de la
**lógica de dominio**, y el "entrada de cliente" (CLI) es un consumidor ligero, no
un cliente de su propio server.

## 1. Pregunta del usuario

> "El walker tiene una parte cli y otra mcp, quizás deberíamos dividirlo en dos
> partes: el MCP migrarlo a la arquitectura de FastMCP, y el CLI a un app-cli,
> el CLI solo con comandos como `prepare` y `status` que lean el path local, y el
> MCP que se integra con el path global y crea el resto de las cosas. ¿Qué crees?"

## 2. Diagnóstico verificado (evidencia en el código)

### 2.1 Comandos que LEEN path local (candidatos a app-cli)

`protocols/cli/cli_interface/application/handlers.py` usa `app.path_resolver` para
resolver directorios locales, sin depender del circuito de transformación:

- `handle_status` → `get_sprint_dir`, `get_walker_root` (líneas 171/183)
- `handle_prepare` → `get_walker_root` (183) — init/registro de proyecto
- `handle_list_projects` / `_resolve_project_from_cwd` → `get_walker_root` + `os.getcwd()`
- `handle_project_info` → `get_walker_root` (318)

Estos son **lectores**: resuelven el project desde CWD y devuelven salud/info del
directorio local. No mutan workflow.

### 2.2 Comandos que TRANSFORMAN estado (candidatos a MCP server)

- `submit` / `advance` / `transition` (`_register_workflow_controller`) → mutan el
  workflow vía `handle_submit`/`handle_transition`.
- `task/<name>` / `:taskName` (`_register_task_controller`) → ejecutan tasks.
- `mcp-tools` / `mcp-routes` (`_register_system_controller`) → introspección registries.
- `mcp-config` → generar config portable para agente IA.

### 2.3 El acoplamiento al MCP es transversal

13 de 15 archivos de `cli_interface` importan `mcp*`:
`__init__`, `mcp`, `engine_mcp`, `router`, `presenter`, `handlers`,
`mcp_client_holder`, `mcp_client`, `mcp_config_service`, `reloader`, `serializer`,
`output`, `errors`. El CLI no es hoy un programa ligero: es un cliente de su propio
MCP.

## 3. Decisión

**Separar Walker en dos superficies con dos responsabilidades y dos transportes:**

| Superficie | Rol | Comandos | Transporte | Path |
|---|---|---|---|---|
| **app-cli** | **Leer** estado local | `prepare`, `status`, `list`, `info` | CLI directo (lee dominio desde disco, **sin** circuito MCP) | local (`os.getcwd()` → `app.path_resolver`) |
| **MCP server** | **Transformar** estado + exponer tools | `submit`, `transition`, `advance`, `task/<name>`, `mcp-config` | FastMCP (migrar de `mcp_server.py` a la arquitectura FastMCP, ADR-069 §8.1) | global (composition root + path global) |

El **principio separador** es: **leer es local y ligero; transformar es server y global.**

### 3.1 Qué NO cambia

- El dominio del engine (`shared/core/engine`) sigue siendo SSOT; ninguna de las dos
  superficies reconstruye dominio (ADR-045/062/065).
- `app/path_resolver.py` sigue siendo SSOT de rutas; el app-cli lo consume (o sube a
  `shared/` como utilidad pura, ver T4 en ADR-069).
- El composition root (`app/composition/registry.py`) cablea ambos; no hay dominio
  nuevo.

### 3.2 Efecto esperado clave

**Elimina el circuito CLI→MCP→REST→CLI.** El app-cli deja de ser un cliente de su
propio MCP: `status`/`prepare` leen el dominio directamente del disco y responden
sin levantar el server. El MCP queda como la única superficie de transformación.

## 4. Reglas de diseño

- R-01: `app-cli` NO importa `protocols/mcp` ni `mcp_client` (rompe el circuito).
- R-02: `app-cli` NO ejecuta tasks de transformación; solo lee (status/info/list/prepare).
- R-03: MCP server expone las tools de transformación con FastMCP; las carpeta por
  namespace/dominio (patrón ADR-069 §8.2 — mcp_shared).
- R-04: El dominio (`shared/core/engine`) sigue sin importar a ninguna superficie
  (engine anémico, ADR-067, INV-W068-09).
- R-05: `mcp-config`/`mcp-tools`/`mcp-routes` quedan en el MCP (autodoc del server),
  no en el CLI ligero.

## 5. failure_scenarios

```json
[
  {
    "fallo": "app-cli importa mcp_client para leer status y reintroduce el circuito CLI->MCP->REST->CLI",
    "causa_raiz": "acoplamiento residual durante la migración",
    "mitigacion": "gate CI: grep 'from protocols.mcp|mcp_client' en app-cli = vacío",
    "deteccion": "test de import aislado de app-cli (no levanta MCP); grep gate"
  },
  {
    "fallo": "status en app-cli lee el path local pero el workflow estaba escrito bajo el path GLOBAL del MCP",
    "causa_raiz": "divergencia de rutas entre las dos superficies",
    "mitigacion": "ambas consumen app/path_resolver como SSOT; la resolución project-by-CWD vs project-by-id se normaliza y se documenta",
    "deteccion": "test e2e que corre status via app-cli y comparar con el workflow real del disco"
  },
  {
    "fallo": "al eliminar el circuito, prepare pierde la capacidad de registrar el proyecto ante el server",
    "causa_raiz": "dependencia implicita de prepare del path global del server",
    "mitigacion": "prepare en app-cli registra SOLO el proyecto local (filesystem); el MCP lo ve cuando se levanta leyendo el mismo PathResolver; no hay registro remoto",
    "deteccion": "test e2e: prepare local + boot MCP y verificar que el proyecto aparece sin registro manual"
  },
  {
    "fallo": "mcp-config quedaba solo en el CLI y un agente IA no puede descubrir las tools del server",
    "causa_raiz": "reparto incorrecto de la autodoc entre superficies",
    "mitigacion": "mcp-config/mcp-tools/mcp-routes viven en el MCP server (R-05); el app-cli ofrece solo routes de lectura",
    "deteccion": "boot MCP y verificar que mcp-tools lista todas las tools; grep app-cli no exporta mcp-tools"
  }
]
```

## 6. Estado / aprobación

**DRAFT — propuesta de diseño.** No se toca código. El usuario eligió "documentar la
propuesta en ADR". Pendiente de revisar si se traza el plan concreto de división por
handler (acción opcional) o se implementa.

## 7. Cross-references

- ADR-016: bounded context `cli_interface` (Protocol) — esta separación refina su distribución.
- ADR-069 §8: estudio del ecosistema MCP real (FastMCP, mcp_shared, dominio-centro) — referente.
- ADR-045/062: composition root y puertos/DI — ambas superficies lo respetan.
- ADR-067: engine anémico — el dominio no importa a las superficies.
- `app/path_resolver.py`: SSOT de rutas que ambas superficies consumen.
