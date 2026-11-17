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
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GLOBAL_HOOKS_REL = Path("config") / "hooks" / "hooks.json"
PROJECT_HOOKS_REL = Path(".cognitive") / "hooks" / "hooks.json"
PROJECT_CONFIG_REL = Path(".cognitive") / "config.json"
MAX_OUTPUT = 500


@dataclass(frozen=True)
class HookSpec:
    id: str
    event: str
    command: str
    timeout_s: int = 10
    blocking: bool = False
    source: str = "global"  # global | project


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
        specs.append(HookSpec(
            id=hook_id,
            event=event,
            command=command,
            timeout_s=int(raw.get("timeout_s") or 10),
            blocking=bool(raw.get("blocking", False)),
            source=source,
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

    # ── Discovery ────────────────────────────────────────────────────

    @classmethod
    def load(cls, project_root: str | Path, walker_root: str | Path | None = None) -> "HookBus":
        project = Path(project_root).resolve()
        walker = Path(walker_root).resolve() if walker_root else _walker_root()
        global_data = _read_json(walker / GLOBAL_HOOKS_REL)
        project_data = _read_json(project / PROJECT_HOOKS_REL)

        merged: dict[str, HookSpec] = {
            spec.id: spec for spec in _parse_specs(global_data, "global")
        }
        skipped = 0
        if project_data:
            if _project_trusted(project):
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
        for hook_id in project_data.get("disabled") or []:
            merged.pop(str(hook_id), None)

        return cls(specs=list(merged.values()), project_root=project,
                   walker_root=walker, skipped_project_hooks=skipped)

    # ── Execution ────────────────────────────────────────────────────

    def hooks_for(self, event: str) -> list[HookSpec]:
        return [s for s in self.specs if s.event == event]

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
                spec.command,
                shell=True,
                cwd=str(self.project_root),
                input=stdin_body,
                capture_output=True,
                text=True,
                timeout=spec.timeout_s,
                env=env,
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
