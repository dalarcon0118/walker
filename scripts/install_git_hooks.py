"""scripts/install_git_hooks.py — instala el hook `post-commit` que sincroniza el CPG.

Por qué: `sync_code_index` solo corre dentro de tareas de workflow, así que
editar y commitear dejaba el CPG atrás (medido 2026-11-17: el nodo de
`harness/runner.py` siguió con `main` en la línea 364 después del commit).
Este instalador pone un `post-commit` que llama a `sync_cpg_on_commit.py` en
background: el commit no espera y el árbol del grafo se mantiene al día.

El hook es genérico: guarda la ruta de walker en el momento de instalarse, así
que también sirve para los repos de producto (`--repo`).

Uso:
    python3 scripts/install_git_hooks.py                 # repo de walker
    python3 scripts/install_git_hooks.py --repo ../game_reset/projects/backend-microservices
    python3 scripts/install_git_hooks.py --uninstall
"""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

WALKER_ROOT = Path(__file__).resolve().parent.parent
MARKER = "# walker-sync:post-commit"

_TEMPLATE = """#!/bin/sh
{marker}
# Instalado por scripts/install_git_hooks.py — no editar a mano.
# Sincroniza el CPG del proyecto en BACKGROUND: nunca bloquea ni falla el commit.
if [ "${{WALKER_SYNC_DISABLED:-0}}" = "1" ]; then
    exit 0
fi

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

python_bin=""
for candidate in "$repo_root/.venv/bin/python" "{walker}/.venv/bin/python" python3 python; do
    if [ -x "$candidate" ]; then python_bin="$candidate"; break; fi
    if command -v "$candidate" >/dev/null 2>&1; then python_bin=$(command -v "$candidate"); break; fi
done
if [ -z "$python_bin" ]; then exit 0; fi

log_dir="$HOME/.walker-backups"
mkdir -p "$log_dir" 2>/dev/null

(
    cd "$repo_root" || exit 0
    "$python_bin" "{walker}/scripts/sync_cpg_on_commit.py" >>"$log_dir/walker-sync.log" 2>&1
) >/dev/null 2>&1 &

exit 0
"""


def hook_path(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-commit"


def install(repo: Path) -> dict:
    target = hook_path(repo)
    if not (repo / ".git").is_dir():
        return {"ok": False, "error": f"{repo} no es un repo git"}
    target.parent.mkdir(parents=True, exist_ok=True)

    backup = None
    if target.exists():
        content = target.read_text(errors="ignore")
        if MARKER in content:
            backup = None  # nuestro propio hook: se reescribe
        else:
            backup = target.with_suffix(".pre-walker")
            backup.write_text(content)
            os.chmod(backup, 0o755)

    target.write_text(_TEMPLATE.format(marker=MARKER, walker=WALKER_ROOT))
    os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {"ok": True, "path": str(target), "backup": str(backup) if backup else None}


def uninstall(repo: Path) -> dict:
    target = hook_path(repo)
    if not target.exists():
        return {"ok": True, "removed": False}
    if MARKER not in target.read_text(errors="ignore"):
        return {"ok": False, "error": f"{target} no fue instalado por walker"}
    target.unlink()
    return {"ok": True, "removed": True, "path": str(target)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(WALKER_ROOT), help="repo donde instalar el hook")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    outcome = uninstall(repo) if args.uninstall else install(repo)
    if not outcome.get("ok"):
        print(f"error: {outcome.get('error')}", flush=True)
        return 2
    if args.uninstall:
        print(f"hook eliminado: {outcome.get('path', '(no estaba)')}")
    else:
        print(f"hook instalado: {outcome['path']}")
        if outcome.get("backup"):
            print(f"  backup del anterior: {outcome['backup']}")
        print(f"  log: {Path.home() / '.walker-backups' / 'walker-sync.log'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
