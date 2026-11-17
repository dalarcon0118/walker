"""shared_kernel/hooks/bus.py — hooks de ciclo de vida con dos scopes.

Modelo (2026-11-17): global (`<walker>/config/hooks/hooks.json`) y proyecto
(`<repo>/.cognitive/hooks/hooks.json`). Los del proyecto pisan a los globales
por `id` y pueden deshabilitarlos con `"disabled": [...]`, igual que la config
en cascada que ya existe en `paths.py`.

Decisiones:
- **Subproceso, no import.** Un plugin importado dentro del server MCP puede
  tumbarlo; walker ya tiene la invariante de que un componente roto no arrastra
  al proceso (el watcher aborta con error accionable). Además así los hooks son
  agnósticos de lenguaje (la mitad del ecosistema real está en JS).
- **Nunca lanza.** `emit()` devuelve resultados; el caller decide qué hacer con
  un hook que falló.
- **Trust.** Un hook de proyecto es ejecución de código arbitrario al abrir un
  repo ajeno; solo corre si el proyecto lo declara confiable en
  `.cognitive/config.json` (`{"hooks": {"trusted": true}}`) o si se exporta
  `WALKER_HOOKS_TRUST=1`. Los globales son del usuario y siempre corren.

Formato de `hooks.json`:

    {
      "hooks": [
        {"id": "cpg-sync", "event": "commit", "command": "python3 script.py",
         "timeout_s": 60, "blocking": false}
      ],
      "disabled": ["hook-global-que-no-quiero"]
    }

El payload del evento llega por **stdin** como JSON; además el proceso recibe
`WALKER_EVENT`, `WALKER_PROJECT`, `WALKER_REPO`, `WALKER_ROOT` y
`WALKER_PYTHON`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fast_mcp.shared_kernel.hooks.events import (
    is_known,
    known_events,
)

logger = logging.getLogger(__name__)

GLOBAL_HOOKS_REL = Path("config") / "hooks" / "hooks.json"
PROJECT_HOOKS_REL = Path(".cognitive") / "hooks" / "hooks.json"
PROJECT_CONFIG_REL = Path(".cognitive") / "config.json"
MAX_OUTPUT = 500


@dataclass(frozen=True)
class HookSpec:
    id: str
    command: str
    event: str = ""                     # hooks inline (un solo evento)
    events: tuple[str, ...] = ()        # paquetes instalados (varios eventos)
    timeout_s: int = 10
    blocking: bool = False
    source: str = "global"              # global | project | package:global | package:project
    version: str = ""                   # versión del paquete (si aplica)
    permissions: tuple[str, ...] = ()   # fs:repo, exec, network, env:all, ...

    def matches(self, event: str) -> bool:
        return event in (self.events or (self.event,))

    def subscribed_events(self) -> tuple[str, ...]:
        return self.events or (self.event,)


@dataclass
class HookResult:
    hook_id: str
    event: str
    ok: bool
    rc: int | None = None
    duration_s: float = 0.0
    timed_out: bool = False
    skipped: bool = False
    error: str = ""
    stdout: str = ""
    stderr: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "hook": self.hook_id, "event": self.event, "ok": self.ok,
            "rc": self.rc, "duration_s": self.duration_s,
            "timed_out": self.timed_out, "skipped": self.skipped,
            "error": self.error,
        }


def _walker_root() -> Path:
    try:
        from fast_mcp.shared_kernel.paths import walker_root

        return Path(walker_root())
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hooks: %s ilegible (%s), se ignora", path, exc)
        return {}


_ENV_ALLOWLIST = {
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TERM",
    "TMPDIR", "SHELL", "USER", "LOGNAME", "PWD",
    # no es un secreto: un hook Python puede depender de paths propios
    "PYTHONPATH",
}


def _scrub_env(env: dict[str, str], permissions: tuple[str, ...]) -> dict[str, str]:
    """Entorno mínimo: sin tokens del usuario, salvo que se pida `env:all`.

    Un hook no necesita `AWS_SECRET_ACCESS_KEY` ni `GITHUB_TOKEN` para
    reindexar el CPG. `env:all` es explícito porque hereda secretos.
    """
    if "env:all" in permissions:
        return dict(env)
    return {
        key: value
        for key, value in env.items()
        if key in _ENV_ALLOWLIST or key.startswith("WALKER_")
    }


def _sblex(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _fs_profile(
    permissions: tuple[str, ...],
    *,
    repo: str,
    walker_root: str,
    home: str | None = None,
    tmpdir: str | None = None,
    python_prefixes: tuple[str, ...] = (),
) -> str:
    """Perfil SBPL: lectura acotada a repo/walker/hooks/python, escritura opt-in.

    Sin `fs:any`, `$HOME` queda denegado salvo los subárboles que el hook
    necesita para arrancar (repo, walker, hooks instalados, prefijos de Python).
    Escribir requiere `fs:repo`, `fs:tmp` o `fs:home`: un hook sin permisos
    puede leer el repo pero no modificarlo.
    """
    if "fs:any" in permissions:
        return ""
    import tempfile

    home_path = Path(home or Path.home())
    tmp = Path(tmpdir or tempfile.gettempdir())
    read_allow = [repo, walker_root, str(Path.home() / ".walker"), *python_prefixes]
    parts = ["(version 1)", "(allow default)"]
    if "fs:home" not in permissions:
        parts.append(f'(deny file-read* (subpath "{_sblex(home_path)}"))')
        parts.append("(allow file-read-metadata)")
        for path in read_allow:
            if path:
                parts.append(f'(allow file-read* (subpath "{_sblex(path)}"))')
    parts.append("(deny file-write*)")
    if "fs:repo" in permissions:
        parts.append(f'(allow file-write* (subpath "{_sblex(repo)}"))')
    if "fs:tmp" in permissions:
        parts.append(f'(allow file-write* (subpath "{_sblex(tmp)}"))')
        parts.append(f'(allow file-read* (subpath "{_sblex(tmp)}"))')
    if "fs:home" in permissions:
        parts.append(f'(allow file-write* (subpath "{_sblex(home_path)}"))')
        parts.append(f'(allow file-read* (subpath "{_sblex(home_path)}"))')
    parts.append('(allow file-write* (literal "/dev/null"))')
    parts.append('(allow file-write* (literal "/dev/tty"))')
    return "".join(parts)


def _with_pythonpath(env: dict[str, str], walker_root: str | Path) -> dict[str, str]:
    """Prefija `PYTHONPATH` con el root de walker.

    Un entry `.py` corre con `$WALKER_PYTHON`, pero `import fast_mcp` solo
    resolvía si ese intérprete tenía walker instalado (venv editable). Con el
    root en PYTHONPATH, el import apunta al repo pase lo que pase con la
    instalación. Es inocuo para hooks que no son Python.
    """
    root = str(walker_root)
    inherited = [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p]
    env = dict(env)
    env["PYTHONPATH"] = os.pathsep.join([root, *[p for p in inherited if p != root]])
    return env


def _wrap_command(
    command: str,
    permissions: tuple[str, ...],
    *,
    repo: str | Path = ".",
    walker_root: str | Path = ".",
    home: str | None = None,
) -> str:
    """Aplica el sandbox (macOS) según permisos. Sin mecanismo → comando tal cual.

    Un solo `sandbox-exec` con un perfil que combina filesystem y red: dos
    invocaciones anidadas no se componen bien (la interna pierde reglas).
    """
    if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
        return command

    prefixes = [sys.prefix, getattr(sys, "base_prefix", sys.prefix),
                str(Path(sys.executable).resolve().parent)]
    fs_profile = _fs_profile(
        permissions,
        repo=str(repo),
        walker_root=str(walker_root),
        home=home,
        python_prefixes=tuple(dict.fromkeys(prefixes)),
    )
    clauses: list[str] = []
    if fs_profile:
        clauses.append(fs_profile.replace("(version 1)", "", 1))
    if "network" not in permissions:
        clauses.append("(deny network*)")
    if not clauses:
        return command

    import shlex

    profile = "(version 1)" + "".join(clauses)
    # `sh -c` adentro: sandbox-exec ejecuta el primer token como binario; sin
    # esto se pierden builtins, redirecciones y pipes del comando original.
    return (
        f"sandbox-exec -p {shlex.quote(profile)} sh -c " + shlex.quote(command)
    )


def _parse_specs(data: dict[str, Any], source: str) -> list[HookSpec]:
    specs: list[HookSpec] = []
    for raw in data.get("hooks") or []:
        if not isinstance(raw, dict):
            continue
        event = str(raw.get("event", "")).strip()
        command = str(raw.get("command", "")).strip()
        hook_id = str(raw.get("id") or f"{event}-{len(specs)}").strip()
        if not event or not command:
            logger.warning("hooks: spec inválida en %s (falta event/command): %s",
                           source, raw)
            continue
        if not is_known(event):
            logger.warning(
                "hooks: %s declara el evento desconocido %r — nunca se va a "
                "disparar (válidos: %s)", hook_id, event, ", ".join(known_events()),
            )
        specs.append(HookSpec(
            id=hook_id,
            event=event,
            command=command,
            timeout_s=int(raw.get("timeout_s") or 10),
            blocking=bool(raw.get("blocking", False)),
            source=source,
            permissions=tuple(str(p) for p in (raw.get("permissions") or [])),
        ))
    return specs


def _project_trusted(project_root: Path) -> bool:
    if os.environ.get("WALKER_HOOKS_TRUST") == "1":
        return True
    cfg = _read_json(project_root / PROJECT_CONFIG_REL)
    hooks_cfg = cfg.get("hooks")
    return isinstance(hooks_cfg, dict) and bool(hooks_cfg.get("trusted"))


@dataclass
class HookBus:
    """Hooks de un proyecto ya fusionados (global + proyecto)."""

    specs: list[HookSpec] = field(default_factory=list)
    project_root: Path = Path(".")
    walker_root: Path = Path(".")
    skipped_project_hooks: int = 0
    # Diagnóstico: hooks que nunca van a correr y por qué (los usa `doctor`).
    unknown_events: list[str] = field(default_factory=list)
    incompatible: list[str] = field(default_factory=list)

    # ── Discovery ────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        project_root: str | Path,
        walker_root: str | Path | None = None,
        hooks_home: str | Path | None = None,
    ) -> "HookBus":
        """Fusiona hooks inline (global y proyecto) + paquetes instalados.

        Precedencia por `id`: global → proyecto → paquetes, y el proyecto puede
        apagar cualquiera con `"disabled"`. Un paquete incompatible con esta
        versión de walker NO corre (se saltea con aviso, nunca rompe el host).
        """
        from fast_mcp import __version__ as walker_version
        from fast_mcp.shared_kernel.hooks.package import (
            check_compat,
            hooks_home as default_hooks_home,
            installed,
            project_scope,
        )

        project = Path(project_root).resolve()
        walker = Path(walker_root).resolve() if walker_root else _walker_root()
        home = Path(hooks_home) if hooks_home else default_hooks_home()
        global_data = _read_json(walker / GLOBAL_HOOKS_REL)
        project_data = _read_json(project / PROJECT_HOOKS_REL)
        trusted = _project_trusted(project)

        merged: dict[str, HookSpec] = {
            spec.id: spec for spec in _parse_specs(global_data, "global")
        }
        skipped = 0
        if project_data:
            if trusted:
                for spec in _parse_specs(project_data, "project"):
                    merged[spec.id] = spec
            else:
                skipped = len(project_data.get("hooks") or [])
                if skipped:
                    logger.info(
                        "hooks: %d hook(s) de %s ignorados — el proyecto no está "
                        "marcado como confiable (hooks.trusted en .cognitive/config.json)",
                        skipped, project,
                    )

        # Paquetes instalados (globales y del proyecto). Un paquete del
        # proyecto sin trust no se ejecuta: misma regla que el hooks.json.
        incompatible: list[str] = []
        scopes = [("global", home)]
        if trusted:
            scopes.append(("project", project_scope(project)))
        elif installed(project_scope(project)):
            skipped += len(installed(project_scope(project)))
            logger.info(
                "hooks: paquetes de %s ignorados — el proyecto no está marcado "
                "como confiable", project,
            )

        for scope, root in scopes:
            for pkg in installed(root, scope=scope):
                problem = check_compat(pkg, walker_version)
                if problem:
                    incompatible.append(f"{scope}/{pkg.id} v{pkg.version}: {problem}")
                    logger.warning("hooks: %s/%s no corre: %s", scope, pkg.id, problem)
                    continue
                if not pkg.enabled:
                    logger.info("hooks: %s/%s está deshabilitado", scope, pkg.id)
                    continue
                merged[pkg.id] = HookSpec(
                    id=pkg.id,
                    command=pkg.command(),
                    events=pkg.events,
                    timeout_s=pkg.timeout_s,
                    blocking=pkg.blocking,
                    source=f"package:{scope}",
                    version=pkg.version,
                    permissions=pkg.permissions,
                )

        for hook_id in project_data.get("disabled") or []:
            merged.pop(str(hook_id), None)

        unknown: list[str] = []
        for spec in merged.values():
            for event in spec.subscribed_events():
                if not is_known(event):
                    unknown.append(f"{spec.source}/{spec.id}: {event}")

        return cls(
            specs=list(merged.values()),
            project_root=project,
            walker_root=walker,
            skipped_project_hooks=skipped,
            unknown_events=unknown,
            incompatible=incompatible,
        )

    # ── Execution ────────────────────────────────────────────────────

    def hooks_for(self, event: str) -> list[HookSpec]:
        return [s for s in self.specs if s.matches(event)]

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> list[HookResult]:
        """Corre los hooks del evento. Nunca lanza; devuelve un resultado por hook."""
        specs = self.hooks_for(event)
        if not specs:
            return []
        body = json.dumps(payload or {}, ensure_ascii=False)
        env = {
            **os.environ,
            "WALKER_EVENT": event,
            "WALKER_REPO": str(self.project_root),
            "WALKER_ROOT": str(self.walker_root),
            "WALKER_PYTHON": sys.executable,
        }
        try:
            from fast_mcp.shared_kernel.project_support import ProjectService

            detected = ProjectService.resolve_from_registry(str(self.project_root))
            if detected and detected.get("project_id"):
                env["WALKER_PROJECT"] = detected["project_id"]
        except Exception:  # noqa: BLE001 — informativo
            pass

        results: list[HookResult] = []
        for spec in specs:
            results.append(self._run(spec, body, env))
        return results

    def _run(self, spec: HookSpec, stdin_body: str, env: dict[str, str]) -> HookResult:
        started = time.time()
        try:
            proc = subprocess.run(
                _wrap_command(spec.command, spec.permissions,
                              repo=self.project_root, walker_root=self.walker_root),
                shell=True,
                cwd=str(self.project_root),
                input=stdin_body,
                capture_output=True,
                text=True,
                timeout=spec.timeout_s,
                env=_with_pythonpath(_scrub_env(env, spec.permissions), self.walker_root),
            )
            result = HookResult(
                hook_id=spec.id, event=spec.event, ok=proc.returncode == 0,
                rc=proc.returncode,
                duration_s=round(time.time() - started, 2),
                stdout=(proc.stdout or "")[-MAX_OUTPUT:],
                stderr=(proc.stderr or "")[-MAX_OUTPUT:],
            )
        except subprocess.TimeoutExpired:
            result = HookResult(
                hook_id=spec.id, event=spec.event, ok=False, rc=None,
                duration_s=round(time.time() - started, 2), timed_out=True,
                error=f"timeout de {spec.timeout_s}s",
            )
        except Exception as exc:  # noqa: BLE001 — un hook no rompe al host
            result = HookResult(
                hook_id=spec.id, event=spec.event, ok=False,
                duration_s=round(time.time() - started, 2),
                error=f"{type(exc).__name__}: {exc}",
            )
        if result.ok:
            logger.debug("hook %s (%s) ok en %.2fs", spec.id, spec.event, result.duration_s)
        else:
            logger.warning(
                "hook %s (%s) falló: rc=%s timeout=%s %s",
                spec.id, spec.event, result.rc, result.timed_out,
                (result.error or result.stderr)[:200],
            )
        return result

    def blocked(self, results: list[HookResult]) -> bool:
        """True si un hook `blocking` del evento falló (solo aplica a `before`)."""
        blocking_ids = {s.id for s in self.specs if s.blocking}
        return any((not r.ok) and r.hook_id in blocking_ids for r in results)
