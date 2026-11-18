"""shared_kernel/build_info.py — ¿qué código está corriendo este proceso?

Motivación medida (2026-11-18): un fix commiteado no aplicaba porque el
servidor MCP seguía ejecutando los módulos cargados al arrancar, y nada en las
respuestas lo decía. El agente concluía "el arreglo no sirve" cuando en
realidad hablaba con código viejo.

`stale` compara la mtime más nueva de `fast_mcp/**/*.py` contra el arranque del
proceso: si hay fuente más nueva, este proceso no la cargó. El resultado se
cachea con TTL corto para no recorrer el árbol en cada llamada.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PROCESS_START = time.time()
_CACHE: dict[str, object] = {"at": 0.0, "data": None}
_TTL = 10.0


def _walker_root() -> Path:
    # .../fast_mcp/shared_kernel/build_info.py → repo root
    return Path(__file__).resolve().parents[2]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _git_sha(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=2,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 — sin git no hay sha, no es un error
        return ""


def _newest_source_mtime(root: Path) -> float:
    newest = 0.0
    base = root / "fast_mcp"
    if not base.is_dir():
        return newest
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            try:
                newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
            except OSError:
                continue
    return newest


def build_info(ttl: float = _TTL) -> dict:
    """Sha del repo + si este proceso quedó atrás respecto del disco."""
    now = time.time()
    cached = _CACHE.get("data")
    if isinstance(cached, dict) and now - float(_CACHE.get("at") or 0) < ttl:
        return dict(cached)

    root = _walker_root()
    newest = _newest_source_mtime(root)
    data = {
        "git_sha": _git_sha(root),
        "process_started_at": _iso(PROCESS_START),
        "newest_source_mtime": _iso(newest),
        # 0.5s de margen: los mtimes del checkout no tienen precisión infinita.
        "stale": bool(newest > PROCESS_START + 0.5),
    }
    _CACHE.update({"at": now, "data": data})
    return dict(data)
