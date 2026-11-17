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
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fast_mcp.shared_kernel.hooks.events import known_events, validate_events

logger = logging.getLogger(__name__)

MANIFEST_NAME = "hook.json"
REGISTRY_NAME = "registry.json"
SIGNATURE_NAME = "hook.sig.json"

# Vocabulario cerrado: un permiso desconocido en el manifiesto es un typo y se
# rechaza al instalar (un `netwrok` que se ignora en silencio es peor que no
# declararlo).
PERMISSIONS: dict[str, str] = {
    "fs:repo": "escribir dentro del repo (la lectura es implícita)",
    "fs:tmp": "leer/escribir en el directorio temporal",
    "fs:home": "leer/escribir en $HOME (git config, ssh, etc.)",
    "fs:any": "sin restricción de filesystem (escape hatch)",
    "exec": "lanzar subprocesos (informativo, no aplicable)",
    "network": "acceso a red",
    "env:all": "heredar TODO el entorno (incluye secretos)",
}
HOOKS_HOME_ENV = "WALKER_HOOKS_HOME"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


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
    permissions: tuple[str, ...] = ()
    signature: str = ""  # key_id que firmó, "" si no está firmado

    def command(self) -> str:
        target = self.path / self.entry
        if self.entry.endswith((".py", ".sh")):
            return f'"$WALKER_PYTHON" "{target}"' if self.entry.endswith(".py") else f'"{target}"'
        return f'"{target}"'


@dataclass(frozen=True)
class SourceSpec:
    """Origen de un paquete: path local o repo git con ref opcional."""

    kind: str  # local | git
    location: str
    ref: str = ""


def parse_source(source: str, ref: str | None = None) -> SourceSpec:
    """`github:user/repo#v1`, `git+https://…#sha`, `/path` → SourceSpec."""
    raw = source.strip()
    explicit_ref = (ref or "").strip()
    if raw.startswith("github:"):
        rest = raw[len("github:"):]
        repo, _, tail = rest.partition("#")
        return SourceSpec("git", f"https://github.com/{repo}.git", explicit_ref or tail)
    if raw.startswith("git+"):
        rest = raw[len("git+"):]
        location, _, tail = rest.partition("#")
        return SourceSpec("git", location, explicit_ref or tail)
    if "://" in raw or raw.startswith("git@"):
        location, _, tail = raw.partition("#")
        return SourceSpec("git", location, explicit_ref or tail)
    return SourceSpec("local", raw, "")


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise HookManifestError(
            f"git {' '.join(args)} falló: {(proc.stderr or proc.stdout).strip()[:200]}"
        )
    return proc.stdout.strip()


def fetch_git(spec: SourceSpec, dest: Path) -> str:
    """Clona shallow en `dest` y devuelve el commit resuelto (HEAD pineado)."""
    dest.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], dest)
    _git(["remote", "add", "origin", spec.location], dest)
    target = spec.ref or "HEAD"
    _git(["fetch", "-q", "--depth", "1", "origin", target], dest)
    _git(["checkout", "-q", "FETCH_HEAD"], dest)
    commit = _git(["rev-parse", "HEAD"], dest)
    if _SHA_RE.match(target) and not commit.startswith(target):
        raise HookManifestError(
            f"el ref {target} no coincide con el commit clonado ({commit[:12]})"
        )
    return commit


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
    entry_path = directory / entry
    if not entry_path.is_file():
        raise HookManifestError(f"entry inexistente: {entry_path}")
    # Un entry que no es Python se ejecuta DIRECTO: sin shebang/+x falla en
    # runtime con rc=126 y el error aparece lejos del manifiesto (medido). Se
    # valida acá para que falle al instalar.
    if not entry.endswith(".py") and os.name != "nt":
        if not os.access(entry_path, os.X_OK):
            raise HookManifestError(
                f"entry {entry} no es ejecutable (falta el bit +x). "
                f"Ejecutá: chmod +x {entry_path} — o usá un entry .py "
                "(se invoca con el intérprete de walker)"
            )
        try:
            first_line = entry_path.open("rb").readline(200)
        except OSError as exc:
            raise HookManifestError(f"no se pudo leer {entry}: {exc}") from exc
        if not first_line.startswith(b"#!"):
            raise HookManifestError(
                f"entry {entry} no tiene shebang (primera línea '#!...'). "
                "Sin intérprete declarado el shell no sabe cómo ejecutarlo; "
                "o usá un entry .py"
            )
    permissions = manifest.get("permissions") or []
    if not isinstance(permissions, list):
        raise HookManifestError("'permissions' debe ser una lista")
    unknown_perms = [str(p) for p in permissions if str(p) not in PERMISSIONS]
    if unknown_perms:
        raise HookManifestError(
            f"permisos desconocidos: {', '.join(unknown_perms)} · válidos: "
            f"{', '.join(PERMISSIONS)}"
        )

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
        permissions=tuple(str(p) for p in permissions),
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


_IGNORE_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_IGNORE_SUFFIX = (".pyc", ".pyo")
DIGEST_ALGO = "tree-v1"


def tree_digest(directory: str | Path) -> str:
    """sha256 determinístico del ÁRBOL completo del paquete (hex de 64).

    El digest anterior cubría solo manifiesto+entry: un `helper.py` importado
    por el entry, o una dependencia maliciosa, quedaba fuera de la verificación.
    Este recorre todos los archivos, ordenados por path relativo, con el nombre
    adentro del hash (renombrar cambia el digest). Se excluyen VCS, caches y
    symlinks (ver `_reject_symlinks`).
    """
    root = Path(directory)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in _IGNORE_PARTS for part in Path(rel).parts):
            continue
        if rel.endswith(_IGNORE_SUFFIX) or rel == ".DS_Store":
            continue
        if rel == SIGNATURE_NAME:
            continue  # firmar no puede cambiar el hash firmado
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _reject_symlinks(directory: Path) -> None:
    """Un paquete con symlinks puede leer fuera del repo al instalarse."""
    links = [p for p in directory.rglob("*") if p.is_symlink()]
    if links:
        raise HookManifestError(
            "el paquete contiene symlinks (no permitidos): "
            + ", ".join(str(p.relative_to(directory)) for p in links[:5])
        )


def _sha256_dir(directory: Path, entry: str) -> str:
    """Compat: digest del árbol (el `entry` ya no define el alcance)."""
    return tree_digest(directory)


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
    ref: str | None = None,
    subdir: str = "",
    expect_sha256: str | None = None,
    require_signature: bool = False,
) -> HookPackage:
    """Instala un paquete local o desde git (ref opcional, pinneado al commit).

    Git: clona shallow en un temporal, resuelve el commit (HEAD), valida el
    manifiesto y recién ahí copia. El registro guarda `source`, `ref`, `commit`
    y `sha256`, que es lo que permite responder qué código corre y detectar
    modificaciones en disco.
    """
    spec = parse_source(str(source), ref)
    tmpdir: Path | None = None
    source_label = str(source)
    commit = ""
    try:
        if spec.kind == "git":
            tmpdir = Path(tempfile.mkdtemp(prefix="walker-hook-"))
            commit = fetch_git(spec, tmpdir)
            source_dir = (tmpdir / subdir) if subdir else tmpdir
            source_label = f"{spec.location}#{spec.ref or commit[:12]}"
        else:
            source_dir = Path(spec.location).resolve()
            if not source_dir.is_dir():
                raise HookManifestError(f"no es un directorio: {source_dir}")

        package = read_manifest(source_dir, scope=scope)
        incompatible = check_compat(package, walker_version)
        if incompatible:
            raise HookManifestError(f"{package.id}: {incompatible}")

        _reject_symlinks(source_dir)

        from fast_mcp.shared_kernel.hooks.signature import (
            SignatureError,
            verify_package,
        )

        try:
            sig_ok, sig_detail = verify_package(source_dir, require=require_signature)
        except SignatureError as exc:
            raise HookManifestError(f"{package.id}: {exc}") from exc
        if not sig_ok:
            raise HookManifestError(
                f"{package.id}: {sig_detail} y se exigió firma "
                "(--require-signature)"
            )
        signature_key = sig_detail.split()[-1] if sig_detail.startswith("firmado por") else ""

        source_digest = tree_digest(source_dir)
        if expect_sha256:
            expected = expect_sha256.strip().lower()
            if expected != source_digest:
                raise HookManifestError(
                    f"{package.id}: el digest del paquete no coincide.\n"
                    f"  esperado: {expected}\n"
                    f"  obtenido: {source_digest}\n"
                    "  (si el código es legítimo, publicá el digest nuevo; "
                    "si no, NO instales)"
                )

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
        copied_digest = tree_digest(target)
        if copied_digest != source_digest:
            shutil.rmtree(target, ignore_errors=True)
            raise HookManifestError(
                f"{package.id}: la copia al scope no coincide con el origen "
                f"({copied_digest} != {source_digest}); instalación abortada"
            )
        entry = {
            "id": installed_pkg.id,
            "version": installed_pkg.version,
            "walker": installed_pkg.walker,
            "events": list(installed_pkg.events),
            "source": source_label,
            "source_type": spec.kind,
            "ref": spec.ref or (commit[:12] if commit else ""),
            "commit": commit,
            "subdir": subdir,
            "sha256": source_digest,
            "digest": DIGEST_ALGO,
            "signature": signature_key,
            "permissions": list(installed_pkg.permissions),
            "enabled": True,
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        registry["hooks"] = [h for h in registry["hooks"] if h.get("id") != package.id]
        registry["hooks"].append(entry)
        save_registry(root, registry)
        logger.info("hook %s v%s instalado en %s (%s)",
                    installed_pkg.id, installed_pkg.version, target, source_label)
        return HookPackage(**{**installed_pkg.__dict__, "enabled": True,
                              "source": source_label,
                              "installed_at": entry["installed_at"]})
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def update(
    hook_id: str,
    scope_root: str | Path,
    *,
    walker_version: str,
    scope: str = "global",
    check: bool = False,
    expect_sha256: str | None = None,
) -> dict:
    """Actualiza un paquete git al ref registrado. `check=True` solo reporta."""
    root = Path(scope_root)
    registry = load_registry(root)
    entry = next((h for h in registry["hooks"] if h.get("id") == hook_id), None)
    if entry is None:
        raise HookManifestError(f"{hook_id} no está instalado en {root}")
    if entry.get("source_type") != "git":
        raise HookManifestError(
            f"{hook_id} se instaló desde un path local ({entry.get('source')}); "
            "reinstalá con install --force"
        )

    spec = parse_source(str(entry.get("source", "")), entry.get("ref"))
    tmpdir = Path(tempfile.mkdtemp(prefix="walker-hook-"))
    try:
        commit = fetch_git(spec, tmpdir)
        source_dir = (tmpdir / entry.get("subdir", "")) if entry.get("subdir") else tmpdir
        package = read_manifest(source_dir, scope=scope)
        incompatible = check_compat(package, walker_version)
        if incompatible:
            raise HookManifestError(f"{hook_id}: {incompatible}")

        _reject_symlinks(source_dir)
        new_sha = tree_digest(source_dir)
        if expect_sha256:
            expected = expect_sha256.strip().lower()
            if expected != new_sha:
                raise HookManifestError(
                    f"{hook_id}: digest disponible {new_sha} != esperado {expected}"
                )
        changes = {
            "id": hook_id,
            "current_version": entry.get("version"),
            "available_version": package.version,
            "current_commit": entry.get("commit", ""),
            "available_commit": commit,
            "current_sha256": entry.get("sha256", ""),
            "available_sha256": new_sha,
            "changed": (package.version != entry.get("version")
                        or new_sha != entry.get("sha256")
                        or commit != entry.get("commit", "")),
            "installed": False,
        }
        if check or not changes["changed"]:
            return changes

        install(
            f"git+{spec.location}#{spec.ref or commit}",
            root,
            walker_version=walker_version,
            force=True,
            scope=scope,
            subdir=entry.get("subdir", ""),
            expect_sha256=new_sha,
        )
        changes["installed"] = True
        return changes
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def verify_installed(scope_root: str | Path) -> list[dict]:
    """Compara `sha256` del disco contra el registro (detecta modificaciones)."""
    root = Path(scope_root)
    registry = {item.get("id"): item for item in load_registry(root).get("hooks", [])}
    drift: list[dict] = []
    for package in installed(root):
        meta = registry.get(package.id, {})
        expected = meta.get("sha256", "")
        if not expected:
            continue
        actual = _sha256_dir(root / package.id, package.entry)
        if actual != expected:
            drift.append({"id": package.id, "expected": expected, "actual": actual})
    return drift


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
        drifted = {item["id"]: item for item in verify_installed(root)}
        for package in installed(root, scope=scope):
            problem = check_compat(package, walker_version)
            from fast_mcp.shared_kernel.hooks.signature import (
                SignatureError,
                verify_package,
            )

            try:
                signed, sig_detail = verify_package(root / package.id)
            except SignatureError as exc:
                signed, sig_detail = False, f"firma inválida: {exc}"
            report[scope].append({
                "id": package.id, "version": package.version,
                "events": list(package.events), "enabled": package.enabled,
                "source": package.source, "problem": problem,
                "permissions": list(package.permissions),
                "signed": signed, "signature": sig_detail,
            })
            if not signed:
                report["issues"].append(f"{scope}/{package.id}: {sig_detail}")
            if problem:
                report["issues"].append(f"{scope}/{package.id}: {problem}")
            if not package.enabled:
                report["issues"].append(f"{scope}/{package.id}: deshabilitado")
            if package.id in drifted:
                report["issues"].append(
                    f"{scope}/{package.id}: modificado en disco "
                    f"({drifted[package.id]['actual']} != {drifted[package.id]['expected']})"
                )

    bus = HookBus.load(project_root or ".", hooks_home=hooks_home_dir)
    for spec in bus.specs:
        for event in (spec.events or (spec.event,)):
            if event not in known_events():
                report["issues"].append(
                    f"{spec.source}/{spec.id}: evento desconocido {event!r}"
                )
    return report
