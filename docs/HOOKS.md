# Hooks de walker

Hooks de ciclo de vida con dos alcances: **global** (del usuario) y **proyecto**.
Un hook es un comando que se ejecuta en un evento, con el payload en stdin.

No confundir con *plugins*: los plugins aportan capacidad (tools, tareas,
adaptadores) y hoy viven en el composition root / `task_registry`. Los hooks
solo observan (o vetan, si son `blocking`) eventos del ciclo de vida.

## Dónde se declaran

| Alcance | Archivo | Confianza |
|---|---|---|
| Global | `<walker>/config/hooks/hooks.json` | del usuario, siempre corre |
| Proyecto | `<repo>/.cognitive/hooks/hooks.json` | requiere `hooks.trusted: true` |

El proyecto **pisa** al global por `id` y puede deshabilitar globales:

```json
{
  "hooks": [
    {"id": "cpg-sync", "event": "commit", "command": "\"$WALKER_PYTHON\" \"$WALKER_ROOT/scripts/sync_cpg_on_commit.py\"", "timeout_s": 300}
  ],
  "disabled": ["algún-hook-global"]
}
```

Para confiar en los hooks del proyecto (código arbitrario al abrir el repo):

```json
// <repo>/.cognitive/config.json
{"hooks": {"trusted": true}}
```

o `WALKER_HOOKS_TRUST=1` (útil en CI). Sin esto, los hooks del proyecto se
ignoran y queda un aviso en el log — nunca se ejecutan a ciegas.

## Eventos

| Evento | Cuándo | Emisor |
|---|---|---|
| `commit` | después de un commit de git | `post-commit` (instalado por `scripts/install_git_hooks.py`) |
| `cpg.indexed` | después de un sync del CPG | `scripts/sync_cpg_on_commit.py` |
| `task.after` | después de cada task del workflow | `TaskRegistry.execute` (hook inyectado por composition) |

Agregar eventos es barato: `HookBus.load(repo).emit("<evento>", {...})`.

## Contrato de ejecución

- El **payload** llega por stdin como JSON.
- Variables de entorno: `WALKER_EVENT`, `WALKER_PROJECT`, `WALKER_REPO`,
  `WALKER_ROOT`, `WALKER_PYTHON`.
- `cwd` = raíz del proyecto.
- `timeout_s` (default 10): al vencer, el hook falla con `timed_out: true`.
- `blocking: false` (default) → un fallo se loguea y el host sigue.
  `blocking: true` → solo tiene sentido en eventos `before`; el runner sale 1.

Un hook **nunca** puede tumbar al host: `HookBus.emit` no lanza, y los callers
(task registry, sync) ignoran el resultado salvo para loguear.

## Uso manual

```bash
# qué hooks correrían para un evento
python3 scripts/hooks_runner.py commit --list

# emitir un evento a mano
python3 scripts/hooks_runner.py task.after --payload '{"task": "run_tests", "ok": true}'

# desde otro repo
echo '{"sha": "abc"}' | python3 scripts/hooks_runner.py commit --repo ../otro-repo
```

## Diseño

- **Subproceso, no import.** Un plugin importado dentro del server MCP puede
  tumbarlo; ya hay una invariante de que un componente roto no arrastra al
  proceso (el watcher aborta con error accionable). Además hace los hooks
  agnósticos de lenguaje: el ecosistema del agente está en JS.
- **Merge igual que la config.** `paths._read_config_file` ya lee global →
  proyecto; los hooks usan la misma precedencia.
- **Trust explícito.** Clonar un repo no debe ejecutar código. El opt-in es por
  proyecto y queda auditable en `.cognitive/config.json`.
- **El hook de commit usa el update incremental.** Los ids del scan son
  determinísticos por (path, type, name) desde 2026-11-17, así que re-escanear
  un archivo conserva los ids de sus símbolos vivos y las aristas entrantes
  sobreviven: medido en walker, tres syncs seguidos dieron 14.038 → 14.041 →
  14.038 aristas (oscila ±3 por placeholders de llamadas externas, no acumula).
  Antes cada sync perdía ~13% de las aristas (14.825 → 12.823). `refresh=True`
  sigue disponible para forzar build completo.
- **Idempotencia del lado del hook.** Un hook que escribe estado (como
  `cpg-sync`) debe poder correr N veces sin efectos raros; el bus no lo
  garantiza por él.

## Paquetes instalables (plugins)

Un hook inline declara la suscripción en el `hooks.json` del que lo instala. Un
**paquete** se declara a sí mismo y el registro solo lo enlaza:

```
<paquete>/hook.json     ← manifiesto
<paquete>/main.py       ← entry
```

```json
{
  "id": "notify-slack",
  "version": "1.2.0",
  "description": "Avisa a un webhook cuando el CPG se reindexó",
  "walker": ">=3.0,<4",
  "events": ["cpg.indexed"],
  "entry": "main.py",
  "timeout_s": 15,
  "blocking": false
}
```

Cada campo tiene consecuencia: `events` se valida contra el catálogo (un typo
falla al instalar, no en runtime), `walker` se compara con
`fast_mcp.__version__` (un paquete incompatible se saltea con aviso, nunca rompe
el host) y `entry` tiene que existir.

### Instalar

```bash
# global: ~/.walker/hooks  (aplica a todos los proyectos)
python3 scripts/hooks_cli.py install ./notify-slack
python3 scripts/hooks_cli.py install github:user/notify-slack#v1.2.0
python3 scripts/hooks_cli.py install "git+https://git.example/x.git#main" --ref v1.2.0

# por proyecto: <repo>/.cognitive/hooks  (viaja con el repo; requiere trust)
python3 scripts/hooks_cli.py --project <repo> install ./notify-slack

# actualizar / sólo ver qué cambió
python3 scripts/hooks_cli.py update notify-slack --check
python3 scripts/hooks_cli.py update notify-slack

python3 scripts/hooks_cli.py list           # instalados + hooks activos
python3 scripts/hooks_cli.py enable|disable <id>
python3 scripts/hooks_cli.py remove <id>
python3 scripts/hooks_cli.py events         # catálogo + suscriptores
python3 scripts/hooks_cli.py doctor         # compatibilidad + eventos huérfanos
```

Registro: `<scope>/registry.json` con `id`, `version`, `source`, `source_type`,
`ref`, `commit`, `sha256`, `enabled`, `installed_at`.

### Pinning, update y hash

- Fuente git: se clona **shallow** en un temporal, se resuelve el commit
  (`rev-parse HEAD`) y recién ahí se copia. El registro guarda `ref` y `commit`.
- Si el `ref` es un **tag o un sha**, `update` no mueve el paquete: queda
  pinneado. Para subir de versión se instala con el ref nuevo
  (`install --force --ref v1.3.0`).
- Si el `ref` es una **rama**, `update` trae el commit nuevo y muestra el diff
  (`v1.0.0 → v1.1.0 · 91a2320 → f2cb0bd`); `--check` sólo informa.
- `sha256` es de manifiesto+entry. `doctor` lo recomputa contra el disco: si un
  archivo cambió después de instalar, lo reporta como
  `modificado en disco (c7395fa… != 7d4b678…)` y sale con rc=1. No es una
  firma criptográfica: detecta cambios, no autentica autor.

### Por qué no en `data/projects.json`

El manifiesto de proyectos es id → `repo_path`, lo reescribe `walker prepare`
y es de la máquina. Meter hooks ahí mezcla dos dominios, obliga a que el
registro preserve configuración ajena en cada alta, y rompe el modelo de
confianza: un hook de proyecto exige que el repo se declare confiable, mientras
que un hook en el registro de walker correría en todos los proyectos sin opt-in.

## Catálogo de eventos

Los eventos son un catálogo explícito (`shared_kernel/hooks/events.py`), no
strings libres. `emit` con un evento desconocido falla con la lista de válidos,
y un hook inline que declara uno inexistente aparece en `doctor`.

| Evento | Emisor | Payload |
|---|---|---|
| `commit` | `post-commit` instalado por `install_git_hooks.py` | `repo` |
| `cpg.indexed` | `scripts/sync_cpg_on_commit.py` | `project_id, mode, nodes, edges, files` |
| `task.after` | `TaskRegistry` (hook inyectado por composition) | `task, task_id, session_id, ok, error, chain_dir` |

Agregar un evento = agregarlo al catálogo + emitirlo desde quien lo produce.
