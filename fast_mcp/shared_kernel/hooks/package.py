"""shared_kernel/hooks/package.py — hooks instalables como paquetes.

Un hook inline (`hooks.json`) declara la suscripción en el archivo del que lo
instala. Un paquete al revés: se declara a sí mismo con `hook.json` y el
registro solo lo enlaza. Eso habilita `walker hooks install <path>` con las
validaciones que un archivo inline no puede tener: eventos existentes, versión
de walker compatible, id no tomado, y trazabilidad de qué código corre.

Layout:

    <scope>/<id>/hook.json     ← manifiesto del paquete
    <scope>/registry.json      ← qué está instalado (versión, origen, hash, enabled)

`scope` es `~/.walker/hooks` (global, aplica a todos los proyectos) o
`<repo>/.cognitive/hooks` (del proyecto, viaja con el repo).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fast_mcp.shared_kernel.hooks.events import known_events, validate_events

logger = logging.getLogger(__name__)

MANIFEST_NAME = "hook.json"
REGISTRY_NAME = "registry.json"
HOOKS_HOME_ENV = "WALKER_HOOKS_HOME"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


class HookManifestError(ValueError):
    """Manifiesto inválido o paquete incompatible."""


@dataclass(frozen=True)
class HookPackage:
    id: str
    version: str
    walker: str
    events: tuple[str, ...]
    entry: str
    path: Path
    timeout_s: int = 10
    blocking: bool = False
    description: str = ""
    scope: str = "global"
    enabled: bool = True
    source: str = ""
    installed_at: str = ""

    def command(self) -> str:
        target = self.path / self.entry
        if self.entry.endswith((".py", ".sh")):
            return f'"$WALKER_PYTHON" "{target}"' if self.entry.endswith(".py") else f'"{target}"'
        return f'"{target}"'


def hooks_home() -> Path:
    override = os.environ.get(HOOKS_HOME_ENV)
    return Path(override) if override else Path.home() / ".walker" / "hooks"


def project_scope(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / ".cognitive" / "hooks"


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("hooks: %s ilegible (%s)", path, exc)
        return {}


def read_manifest(package_dir: str | Path, *, scope: str = "global") -> HookPackage:
    """Lee y valida `hook.json`. Lanza HookManifestError si falta o está mal."""
    directory = Path(package_dir)
    manifest = _read_json(directory / MANIFEST_NAME)
    if not manifest:
        raise HookManifestError(f"{directory}: falta {MANIFEST_NAME} o no es JSON válido")

    hook_id = str(manifest.get("id", "")).strip()
    if not _ID_RE.match(hook_id):
        raise HookManifestError(
            f"id inválido {hook_id!r}: minúsculas, números, '.', '_' o '-' (2-64)"
        )
    version = str(manifest.get("version", "")).strip()
    if not version:
        raise HookManifestError("falta 'version'")
    walker = str(manifest.get("walker", "")).strip()
    if not walker:
        raise HookManifestError("falta 'walker' (rango de compatibilidad, ej: '>=3.0,<4')")
    events = manifest.get("events") or []
    if not isinstance(events, list) or not events:
        raise HookManifestError("'events' debe ser una lista no vacía")
    unknown = validate_events([str(e) for e in events])
    if unknown:
        raise HookManifestError(
            f"eventos desconocidos: {', '.join(unknown)} · válidos: {', '.join(known_events())}"
        )
    entry = str(manifest.get("entry", "")).strip()
    if not entry:
        raise HookManifestError("falta 'entry' (script a ejecutar)")
    if not (directory / entry).is_file():
        raise HookManifestError(f"entry inexistente: {directory / entry}")

    return HookPackage(
        id=hook_id,
        version=version,
        walker=walker,
        events=tuple(str(e) for e in events),
        entry=entry,
        path=directory.resolve(),
        timeout_s=int(manifest.get("timeout_s") or 10),
        blocking=bool(manifest.get("blocking", False)),
        description=str(manifest.get("description", "")),
        scope=scope,
    )


def check_compat(package: HookPackage, walker_version: str) -> str:
    """'' si es compatible; si no, el motivo (para instalar y para doctor)."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    try:
        spec = SpecifierSet(package.walker)
    except InvalidSpecifier:
        return f"constraint inválido: {package.walker!r} (ej: '>=3.0,<4')"
    if not spec.contains(walker_version):
        return f"requiere walker {package.walker}, instalado {walker_version}"
    return ""


def registry_path(scope_root: str | Path) -> Path:
    return Path(scope_root) / REGISTRY_NAME


def load_registry(scope_root: str | Path) -> dict:
    data = _read_json(registry_path(scope_root))
    data.setdefault("hooks", [])
    return data


def save_registry(scope_root: str | Path, data: dict) -> None:
    root = Path(scope_root)
    root.mkdir(parents=True, exist_ok=True)
    registry_path(root).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _sha256_dir(directory: Path, entry: str) -> str:
    digest = hashlib.sha256()
    files = [Path(MANIFEST_NAME), Path(entry)]
    for rel in files:
        path = directory / rel
        if path.is_file():
            digest.update(str(rel).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def installed(scope_root: str | Path, *, scope: str = "global") -> list[HookPackage]:
    """Paquetes instalados, con `enabled` del registro."""
    root = Path(scope_root)
    registry = {item.get("id"): item for item in load_registry(root).get("hooks", [])}
    packages: list[HookPackage] = []
    if not root.is_dir():
        return packages
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / MANIFEST_NAME).is_file():
            continue
        try:
            package = read_manifest(child, scope=scope)
        except HookManifestError as exc:
            logger.warning("hooks: paquete inválido en %s (%s)", child, exc)
            continue
        meta = registry.get(package.id, {})
        packages.append(
            HookPackage(
                **{
                    **package.__dict__,
                    "enabled": bool(meta.get("enabled", True)),
                    "source": str(meta.get("source", "")),
                    "installed_at": str(meta.get("installed_at", "")),
                }
            )
        )
    return packages


def install(
    source: str | Path,
    scope_root: str | Path,
    *,
    walker_version: str,
    force: bool = False,
    scope: str = "global",
) -> HookPackage:
    """Copia el paquete al scope y lo registra. Lanza HookManifestError."""
    source_dir = Path(source).resolve()
    if not source_dir.is_dir():
        raise HookManifestError(f"no es un directorio: {source_dir}")

    package = read_manifest(source_dir, scope=scope)
    incompatible = check_compat(package, walker_version)
    if incompatible:
        raise HookManifestError(f"{package.id}: {incompatible}")

    root = Path(scope_root)
    root.mkdir(parents=True, exist_ok=True)
    registry = load_registry(root)
    existing = next((h for h in registry["hooks"] if h.get("id") == package.id), None)
    target = root / package.id
    if existing and not force:
        raise HookManifestError(
            f"{package.id} v{existing.get('version')} ya está instalado en {root} "
            f"(usá --force para reemplazarlo)"
        )

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source_dir,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
    )

    installed_pkg = read_manifest(target, scope=scope)
    entry = {
        "id": installed_pkg.id,
        "version": installed_pkg.version,
        "walker": installed_pkg.walker,
        "events": list(installed_pkg.events),
        "source": str(source_dir),
        "sha256": _sha256_dir(target, installed_pkg.entry),
        "enabled": True,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    registry["hooks"] = [h for h in registry["hooks"] if h.get("id") != package.id]
    registry["hooks"].append(entry)
    save_registry(root, registry)
    logger.info("hook %s v%s instalado en %s", installed_pkg.id, installed_pkg.version, target)
    return HookPackage(**{**installed_pkg.__dict__, "enabled": True,
                          "source": entry["source"], "installed_at": entry["installed_at"]})


def remove(hook_id: str, scope_root: str | Path) -> dict:
    """Desinstala un paquete (dir + entrada de registro)."""
    root = Path(scope_root)
    registry = load_registry(root)
    before = len(registry["hooks"])
    registry["hooks"] = [h for h in registry["hooks"] if h.get("id") != hook_id]
    target = root / hook_id
    removed_dir = False
    if target.is_dir():
        shutil.rmtree(target)
        removed_dir = True
    save_registry(root, registry)
    return {"removed": before != len(registry["hooks"]) or removed_dir,
            "dir_removed": removed_dir}


def set_enabled(hook_id: str, scope_root: str | Path, enabled: bool) -> bool:
    root = Path(scope_root)
    registry = load_registry(root)
    found = False
    for item in registry["hooks"]:
        if item.get("id") == hook_id:
            item["enabled"] = enabled
            found = True
    if found:
        save_registry(root, registry)
    return found


def doctor_report(
    project_root: str | Path | None = None,
    *,
    walker_version: str,
    hooks_home_dir: str | Path | None = None,
) -> dict:
    """Estado de salud: catálogo, paquetes por scope, compatibilidad y huérfanos."""
    from fast_mcp.shared_kernel.hooks.bus import HookBus

    home = Path(hooks_home_dir) if hooks_home_dir else hooks_home()
    report: dict = {"walker_version": walker_version, "events": known_events(),
                    "global": [], "project": [], "issues": []}

    for scope, root in (("global", home), ("project", project_scope(project_root or "."))):
        for package in installed(root, scope=scope):
            problem = check_compat(package, walker_version)
            report[scope].append({
                "id": package.id, "version": package.version,
                "events": list(package.events), "enabled": package.enabled,
                "source": package.source, "problem": problem,
            })
            if problem:
                report["issues"].append(f"{scope}/{package.id}: {problem}")
            if not package.enabled:
                report["issues"].append(f"{scope}/{package.id}: deshabilitado")

    bus = HookBus.load(project_root or ".", hooks_home=hooks_home_dir)
    for spec in bus.specs:
        for event in (spec.events or (spec.event,)):
            if event not in known_events():
                report["issues"].append(
                    f"{spec.source}/{spec.id}: evento desconocido {event!r}"
                )
    return report
