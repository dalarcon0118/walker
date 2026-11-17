"""scripts/hooks_cli.py — gestión de hooks: inline + paquetes instalables.

    python3 scripts/hooks_cli.py list [--project PATH]
    python3 scripts/hooks_cli.py install <path> [--project PATH] [--force]
    python3 scripts/hooks_cli.py remove <id> [--project PATH]
    python3 scripts/hooks_cli.py enable|disable <id> [--project PATH]
    python3 scripts/hooks_cli.py events
    python3 scripts/hooks_cli.py doctor [--project PATH]

Scope: sin `--project` opera sobre `~/.walker/hooks` (global, aplica a todos los
proyectos); con `--project` opera sobre `<repo>/.cognitive/hooks` (viaja con el
repo). `install` valida manifiesto, eventos y compatibilidad de versión antes
de copiar nada.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WALKER_ROOT = Path(__file__).resolve().parent.parent
if str(WALKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WALKER_ROOT))

from fast_mcp import __version__ as WALKER_VERSION  # noqa: E402
from fast_mcp.shared_kernel.hooks.bus import HookBus  # noqa: E402
from fast_mcp.shared_kernel.hooks.events import describe, known_events  # noqa: E402
from fast_mcp.shared_kernel.hooks.signature import (  # noqa: E402
    SignatureError,
    TrustedKey,
    keys_path,
    load_keys,
    revoke_key,
    rotate_key,
    save_keys,
    write_signature,
)
from fast_mcp.shared_kernel.hooks.package import (  # noqa: E402
    HookManifestError,
    fetch_git,
    parse_source,
    read_manifest,
    tree_digest,
    check_compat,
    doctor_report,
    hooks_home,
    install,
    installed,
    project_scope,
    remove,
    set_enabled,
    update,
)


def _scope(args) -> Path:
    return project_scope(args.project) if args.project else hooks_home()


def _cmd_list(args) -> int:
    root = _scope(args)
    packages = installed(root, scope="project" if args.project else "global")
    print(f"scope: {root}  (walker {WALKER_VERSION})")
    if not packages:
        print("  (sin paquetes instalados)")
    for pkg in packages:
        problem = check_compat(pkg, WALKER_VERSION)
        state = "on " if pkg.enabled else "off"
        extra = f" · {problem}" if problem else ""
        print(f"  [{state}] {pkg.id} v{pkg.version} · {','.join(pkg.events)}{extra}")
        if pkg.source:
            print(f"        origen: {pkg.source}")

    bus = HookBus.load(args.project or ".")
    if bus.specs:
        print("\nhooks inline + paquetes activos:")
        for spec in bus.specs:
            print(f"  {spec.id:<28} [{spec.source}] {','.join(spec.subscribed_events())}")
    for issue in bus.unknown_events:
        print(f"  ! evento desconocido → {issue}")
    return 0


def _cmd_install(args) -> int:
    root = _scope(args)
    try:
        pkg = install(
            args.path,
            root,
            walker_version=WALKER_VERSION,
            force=args.force,
            scope="project" if args.project else "global",
            ref=args.ref,
            subdir=args.subdir,
            expect_sha256=args.sha256,
            require_signature=args.require_signature,
        )
    except HookManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"instalado {pkg.id} v{pkg.version} en {pkg.path}")
    print(f"  eventos: {', '.join(pkg.events)}")
    if args.project:
        print("  (recordá marcar el proyecto como confiable: "
              '.cognitive/config.json → {"hooks": {"trusted": true}})')
    return 0


def _cmd_update(args) -> int:
    try:
        outcome = update(
            args.id,
            _scope(args),
            walker_version=WALKER_VERSION,
            scope="project" if args.project else "global",
            check=args.check,
            expect_sha256=args.sha256,
            require_signature=args.require_signature,
        )
    except HookManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not outcome["changed"]:
        print(f"{args.id} al día (v{outcome['current_version']} · {outcome['current_commit'][:12]})")
        return 0
    print(f"{args.id}: v{outcome['current_version']} → v{outcome['available_version']} "
          f"· {outcome['current_commit'][:12] or '?'} → {outcome['available_commit'][:12]}")
    if args.check:
        print("  (check-only: no se instaló)")
        return 0
    print("  actualizado" if outcome["installed"] else "  sin cambios")
    return 0


def _cmd_keys(args) -> int:
    keys = load_keys()
    if args.action == "list":
        if not keys:
            print(f"sin claves confiables ({keys_path()})")
        for key in keys.values():
            extra = ""
            if key.revoked:
                extra = f" · REVOCADA {key.revoked_at[:10]}"
                if key.revoked_reason:
                    extra += f" ({key.revoked_reason})"
            elif key.rotated_at:
                extra = f" · rotada {key.rotated_at[:10]} (antes {key.rotated_from}…)"
            print(f"  {key.id:<20} {key.ed25519[:16]}…  {key.added_at}{extra}")
        return 0
    if args.action in ("revoke", "rotate"):
        if not args.id:
            print(f"error: keys {args.action} requiere un id", file=sys.stderr)
            return 2
        try:
            if args.action == "revoke":
                key = revoke_key(args.id, args.reason or "")
                print(f"clave '{key.id}' revocada ({key.revoked_at[:10]})")
            else:
                raw = (args.public_key or "").strip()
                from pathlib import Path as _P

                if _P(raw).is_file():
                    raw = _P(raw).read_text().strip()
                if not raw:
                    print("error: rotate requiere --public-key", file=sys.stderr)
                    return 2
                key = rotate_key(args.id, raw)
                print(f"clave '{key.id}' rotada (antes {key.rotated_from}…)")
                if key.revoked:
                    print(
                        f"  ! la clave sigue REVOCADA ({key.revoked_at[:10]}): rotar "
                        "no restaura la confianza. Para volver a confiar, remove + add."
                    )
        except SignatureError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.action == "add":
        raw = args.public_key.strip()
        from pathlib import Path as _P

        if _P(raw).is_file():
            raw = _P(raw).read_text().strip()
        if not raw:
            print("error: --public-key vacío", file=sys.stderr)
            return 2
        if args.id in keys:
            print(
                f"error: la clave '{args.id}' ya existe; usá 'keys rotate' para "
                "cambiar el material o 'keys remove' para borrarla",
                file=sys.stderr,
            )
            return 2
        from datetime import datetime, timezone

        keys[args.id] = TrustedKey(
            id=args.id, ed25519=raw,
            added_at=datetime.now(timezone.utc).isoformat(),
        )
        path = save_keys(keys)
        print(f"clave '{args.id}' agregada a {path}")
        return 0
    if args.action == "remove":
        if args.id not in keys:
            print(f"no existe la clave '{args.id}'")
            return 1
        del keys[args.id]
        save_keys(keys)
        print(f"clave '{args.id}' eliminada")
        return 0
    return 2


def _cmd_sign(args) -> int:
    from pathlib import Path as _P

    try:
        payload = write_signature(_P(args.path), args.key, args.id)
    except (SignatureError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"firmado con '{payload['key_id']}' · sha256 {payload['sha256']}")
    print(f"  {_P(args.path) / 'hook.sig.json'}")
    return 0


def _cmd_digest(args) -> int:
    spec = parse_source(args.source, args.ref)
    tmp = None
    try:
        if spec.kind == "git":
            import tempfile
            from pathlib import Path as _P

            tmp = _P(tempfile.mkdtemp(prefix="walker-hook-"))
            commit = fetch_git(spec, tmp)
            source_dir = (tmp / args.subdir) if args.subdir else tmp
            print(f"origen: {spec.location}#{spec.ref or commit[:12]} · commit {commit}")
        else:
            from pathlib import Path as _P

            source_dir = _P(spec.location).resolve()
        package = read_manifest(source_dir)
        print(f"paquete: {package.id} v{package.version} · eventos: {','.join(package.events)}")
        print(f"sha256: {tree_digest(source_dir)}")
        print("\npublicá ese sha256 y verificá con: install <origen> --sha256 <hex>")
        return 0
    except HookManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if tmp is not None:
            import shutil as _sh

            _sh.rmtree(tmp, ignore_errors=True)


def _cmd_remove(args) -> int:
    outcome = remove(args.id, _scope(args))
    print(f"{'removido' if outcome['removed'] else 'no estaba instalado'}: {args.id}")
    return 0 if outcome["removed"] else 1


def _cmd_toggle(args) -> int:
    enabled = args.command == "enable"
    found = set_enabled(args.id, _scope(args), enabled)
    print(f"{'habilitado' if enabled else 'deshabilitado'}: {args.id}" if found
          else f"no instalado: {args.id}")
    return 0 if found else 1


def _cmd_events(args) -> int:
    bus = HookBus.load(args.project or ".")
    by_event: dict[str, list[str]] = {}
    for spec in bus.specs:
        for event in spec.subscribed_events():
            by_event.setdefault(event, []).append(f"{spec.id} [{spec.source}]")
    for spec in describe():
        print(f"  {spec['name']:<16} {spec['description']}")
        print(f"      payload: {', '.join(spec['payload'])}")
        subs = by_event.get(spec["name"], [])
        print(f"      suscriptores: {', '.join(subs) if subs else '(ninguno)'}")
    unknown = [e for e in by_event if e not in known_events()]
    for event in unknown:
        print(f"  ! {event}: declarado pero NO existe (nunca se dispara) → "
              f"{', '.join(by_event[event])}")
    return 0


def _cmd_doctor(args) -> int:
    report = doctor_report(args.project or ".", walker_version=WALKER_VERSION)
    print(f"walker {report['walker_version']} · eventos conocidos: {', '.join(report['events'])}")
    for scope in ("global", "project"):
        print(f"\n{scope}:")
        if not report[scope]:
            print("  (sin paquetes)")
        for item in report[scope]:
            state = "on " if item["enabled"] else "off"
            print(f"  [{state}] {item['id']} v{item['version']} · "
                  f"{','.join(item['events'])}")
            perms = item.get("permissions") or []
            print(f"        permisos: {','.join(perms) if perms else '(mínimos)'} · "
                  f"{item.get('signature', 'sin firma')}")
    print("\nissues:" if report["issues"] else "\nissues: ninguno")
    for issue in report["issues"]:
        print(f"  ! {issue}")
    return 1 if report["issues"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="walker hooks")
    ap.add_argument("--project", default=None,
                    help="opera sobre <repo>/.cognitive/hooks en vez del global")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="paquetes instalados y hooks activos")

    p_install = sub.add_parser("install", help="instala un paquete de hook")
    p_install.add_argument("path", help="directorio con hook.json")
    p_install.add_argument("--force", action="store_true",
                           help="reemplaza una versión ya instalada")
    p_install.add_argument("--ref", default=None,
                           help="tag/branch/sha para fuentes git (se registra pinneado)")
    p_install.add_argument("--subdir", default="",
                           help="subdirectorio del repo donde vive hook.json")
    p_install.add_argument("--sha256", default=None,
                           help="digest esperado del árbol (aborta si no coincide)")
    p_install.add_argument("--require-signature", action="store_true",
                           help="falla si el paquete no está firmado por una clave confiable")

    p_remove = sub.add_parser("remove", help="desinstala un paquete")
    p_remove.add_argument("id")

    p_update = sub.add_parser("update", help="actualiza un paquete git al ref registrado")
    p_update.add_argument("id")
    p_update.add_argument("--check", action="store_true",
                          help="solo informa, no instala")
    p_update.add_argument("--sha256", default=None,
                          help="digest esperado de la versión nueva")

    for name in ("enable", "disable"):
        p = sub.add_parser(name, help=f"{name} un paquete instalado")
        p.add_argument("id")

    p_keys = sub.add_parser("keys", help="claves ed25519 confiables")
    p_keys.add_argument("action", choices=["list", "add", "remove", "revoke", "rotate"])
    p_keys.add_argument("id", nargs="?")
    p_keys.add_argument("--public-key", default=None,
                        help="base64 de la clave pública o path a un .pub")
    p_keys.add_argument("--reason", default="", help="motivo de la revocación")

    p_sign = sub.add_parser("sign", help="firma un paquete con una clave privada")
    p_sign.add_argument("path")
    p_sign.add_argument("--key", required=True, help="PEM de la clave privada")
    p_sign.add_argument("--id", required=True, help="id de la clave (el de keys add)")

    p_digest = sub.add_parser("digest", help="calcula el sha256 de un paquete")
    p_digest.add_argument("source")
    p_digest.add_argument("--ref", default=None)
    p_digest.add_argument("--subdir", default="")

    sub.add_parser("events", help="catálogo de eventos y suscriptores")
    sub.add_parser("doctor", help="salud: compatibilidad, eventos desconocidos")

    args = ap.parse_args()
    handlers = {
        "list": _cmd_list,
        "install": _cmd_install,
        "update": _cmd_update,
        "digest": _cmd_digest,
        "keys": _cmd_keys,
        "sign": _cmd_sign,
        "remove": _cmd_remove,
        "enable": _cmd_toggle,
        "disable": _cmd_toggle,
        "events": _cmd_events,
        "doctor": _cmd_doctor,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
