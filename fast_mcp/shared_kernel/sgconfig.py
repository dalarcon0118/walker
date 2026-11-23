"""shared_kernel.sgconfig — Siembra de sgconfig.yml para lenguajes custom (ast-grep).

Módulo hoja: no importa features/. Usado por use_cases (prepare) y por
ast_grep_adapter (_resolve_sgconfig) para generar configs con paths absolutos.
"""
from __future__ import annotations

from pathlib import Path

from fast_mcp.shared_kernel import paths


def _walker_data_dir() -> Path:
    return paths.walker_root() / "data"


def seed_sgconfig(data_dir: Path) -> bool:
    """Escribe `<proyecto>/data/sgconfig.yml` para ast-grep (best-effort)."""
    dest = Path(data_dir) / "sgconfig.yml"
    if dest.exists():
        return False
    try:
        base = _walker_data_dir()
        parser_path = base / "parsers" / "purescript.dylib"
        if not parser_path.is_file():
            return False
        outline_path = base / "outline_rules" / "purescript.yml"
        lines = [
            "customLanguages:",
            "  purescript:",
            f"    libraryPath: {parser_path}",
            "    extensions: [purs]",
            '    expandoChar: "_"',
        ]
        if outline_path.is_file():
            lines.append(f"    outlineRules: {outline_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def seed_root_sgconfig(project_root: str | Path) -> bool:
    """Escribe `<proyecto>/sgconfig.yml` (raíz del repo) para ast-grep (best-effort)."""
    dest = Path(project_root) / "sgconfig.yml"
    if dest.exists():
        return False
    try:
        base = _walker_data_dir()
        global_sg = base / "sgconfig.yml"
        if not global_sg.is_file():
            # generar el global primero
            seed_sgconfig(base)
            global_sg = base / "sgconfig.yml"
        if not global_sg.is_file():
            return False
        dest.write_text(global_sg.read_text())
        return True
    except Exception:  # noqa: BLE001
        return False
