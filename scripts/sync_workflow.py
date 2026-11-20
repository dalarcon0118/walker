#!/usr/bin/env python3
"""Sincroniza el grafo del workflow: `workflow-src/` (versionado) ↔ runtime.

`data/workflow/` está en `.gitignore` (es runtime), así que el grafo no se
reproducía en un checkout limpio. `workflow-src/` es la copia versionada;
`GraphRepository` la siembra cuando falta.

Uso:
    python3 scripts/sync_workflow.py --check   # falla si hay drift
    python3 scripts/sync_workflow.py --apply   # runtime -> src (exportar)
    python3 scripts/sync_workflow.py --seed    # src -> runtime (sembrar)
"""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "workflow-src"
RUNTIME = ROOT / "data" / "workflow"


def _files(root: Path) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {
        str(path.relative_to(root)): path
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def diff_trees(source: Path, target: Path) -> list[str]:
    """Rutas relativas que difieren (faltan o cambian de contenido)."""
    src, dst = _files(source), _files(target)
    diffs: list[str] = []
    for name, path in src.items():
        other = dst.get(name)
        if other is None:
            diffs.append(f"falta en runtime: {name}")
        elif name.endswith(".json"):
            try:
                if json.loads(path.read_text()) != json.loads(other.read_text()):
                    diffs.append(f"contenido distinto: {name}")
            except json.JSONDecodeError:
                diffs.append(f"JSON inválido: {name}")
        elif not filecmp.cmp(path, other, shallow=False):
            diffs.append(f"contenido distinto: {name}")
    for name in dst.keys() - src.keys():
        diffs.append(f"sobra en runtime (no versionado): {name}")
    return diffs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sincroniza workflow-src ↔ data/workflow")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--seed", action="store_true")
    args = ap.parse_args(argv)

    if args.seed:
        if not SRC.is_dir():
            print("no existe workflow-src/", file=sys.stderr)
            return 2
        shutil.copytree(SRC, RUNTIME, dirs_exist_ok=True)
        print(f"sembrado: {SRC} -> {RUNTIME}")
        return 0

    if args.apply:
        if not RUNTIME.is_dir():
            print("no existe data/workflow/ (nada que exportar)", file=sys.stderr)
            return 2
        if SRC.exists():
            shutil.rmtree(SRC)
        shutil.copytree(RUNTIME, SRC)
        print(f"exportado: {RUNTIME} -> {SRC}")
        return 0

    if not SRC.is_dir() or not RUNTIME.is_dir():
        print(f"faltan directorios: src={SRC.is_dir()} runtime={RUNTIME.is_dir()}",
              file=sys.stderr)
        return 2
    diffs = diff_trees(SRC, RUNTIME)
    if diffs:
        print(f"drift ({len(diffs)}):", file=sys.stderr)
        for item in diffs[:20]:
            print(f"  {item}", file=sys.stderr)
        print("Corré --apply para exportar el runtime o --seed para sembrarlo.",
              file=sys.stderr)
        return 1
    print(f"sin drift: {len(_files(SRC))} archivos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
