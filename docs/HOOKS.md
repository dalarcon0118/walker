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
- `sha256` es el digest del **árbol completo** (`tree-v1`): todos los archivos,
  ordenados por path relativo, con el nombre adentro del hash. El digest
  anterior cubría solo manifiesto+entry, así que un `helper.py` importado por el
  entry —o una dependencia maliciosa— no se verificaba. Se excluyen `.git`,
  caches y symlinks (un paquete con symlinks se rechaza al instalar).
- `doctor` recomputa el digest contra el disco: si cualquier archivo cambió
  después de instalar, lo reporta como `modificado en disco (dcb9488… !=
  02d2638…)` y sale con rc=1.

### Por qué no en `data/projects.json`

El manifiesto de proyectos es id → `repo_path`, lo reescribe `walker prepare`
y es de la máquina. Meter hooks ahí mezcla dos dominios, obliga a que el
registro preserve configuración ajena en cada alta, y rompe el modelo de
confianza: un hook de proyecto exige que el repo se declare confiable, mientras
que un hook en el registro de walker correría en todos los proyectos sin opt-in.

### Integridad: verificar contra un hash publicado

```bash
# el autor publica el digest (o lo calculás vos sobre el código ya revisado)
python3 scripts/hooks_cli.py digest github:user/notify-slack#v1.2.0

# instalar exigiendo ese contenido exacto
python3 scripts/hooks_cli.py install github:user/notify-slack#v1.2.0 \
  --sha256 02d2638de8adf97ae265e9db7c02f99cf3a02a8d97575f21751d697ad6b356b0

# actualizar exigiendo el digest de la versión nueva
python3 scripts/hooks_cli.py update notify-slack --sha256 <hex>
```

Si el digest no coincide, `install` aborta **antes de copiar** y muestra ambos
hashes. Qué garantiza y qué no: garantiza que el código instalado es exactamente
el que corresponde a ese hash; **no** garantiza autoría (no hay firma), así que
el hash tiene que venir por un canal distinto del repo del plugin (release notes,
un mensaje del autor, un `sha256sum` publicado). Es integridad, no autenticación.

## Lenguaje del entry

Un hook es un **comando**, no un import: el bus ejecuta un subproceso. Eso
permite cualquier lenguaje y mantiene el aislamiento (un hook no puede tumbar
el server MCP; el sandbox aplica igual).

| `entry` | Cómo corre | Necesita |
|---|---|---|
| `main.py` | `"$WALKER_PYTHON" "<pkg>/main.py"` | nada: sin shebang, sin permiso de ejecución |
| `main.sh` | `"<pkg>/main.sh"` directo | shebang + bit de ejecución |
| `main.js` | `"<pkg>/main.js"` directo | shebang + bit de ejecución |

Un entry que no es `.py` se ejecuta directo, así que sin `chmod +x` falla en
runtime con `rc=126` y el error aparece lejos del manifiesto. Por eso
`read_manifest` (y por lo tanto `install`) exige shebang y bit de ejecución
**antes** de copiar: el fallo se ve donde se cometió.

Los hooks inline (`hooks.json`) son shell puro: `"command": "jq -r .task | ..."`
funciona sin manifiesto. El payload llega por stdin y `WALKER_EVENT`/`WALKER_REPO`
por entorno.

Para hooks Python, el entorno incluye `PYTHONPATH` con el root de walker
prefijado (y el valor heredado del usuario, sin duplicar). Así `import fast_mcp`
apunta al repo aunque el intérprete no tenga el paquete instalado; es inocuo para
los hooks que no son Python. Los módulos hermanos del paquete (`import helper`)
funcionan sin configuración porque Python agrega el directorio del script a
`sys.path`.

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

### Firmas: integridad y autoría

El digest solo no prueba autoría. Un paquete puede firmarse con ed25519 y
verificarse contra una lista de claves confiables del usuario:

```bash
# autor: genera el par de claves y firma el paquete
python3 -m scripts.hooks_cli keys --help          # ver subcomandos
python3 scripts/hooks_cli.py sign ./notify-slack --key david.pem --id david
# (el par se genera con generate_keypair(".", "david") de
#  fast_mcp.shared_kernel.hooks.signature)

# usuario: confía en la clave (una sola vez) e instala exigiendo firma
python3 scripts/hooks_cli.py keys add david --public-key david.pub
python3 scripts/hooks_cli.py keys list
python3 scripts/hooks_cli.py install github:user/notify-slack#v1.2.0 --require-signature
```

Se firma el **digest del árbol**, no el archivo: cualquier cambio posterior en
cualquier archivo invalida la firma (`hook.sig.json` queda fuera del digest).
Una firma de una clave que no está en `~/.walker/trusted_keys.json` se rechaza
con el id de la clave, para que decidas explícitamente si confiás en ella.
`doctor` muestra `firmado por <id>` o `sin firma`.

Límites: la firma prueba que el contenido lo firmó esa clave; no prueba que la
clave sea de quien dice ser (eso es gestión de claves, fuera de alcance), ni
protege si la clave privada se filtra.

#### Revocación y rotación

```bash
python3 scripts/hooks_cli.py keys revoke david --reason "privada filtrada"
python3 scripts/hooks_cli.py keys list
python3 scripts/hooks_cli.py keys rotate david --public-key david-nueva.pub
```

- `revoke` marca la clave con fecha y motivo. Una firma de una clave revocada
  **no instala ni actualiza**: el error dice cuándo se revocó y por qué. Los
  paquetes ya instalados aparecen en `doctor` como `firma inválida: … REVOCADA`.
- `rotate` cambia el material conservando el id y registra `rotated_from`
  (fingerprint corto del anterior). Las firmas viejas dejan de validar, que es
  el punto; hay que re-firmar con la privada nueva.
- Rotar **no** limpia una revocación: si la clave se revocó por compromiso,
  `rotate` la deja revocada y avisa. Para volver a confiar hay que
  `keys remove` + `keys add` con la clave nueva, que es una decisión explícita.
- Un id ya existente no se puede `add` de nuevo (para no revivir una clave
  revocada sin querer): el CLI pide `rotate` o `remove`.

### Permisos

El manifiesto declara lo que el hook necesita; el vocabulario es cerrado (un
typo falla al instalar):

| Permiso | Efecto |
|---|---|
| `env:all` | hereda TODO el entorno; sin esto, el hook corre con PATH/HOME/LANG/TMPDIR/WALKER_* |
| `fs:repo` | puede **escribir** en el repo (leerlo es implícito: sin esto no puede reindexar nada) |
| `fs:tmp` | puede leer/escribir en el temporal del sistema |
| `fs:home` | puede leer/escribir en `$HOME` (git config, ssh, caches) |
| `fs:any` | sin restricción de filesystem (escape hatch explícito) |
| `network` | permite red; sin esto, en macOS se deniega |
| `exec` | declara que lanza subprocesos (informativo: no se puede impedir) |

En macOS el baseline (sin `fs:any`) es un perfil de `sandbox-exec` que:

1. **deniega leer `$HOME`**, salvo los subárboles que el hook necesita para
   arrancar: el repo, `<walker>`, `~/.walker/hooks` y los prefijos de Python;
2. **deniega escribir** salvo `/dev/null` y lo que habiliten `fs:repo`,
   `fs:tmp` o `fs:home`.

Verificado en test: un hook sin `fs:home` no puede leer `~/.zshrc` (mismo camino
que `~/.ssh/id_rsa`), y uno sin `fs:repo` no puede escribir en el repo. El
entorno mínimo es la parte multiplataforma: un hook no ve
`AWS_SECRET_ACCESS_KEY` ni `GITHUB_TOKEN` salvo `env:all`. En Linux/Windows no
hay sandbox sin privilegios: filesystem y red quedan como intención declarada.

Límite honesto: esto frena accidentes y código malicioso oportunista, no a un
atacante con control del binario y de la clave de firma. `exec` no se puede
impedir porque el hook **es** un proceso; el límite de seguridad sigue siendo
instalar (y firmar).
