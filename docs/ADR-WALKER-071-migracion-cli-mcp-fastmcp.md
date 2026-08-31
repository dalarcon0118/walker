# ADR-WALKER-071 — Migración definitiva: CLI y MCP separados (FastMCP)

## 0. Contexto y objetivo

Walker tenía **una sola superficie** en `protocols/cli/cli_interface/` que mezclaba
CLI y MCP, con el anti-patrón documentado en ADR-070: el "circuito CLI→MCP→REST→CLI".
El objetivo de este ADR es **dejar separados de manera definitiva** el CLI (lectura
local) y el MCP (transformación/global), y **migrar el MCP a FastMCP** como framework
(ADR-069 §8.1: FastMCP es el estándar de facto del ecosistema MCP).

**Decisión de layout ya aplicada por el usuario (movimiento físico en filesystem):**

```
walker/
  cli/     # SOLO lo del CLI (app-cli ligero: prepare/status/list/info)
  mcp/     # TODO lo del MCP (app, features, shared, protocols, data, config, schemas, sqlite)
```

> Nota de higiene: `mcp/sqlite:` (con `:`) es un folder accidental que contiene una
> ruta pytest temporal (`/private/var/folders/.../pytest-65`); no es path de app.
> Conviene eliminarlo o ignorarlo en el commit de la migración.

## 1. Diagnóstico verificado

### 1.1 Movimiento físico sin `git mv` → estado git

El monorepo completo se movió a `mcp/` **a nivel de filesystem**, pero NO con
`git mv`. Por eso git lo ve así:

- Raíz: `app/`, `features/`, `shared/`, `protocols/`, `config/`, `data/`, `schemas/`,
  `prompts/` → **`D` (deleted)**.
- `mcp/` → **`??` (untracked)**, con 560 entradas.
- `cli/` → **vacío** (creado, sin contenido).

**Consecuencia para la migración:** para que git registre el movimiento como `R100`
(rename, preservando historial) hay que hacer `git mv` (o `git add -A` + `git rm`),
NO un simple copy. El orden importa para no perder el historial.

### 1.2 Imports internos dentro de `mcp/`

Los imports dentro de `mcp/` **NO** usan prefijo `mcp.`; siguen como:
`from app.`, `from features.`, `from shared.`, `from protocols.`,
`from config.`, `from data.`.

Esto implica que **`mcp/` debe estar en `sys.path` como la raíz del paquete**
(no la raíz de `walker/`). `bin/walker-mcp` ya hace exactamente esto:

```python
_WALKER_ROOT = os.path.dirname(_SCRIPT_DIR)  # bin/../ 
sys.path.insert(0, _WALKER_ROOT)             # raíz de walker
from protocols.mcp.mcp_adapter import serve_stdio
```

(Nota: hoy `_WALKER_ROOT` = raíz de `walker/`, porque `protocols` estaba en raíz.
Tras el move, `protocols` está en `mcp/protocols`, así que el entry point deberá
apuntar a `mcp/` como raíz de path, no a la raíz de walker.)

### 1.3 El MCP server actual es un adapter propio (no FastMCP)

`mcp/protocols/mcp/mcp_server.py` (250 líneas) define un `McpServer` propio:
`register`, `tool()`, `register_tools`, `resource()`, `prompt()`, `compose()`,
`list_tools`, `list_resources`, `list_prompts`, `call_tool`, dispatch. Se monta
via `serve_stdio()` en `bin/walker-mcp`.

Esto es un **framework MCP propio** que configura `tools`/`resources`/`prompts`
manualmente. El objetivo es reemplazarlo por **FastMCP** (`@mcp.tool()`, autogen de
schema, transporte stdio/HTTP). El `McpServer` propio puede quedar como adapter
interno (portador del DAG gating y _publish_events) o descartarse en favor de FastMCP.

### 1.4 El CLI hoy NO es ligero

13 de 15 archivos de `protocols/cli/cli_interface/` importan `mcp*`
(`mcp_client`, `mcp_client_holder`, `mcp_config_service`, `reloader`...). El CLI es
un cliente de su propio MCP. En la separación definitiva, `cli/` debe ser un
programa ligero que lee el dominio del disco sin levantar el server.

## 2. Decisión

**Layout definitivo de dos superficies independientes:**

```
walker/
  cli/                       # app-cli: lectura local (dominio desde disco)
    walker                    # binario: prepare, status, list, info
    ... (solo lo que lee path local, sin imports de mcp)
  mcp/                       # MCP server: transformación + tools
    app/ features/ shared/ protocols/ config/ data/ schemas/ sqlite/
    main.py                   # composition root: FastMCP server
  bin/
    walker                    # → cli/ (read)
    walker-mcp                # → mcp/ (FastMCP transform)
```

### 2.1 Principio separador (heredado de ADR-070)

**Leer es local y ligero; transformar es server y global.**

| Superficie | Rol | Comandos/tools | Transporte |
|---|---|---|---|
| `cli/` | Leer estado local | `prepare`, `status`, `list`, `info` | CLI directo, **sin** circuito MCP |
| `mcp/` | Transformar + exponer tools | `submit`, `transition`, `advance`, `task/*`, `mcp-config/tools/routes` | **FastMCP** (stdio/HTTP) |

### 2.2 Migración a FastMCP

El `mcp/` server pasa a arquitectura FastMCP (patrón ADR-069 §8.2):
- Un `main.py` (composition root) construye `FastMCP("walker")`.
- Las tools se registran con `@mcp.tool()` desde los adapters (tools thin,
  lógica en domain/application).
- Namespaces por dominio (project, workflow, task, sprint, intelligence...).
- Transporte: stdio para agents locales, Streamable HTTP opcional.

### 2.3 ¿Qué pasa con el `McpServer` propio actual?

Opciones (a refinar en el plan):
- **(a)** Mantenerlo como adapter interno **detrás de FastMCP** si los tests de
  DAG gating / `_publish_events` dependen de él (bajo riesgo, incremental).
- **(b)** Reemplazarlo por completo por FastMCP y portar el gating a decoradores
  (más limpio, refactor mayor).

Recomendado: **(a) incremental** primero — exponer las mismas tools vía FastMCP
sin romper los tests, y después evaluar retirar el adapter propio.

## 3. Pasos de la migración (plan provisional)

1. **Normalizar el move en git** (preservar historial):
   `git mv app features shared protocols config data schemas prompts → mcp/`
   (o confirmar el estado actual y comitear como move si ya está físicamente movido).
2. **Crear `cli/` funcional**: mover `protocols/cli/cli_interface` a `cli/`,
   quedándose SOLO con los handlers de lectura (`status`, `prepare`, `list`, `info`)
   y `app/path_resolver` (o subirlo a `shared/` — T4 de ADR-069).
   **Gate:** `grep "from protocols.mcp|mcp_client" cli/` = vacío.
3. **Migrar `mcp/` server a FastMCP**: nuevo `main.py` con `FastMCP`, registrar
   tools desde el registry; mantener `serve_stdio` equivalente bajo FastMCP.
4. **Ajustar entry points**: `bin/walker` → `cli/`, `bin/walker-mcp` → `mcp/`
   con `sys.path` apuntando a `mcp/`.
5. **Tests**: boot MCP 31 tools; app-cli `status`/`prepare` sin levantar server;
   acyclicity + composition + uncaptured en verde.
6. **Actualizar ADR-016** (bounded context cli_interface) y ADR-070 (propuesta → implementación).

## 4. Reglas de diseño (non-negotiables)

- R-01: `cli/` NO importa `mcp_client` ni `protocols.mcp` (rompe el circuito).
- R-02: `cli/` solo lee (status/info/list/prepare); NO ejecuta tasks de transformación.
- R-03: `mcp/` usa FastMCP como framework; tools thin, lógica en dominio.
- R-04: El dominio (`shared/core/engine`) nunca importa superficies (ADT-067, INV-W068-09).
- R-05: Ambas superficies consumen el mismo `app/path_resolver` (o `shared/`) como
  **SSOT de rutas** para no divergir local/global.
- R-06: El move se registra en git como **rename** (historial), no como copy.

## 5. failure_scenarios

```json
[
  {
    "fallo": "el move físico sin git mv hace que git registre delete+untracked y se pierda el historial",
    "causa_raiz": "filesystem move no seguido por git",
    "mitigacion": "usar git mv (o git add -A + git rm --cached) para que git detecte R100",
    "deteccion": "git status muestra 'R' en vez de 'D' + '??'; git log --follow <file> conserva el historial"
  },
  {
    "fallo": "al mover protocols/ a mcp/, los imports 'from protocols.' dejan de resolver",
    "causa_raiz": "el sys.path apuntaba a la raiz de walker, no a mcp/",
    "mitigacion": "bin/walker-mcp y main.py insertan mcp/ en sys.path; verificar boot",
    "deteccion": "boot MCP (31 tools) y test de import aislado desde mcp/"
  },
  {
    "fallo": "cli/ reintroduce el circuito importando mcp_client para leer status",
    "causa_raiz": "acoplamiento residual durante la migracion",
    "mitigacion": "gate CI: grep 'from protocols.mcp|mcp_client' cli/ = vacio",
    "deteccion": "grep gate en CI + test de import aislado de cli/ (no levanta server)"
  },
  {
    "fallo": "FastMCP registra las 31 tools pero una tool falla porque perdio el DAG gating del McpServer propio",
    "causa_raiz": "reemplazo de adapter sin portar el gating",
    "mitigacion": "migracion incremental (a): FastMCP detrás del adapter; retirar el propio solo tras portar gating",
    "deteccion": "test e2e que llama a cada tool y verifica gating/publish_events"
  },
  {
    "fallo": "status en cli/ lee path local pero el workflow fue escrito bajo el path GLOBAL de mcp/",
    "causa_raiz": "divergencia de rutas entre superficies",
    "mitigacion": "ambas usan app/path_resolver (R-05) SSOT; normalizar project-by-CWD vs project-by-id",
    "deteccion": "test e2e: corre status via cli/ y compara con el workflow real del disco de mcp/"
  },
  {
    "fallo": "mcp/sqlite: (basura accidental) se commitea y ensucia el repo",
    "causa_raiz": "folder con ':' creado durante el move (ruta pytest temporal)",
    "mitigacion": "eliminar/ignorar mcp/sqlite: antes del commit",
    "deteccion": "ls mcp/ no muestra 'sqlite:' o esta en .gitignore"
  }
]
```

## 6. Estado / aprobación

**DRAFT — plan de migración.** Layout ya aplicado a nivel filesystem por el usuario
(`cli/` + `mcp/`); falta normalizar en git, crear `cli/` funcional, migrar el server
a FastMCP y ajustar entry points/tests. `failure_scenarios` presente. Pendiente de
refinar el orden de pasos y aprobar.

## 7. Cross-references

- ADR-070: propuesta lee-vs-transforma (base conceptual de esta migración).
- ADR-069 §8: estudio del ecosistema MCP real (FastMCP, mcp_shared, dominio-centro).
- ADR-016: bounded context `cli_interface` (a actualizar al final).
- ADR-045/062: composition root y puertos (FastMCP se cablea en main.py).
- ADR-067: engine anémico — el dominio no importa superficies.
- ADR-069 T4: `app/path_resolver` puede subir a `shared/` como utilidad pura.
