"""Cliente MCP mínimo para la API de walker (JSON-RPC por stdio).

El harness no lee ni escribe archivos de walker: le habla por su API MCP.
Levanta el server una vez por corrida, negocia `initialize` y llama tools.

Uso:
    with WalkerClient() as walker:
        walker.call("trace_push", {...})
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _python_with_mcp() -> str:
    """El primer intérprete que puede importar `mcp` (el .venv de walker puede estar incompleto)."""
    env_python = os.environ.get("WALKER_PYTHON")
    candidates = (env_python, shutil.which("python3"), sys.executable, "/usr/bin/python3")
    for candidate in candidates:
        if not candidate:
            continue
        try:
            check = subprocess.run([candidate, "-c", "import mcp"],
                                   capture_output=True, timeout=10)
            if check.returncode == 0:
                return candidate
        except Exception:
            continue
    return sys.executable


class WalkerClient:
    def __init__(self, command: list[str] | None = None, timeout: int = 60) -> None:
        root = Path(__file__).resolve().parent.parent
        self.command = command or [_python_with_mcp(), str(root / "bin" / "walker-mcp")]
        self.cwd = root
        self.timeout = timeout
        self._id = 0
        self._proc: subprocess.Popen | None = None

    # -- ciclo de vida --------------------------------------------------
    def __enter__(self) -> "WalkerClient":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._send({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "walker-harness", "version": "0"},
            },
        })
        init = self._recv()
        if "error" in init:
            raise RuntimeError(f"initialize falló: {init['error']}")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def close(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None

    # -- transporte -----------------------------------------------------
    def _send(self, payload: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("walker no está corriendo")
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def _recv(self) -> dict:
        if not self._proc or not self._proc.stdout:
            raise RuntimeError("walker no está corriendo")
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("walker cerró el stdout (¿el server murió?)")
            line = line.strip()
            if line.startswith("{") and '"jsonrpc"' in line:
                return json.loads(line)

    # -- API ------------------------------------------------------------
    def call(self, name: str, arguments: dict) -> dict:
        """Llama una tool MCP y devuelve el JSON de su `content` (o `{"raw": texto}`)."""
        self._id += 1
        self._send({
            "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        response = self._recv()
        if "error" in response:
            raise RuntimeError(f"{name} falló: {response['error']}")
        content = (response.get("result") or {}).get("content") or []
        text = content[0].get("text") if content else None
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
