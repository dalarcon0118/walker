# ADR-WALKER-071 — MCP como superficie única + migración a FastMCP

## 0. Contexto y objetivo

Walker tenía **una sola superficie** en `protocols/cli/cli_interface/` que mezclaba
CLI y MCP, con el anti-patrón documentado en ADR-070: el "circuito CLI→MCP→REST→CLI".
El objetivo de este ADR es **migrar el MCP a FastMCP** como framework y **consolidar el
MCP como la superficie única de capacidades** (ADR-069 §8.1: FastMCP es el estándar de
facto del ecosistema MCP). El CLI actual se mantiene como **wrapper compartido** que
reusa el mismo `AppContainer`, sin convertirse en pieza autónoma.

**Decisiones del usuario (a lo largo del hilo):**
1. Mover todo el monorepo a `mcp/` (movimiento físico en filesystem).
2. Analizar si extraer un CLI autónomo tipo git era necesario → **NO**: el MCP ya
   expone `prepare/status/list/info` como tools, así que **solo el MCP basta**.
3. El `walker-client` autónomo con `.walker` global queda descartado por ahora
   (solo tendría sentido con uso humano-directo, y requeriría un registro global propio).

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

**Layout definitivo: SOLO MCP (el CLI no se extrae como pieza autónoma).**

```
walker/
  mcp/                       # MCP server: TODAS las capacidades (prepare/status/list/info + transformación)
    app/ features/ shared/ protocols/ config/ data/ schemas/ sqlite/
    main.py                   # composition root: FastMCP server
  protocols/cli/             # CLI actual, queda como WRAPPER compartido (NO se hace autónomo)
  bin/
    walker-mcp                # → mcp/ (FastMCP)
    walker                    # wrapper que reusa el mismo AppContainer (no pieza independiente)
```

### 2.1 Decisión: el CLI actual cubre lo que ya hace el MCP (no se extrae)

Tras analizar, el CLI de "lectura/registro" (`prepare`, `status`, `list`, `info`)
**ya existe como tools MCP** (`initialize_project`, `list_projects`, `show_dashboard`,
etc., expuestos vía `tool_catalog.list_tools()`), y el MCP `prepare` puede registrar
proyectos desde cero con `ProjectService.resolve_paths` + registro global, **sin**
necesitar un CLI previo para bootstrap.

Conclusión (decisión tomada): **solo el MCP basta**. El CLI actual se mantiene como
wrapper que comparte `AppContainer` con el MCP (mismo monorepo), **sin invertir** en
convertirlo en un paquete autónomo tipo git. La idea futura del `walker-client`
autónomo con `.walker` global queda descartada por ahora (no aporta valor mientras
el MCP ya expone las mismas tools; un CLI autónomo solo tendría sentido si el flujo
de uso fuera humano-directo sin agente, y requeriría su propio registro global).

> Nota: la carpeta `cli/` vacía creada en la raíz durante la exploración preliminar
> se **descarta** de la migración (no se extrae nada a ella). El CLI vive en
> `protocols/cli/` como hoy.

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
   **Descartar** la carpeta `cli/` vacía de la raíz (no aplica a esta decisión).
2. **Validar que `prepare/status/list/info` funcionan como tools MCP** end-to-end
   (bootstrap del primer proyecto vía MCP sin CLI previo) — cierra la redundancia.
3. **Migrar `mcp/` server a FastMCP**: nuevo `main.py` con `FastMCP`, registrar
   tools desde `tool_catalog.list_tools()`; mantener `serve_stdio` equivalente bajo
   FastMCP. Namespaces por dominio.
4. **Ajustar entry point**: `bin/walker-mcp` → `mcp/` con `sys.path` apuntando a `mcp/`.
   (El `bin/walker` CLI sigue reusando el mismo monorepo; no se convierte en paquete.)
5. **Tests**: boot MCP tools (incluido `prepare/status/list/info`); acyclicity +
   composition + uncaptured en verde.
6. **Actualizar ADR-016** (bounded context cli_interface) y ADR-070 (propuesta → implementación).

## 4. Reglas de diseño (non-negotiables)

- R-01: El CLI (`protocols/cli`) se mantiene como wrapper que comparte `AppContainer`;
  NO se convierte en paquete autónomo. El MCP es la superficie única de tools.
- R-02: `prepare/status/list/info` se sirven como **tools MCP** (ya existen:
  `initialize_project`, `list_projects`, `show_dashboard`, ...). No se duplican en un
  binario CLI separado.
- R-03: `mcp/` usa FastMCP como framework; tools thin, lógica en dominio.
- R-04: El dominio (`shared/core/engine`) nunca importa superficies (ADT-067, INV-W068-09).
- R-05: Toda la resolución de rutas usa `app/path_resolver` como **SSOT de rutas**
  (registro global `data/projects.json`).
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
    "deteccion": "boot MCP y test de import aislado desde mcp/"
  },
  {
    "fallo": "el primer proyecto no se puede preparar solo con el MCP (sin CLI previo)",
    "causa_raiz": "bootstrap dependia de un CLI separado",
    "mitigacion": "la tool MCP initialize_project resuelve paths via ProjectService.resolve_paths + registro global; no requiere CLI",
    "deteccion": "test e2e: prepare un proyecto nuevo via tool MCP y verificar que aparece en data/projects.json"
  },
  {
    "fallo": "FastMCP registra las 31 tools pero una tool falla porque perdio el DAG gating del McpServer propio",
    "causa_raiz": "reemplazo de adapter sin portar el gating",
    "mitigacion": "migracion incremental (a): FastMCP detrás del adapter; retirar el propio solo tras portar gating",
    "deteccion": "test e2e que llama a cada tool y verifica gating/publish_events"
  },
  {
    "fallo": "una tool MCP (status/prepare/list/info) no resuelve el proyecto correctamente al usar path_by_CWD vs path_global del registro",
    "causa_raiz": "divergencia en como se resuelve project_id vs project_root",
    "mitigacion": "toda resolucion de rutas via app/path_resolver como SSOT (R-05); el registro global (data/projects.json) resuelve project_root desde project_id",
    "deteccion": "test e2e: llama status/list via tool MCP y verifica que coincide con el project_root real del disco"
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

**DRAFT — plan de migración.** Layout en `mcp/` ya aplicado a nivel filesystem por el
usuario; el cli autónomo se **descartó** (solo MCP basta). Falta: normalizar el move
en git como rename, validar prepare/status/list/info como tools MCP, migrar el server
a FastMCP, ajustar `bin/walker-mcp` y correr tests. `failure_scenarios` presente. Pendiente de
refinar el orden de pasos y aprobar.

## 7. Cross-references

- ADR-070: propuesta lee-vs-transforma (base conceptual de esta migración).
- ADR-069 §8: estudio del ecosistema MCP real (FastMCP, mcp_shared, dominio-centro).
- ADR-016: bounded context `cli_interface` (a actualizar al final).
- ADR-045/062: composition root y puertos (FastMCP se cablea en main.py).
- ADR-067: engine anémico — el dominio no importa superficies.
- ADR-069 T4: `app/path_resolver` puede subir a `shared/` como utilidad pura.
