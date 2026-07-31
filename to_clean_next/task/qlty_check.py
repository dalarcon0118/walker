"""task/qlty_check.py — Quality check"""

from __future__ import annotations
from __future__ import annotations
import os
import sys
from _internal import qlty_check as _qlty_mod


def cmd_qlty_check(args: list[str]) -> int:
    """Run Qlty quality analysis on task artifacts."""
    positional = [a for a in args if not a.startswith("--")]
    flags = set(a for a in args if a.startswith("--"))

    if len(positional) < 1 or len(positional) > 3:
        eprint(
            "Uso: python3 scripts/task.py qlty-check <chain_dir> [task_id] [--verbose]"
        )
        eprint()
        eprint("  chain_dir    Ruta al directorio del sprint")
        eprint("  task_id      ID de la tarea a analizar (opcional si --dir)")
        eprint("  --verbose    Mostrar métricas detalladas por archivo")
        eprint()
        eprint("También para code review de directorios completos:")
        eprint("  python3 scripts/task.py qlty-check --dir <ruta> [--verbose]")
        eprint()
        eprint("Exit codes:")
        eprint("  0 → Todos los quality gates pasan")
        eprint("  1 → Algunos quality gates FALLAN")
        eprint("  2 → Error")
        return 2

    # Support direct --dir mode for code review (absorbed from _internal/qlty_check)
    if "--dir" in flags:
        # Extract the directory argument
        dir_idx = None
        for i, a in enumerate(args):
            if a == "--dir" and i + 1 < len(args):
                dir_idx = i + 1
                break
        if dir_idx is None:
            eprint("❌ --dir requiere un argumento (ruta al directorio)")
            return 2
        if _qlty_mod is None:
            eprint("❌ _internal.qlty_check no disponible")
            return 2
        qlty_argv = ["qlty_check.py", "--dir", args[dir_idx]]
        if "--verbose" in flags:
            qlty_argv.append("--verbose")
        old_argv = sys.argv
        try:
            sys.argv = qlty_argv
            _qlty_mod.main()
            return 0
        except SystemExit as e:
            rc = e.code if e.code is not None else 0
            return rc if rc in (0, 1) else 2
        finally:
            sys.argv = old_argv

    if len(positional) < 1:
        eprint("❌ chain_dir es requerido")
        return 2

    chain_dir = os.path.abspath(positional[0])

    if _qlty_mod is None:
        eprint("❌ _internal.qlty_check no disponible")
        return 2

    qlty_argv = ["qlty_check.py", chain_dir]
    if len(positional) >= 2:
        qlty_argv.append(positional[1])
    if "--verbose" in flags:
        qlty_argv.append("--verbose")
    old_argv = sys.argv
    try:
        sys.argv = qlty_argv
        _qlty_mod.main()
        return 0
    except SystemExit as e:
        rc = e.code if e.code is not None else 0
        return rc if rc in (0, 1) else 2
    finally:
        sys.argv = old_argv
