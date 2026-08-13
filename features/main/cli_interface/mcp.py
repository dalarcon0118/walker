"""mcp.py — Driver del adaptador MCP (entry point limpio).

ARQUITECTURA LIMPIA: entry point del adaptador de entrada MCP.
Se invoca como `python -m features.cli_interface.mcp` — NO hay scripts/.

El driver solo arranca el loop stdio JSON-RPC; toda la logica vive
en mcp_adapter (adaptador) que delega en el Router (SSOT).

ANTI-ACUMULACION: cada sesion del cliente MCP (opencode, etc.) spawns
su propio proceso `python -m features.cli_interface.mcp`. Si una sesion
se cierra sin cerrar el pipe stdio limpiamente, el proceso queda
HUERFANO. Este driver incluye un guard que al arrancar detecta y mata
los procesos previos del mismo entry point (excluyendo el propio y su
padre) — asi nunca se acumulan instancias.

Uso:
    python -m features.cli_interface.mcp          # servidor stdio
    python -m features.cli_interface.mcp --self-test
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)

# IMPORT LAZY (ciclo de imports con bootstrap_modules, D1):
#   bootstrap_modules() → _discover_mcp_modules() importa este módulo
#   (features.cli_interface.mcp) para registrar tools MCP. Si aquí se
#   importara mcp_adapter a nivel de módulo, y mcp_adapter estuviera
#   parcialmente inicializado (su init dispara bootstrap_modules vía
#   _load_protocol_text/tool_catalog), el `from .mcp_adapter import ...`
#   lanzaría ImportError y el singleton del ModuleRegistry quedaría cacheado
#   SIN este módulo para toda la sesión (tests MCP/CLI rotos en cascada).
#   El driver solo necesita _handle_request/serve_stdio en runtime (main /
#   _self_test), así que el import va lazy dentro de las funciones.


def _kill_previous_instances() -> None:
    """Mata procesos HUERFANOS de este mismo MCP server (instancias zombies).

    PRINCIPIO: cada sesion del cliente MCP spawns un proceso. Si la sesion
    anterior murio sin cerrar el pipe stdio limpiamente, el proceso queda
    HUERFANO (reparented a launchd/init, ppid=1) y consume el codigo VIEJO
    (sintomas de "server stale"). Al arrancar, este guard cierra SOLO los
    procesos huerfanos reales.

    Seguridad (leccion del bug -32000 "tools desaparecieron"):
      - Excluye el PID actual y el PID padre (opencode/terminal que nos
        spawns) — nunca se mata a si mismo ni a su dueño.
      - Solo mata procesos con ppid=1 (huerfanos REALES, sin padre vivo).
        Un server MCP VIVO de otra sesion TAMBIEN matchea el entry point
        (p. ej. el server del agente opencode con ppid=opencode), pero NO
        se mata: tiene padre vivo. El guard anterior mataba a ciegas todo
        proceso previo con el mismo entry point y derribaba el server del
        agente activo, dejando las tools walker_* sin responder.
      - Solo matchea el entry point exacto `features.cli_interface.mcp`
        (no afecta otros MCP servers ni el resto del sistema).
    """
    self_pid = os.getpid()
    parent_pid = os.getppid()
    pattern = "features.cli_interface.mcp"
    pgrep_outcome = _pgrep_core(pattern)
    if not is_successful(pgrep_outcome):
        # Sin pgrep o timeout: no forzar
        logger.debug("pgrep no disponible (%s: %s); guard omitido",
                     type(pgrep_outcome.failure()).__name__, pgrep_outcome.failure())
        return
    out = pgrep_outcome.value_or(None)
    if out is None or out.returncode != 0 or not out.stdout.strip():
        return  # no hay procesos previos
    for pid_str in out.stdout.strip().splitlines():
        pid_outcome = _parse_pid_core(pid_str)
        if not is_successful(pid_outcome):
            continue  # pid no numérico: se omite
        pid = pid_outcome.value_or(0)
        if pid in (self_pid, parent_pid):
            continue  # no matarse ni matar al dueño
        ppid_outcome = _ppid_core(pid)
        if not is_successful(ppid_outcome):
            logger.debug("ppid de %s no disponible (%s); se omite el proceso",
                         pid, type(ppid_outcome.failure()).__name__)
            continue
        ppid = ppid_outcome.value_or(0)
        if ppid != 1:
            # Proceso CON padre vivo (otra sesión MCP activa, p.ej. el server
            # del agente): NO es huérfano, está en uso → NO se mata.
            logger.debug("proceso %s tiene padre vivo (%s); se conserva", pid, ppid)
            continue
        kill_outcome = _terminate_core(pid)
        if not is_successful(kill_outcome):
            logger.debug(
                "no se pudo terminar %s (%s: %s); ya murio o sin permisos",
                pid, type(kill_outcome.failure()).__name__, kill_outcome.failure(),
            )


@safe
def _pgrep_core(pattern: str):
    """Core puro: lista procesos del entry point (puede lanzar OSError/Timeout)."""
    return subprocess.run(
        ["pgrep", "-f", pattern],
        capture_output=True,
        text=True,
        timeout=5,
    )


@safe
def _ppid_core(pid: int) -> int:
    """Core puro: ppid de un proceso via ps (puede lanzar OSError/Timeout).

    ps devuelve '' si el proceso ya murió (el guard no lo toca).
    """
    outcome = subprocess.run(
        ["ps", "-o", "ppid=", "-p", str(pid)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    raw = outcome.stdout.strip()
    return int(raw) if raw else 0


@safe
def _parse_pid_core(pid_str: str) -> int:
    """Core puro: parsea un pid (puede lanzar ValueError)."""
    return int(pid_str.strip())


@safe
def _terminate_core(pid: int) -> None:
    """Core puro: termina un proceso huerfano (puede lanzar)."""
    os.kill(pid, 15)  # SIGTERM


def _self_test() -> int:
    """Verifica el protocolo MCP sin abrir stdio (modo test)."""
    from .mcp_adapter import _handle_request

    init = _handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    tools = _handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    print("walker MCP (via Router) self-test OK")
    print("  server:", init["result"]["serverInfo"])
    print("  tools:", [t["name"] for t in tools["result"]["tools"]])
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return _self_test()
    _kill_previous_instances()
    from .mcp_adapter import serve_stdio

    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
