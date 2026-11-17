"""fast_mcp.hooks — API estable para autores de hooks.

Este módulo es el CONTRATO público. Lo de `fast_mcp.shared_kernel.hooks` es la
implementación (bus, manifiestos, firmas, sandbox) y puede cambiar; un hook de
terceros debe importar solo de acá.

Uso mínimo (entry `main.py` de un paquete):

    from fast_mcp.hooks import event, payload, run

    def main(data: dict) -> int:
        if event() == "cpg.indexed":
            print(f"indexado: {data.get('nodes')} nodos")
        return 0

    if __name__ == "__main__":
        raise SystemExit(run(main))

`run` lee el payload de stdin, llama a `main`, y convierte cualquier excepción
en exit code 1 con un mensaje en stderr (un hook que explota no debe dejar un
traceback sin contexto en el log del commit).

Sin estado global: `payload()` lee stdin una vez y lo cachea. Todo lo demás son
variables de entorno que el bus garantiza (`WALKER_EVENT`, `WALKER_PROJECT`,
`WALKER_REPO`, `WALKER_ROOT`, `WALKER_PYTHON`).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "event",
    "fail",
    "known_events",
    "payload",
    "project_id",
    "python",
    "repo_path",
    "run",
    "warn",
    "walker_root",
]

_PAYLOAD_CACHE: dict[str, Any] | None = None


# ── Contexto del evento ──────────────────────────────────────────────────


def event() -> str:
    """Nombre del evento que disparó el hook (`commit`, `cpg.indexed`, ...)."""
    return os.environ.get("WALKER_EVENT", "")


def payload() -> dict[str, Any]:
    """Payload del evento (JSON por stdin). Nunca lanza: {} si falta o es inválido."""
    global _PAYLOAD_CACHE
    if _PAYLOAD_CACHE is not None:
        return _PAYLOAD_CACHE
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    if not raw.strip():
        _PAYLOAD_CACHE = {}
        return _PAYLOAD_CACHE
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _PAYLOAD_CACHE = {"raw": raw}
        return _PAYLOAD_CACHE
    _PAYLOAD_CACHE = parsed if isinstance(parsed, dict) else {"value": parsed}
    return _PAYLOAD_CACHE


def project_id() -> str:
    return os.environ.get("WALKER_PROJECT", "")


def repo_path() -> Path:
    return Path(os.environ.get("WALKER_REPO", ".")).resolve()


def walker_root() -> Path:
    return Path(os.environ.get("WALKER_ROOT", ".")).resolve()


def python() -> str:
    """Intérprete de walker (el mismo con el que corre el hook)."""
    return os.environ.get("WALKER_PYTHON", sys.executable)


def known_events() -> list[str]:
    """Eventos que existen. Si el hook no escucha uno de estos, nunca corre."""
    from fast_mcp.shared_kernel.hooks.events import known_events as _known

    return _known()


# ── Salida y errores ─────────────────────────────────────────────────────


def warn(message: str) -> None:
    """Aviso a stderr (se ve en el log del runner, no contamina stdout)."""
    print(f"[hook] {message}", file=sys.stderr, flush=True)


def fail(message: str, code: int = 1) -> int:
    """Reporta un fallo accionable y devuelve el exit code."""
    warn(f"ERROR: {message}")
    return code


def run(main: Callable[[dict[str, Any]], int | None]) -> int:
    """Ejecuta `main(payload)` y normaliza el resultado a un exit code.

    - devuelve lo que devuelva `main` (None → 0);
    - una excepción → traza corta a stderr y 1 (el bus loguea rc y stderr).
    """
    try:
        result = main(payload())
    except Exception as exc:  # noqa: BLE001 — el hook no debe crashear sin contexto
        warn(f"{type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)
        return 1
    return 0 if result is None else int(result)
