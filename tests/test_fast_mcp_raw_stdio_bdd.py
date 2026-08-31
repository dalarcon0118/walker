"""test_fast_mcp_raw_stdio_bdd.py — Reproduce EXACTAMENTE lo que hace opencode:

Spawnea `bin/walker-mcp` como subproceso y le envía JSON-RPC crudo por stdin,
capturando la salida stdout real (lo que opencode parsea). Si stdout tiene un
traceback de Python o texto no-JSON, el protocolo se corrompe y opencode
marca el server desconectado.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


_WALKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_WALKER_ROOT, "bin", "walker-mcp")


def test_raw_stdio_no_stdout_corruption():
    proc = subprocess.Popen(
        ["python3", _BIN],
        cwd=_WALKER_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**os.environ},
    )

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    # JSON-RPC initialize
    send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0"},
        },
    })
    time.sleep(1.5)
    # tools/list
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    time.sleep(1.5)

    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    out = proc.stdout.read()
    err = proc.stderr.read()
    print("\n===== RAW STDOUT (lo que opencode parsea) =====")
    print(out[:1200])
    print("===== STDERR (logs, no deben ir a stdout) =====")
    print(err[:800])
    print("===== fin =====")

    # Debe ser JSON-RPC válido, no un traceback.
    assert out.strip().startswith("{") or out.strip().startswith("["), (
        f"stdout NO es JSON-RPC (server crasheó?): {out[:400]}"
    )
    first = json.loads(out.strip().splitlines()[0])
    assert first.get("id") == 1
    assert "result" in first, f"initialize no respondió result: {first}"
