"""shared_kernel.paths — Rutas de la instalación walker (SSOT del kernel).

Port de app/path_resolver (solo lo que fast_mcp necesita), stdlib only.
`root` es inyectable en todas las funciones: tests construyen contextos con
raíces propias sin monkeypatching (coherencia §3bis regla 3).
"""

from __future__ import annotations

from pathlib import Path


def walker_root() -> Path:
    """Raíz del proyecto walker.

    1. Camina hacia arriba desde este archivo buscando `config/config.json`
       (port de app/path_resolver.get_walker_root).
    2. Fallback estable: el padre del paquete `fast_mcp/` — el ADR-071 §2 fija
       el layout con `fast_mcp/` en la raíz de walker. (No se usa el CWD como
       fallback: el server puede arrancar desde cualquier directorio.)
    """
    candidate = Path(__file__).resolve().parent.parent.parent
    for _ in range(8):
        if (candidate / "config" / "config.json").is_file():
            return candidate.resolve()
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return Path(__file__).resolve().parent.parent.parent


def registry_path(root: Path | None = None) -> Path:
    """Registro global de proyectos: <walker_root>/data/projects.json."""
    base = root if root is not None else walker_root()
    return base / "data" / "projects.json"


def walkerignore_template(root: Path | None = None) -> Path:
    """Template .walkerignore que `prepare` copia a cada proyecto."""
    base = root if root is not None else walker_root()
    return base / "data" / ".walkerignore"
