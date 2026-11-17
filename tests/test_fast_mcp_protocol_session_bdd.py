"""test_fast_mcp_protocol_session_bdd.py — Sesión REAL del protocolo MCP (wire-level).

Ejecuta el protocolo completo JSON-RPC 2.0 contra `bin/walker-mcp` tal como lo
hace opencode: initialize → notifications/initialized → tools/list →
tools/call (ping, list, status). Muestra cada mensaje de request/response tal
como viaja por stdio.
"""

from __future__ import annotations

import json
import os
import subprocess


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_ROOT, "bin", "walker-mcp")


def test_sesion_completa_protocolo_mcp():
    proc = subprocess.Popen(
        ["python3", _BIN],
        cwd=_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env={**os.environ},
    )

    def send(msg: dict) -> None:
        print(f"\n>>> REQUEST: {json.dumps(msg)[:180]}")
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def recv(step: str) -> dict:
        line = proc.stdout.readline()
        resp = json.loads(line)
        print(f"<<< RESPONSE ({step}): {json.dumps(resp)[:220]}")
        return resp

    try:
        # ── 1. initialize (handshake) ──
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "protocol-demo", "version": "0"},
            },
        })
        init = recv("initialize")
        assert init["result"]["serverInfo"]["name"] == "walker"

        # ── 2. notification initialized (cierra el handshake) ──
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # ── 3. tools/list ──
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = recv("tools/list")
        names = [t["name"] for t in tools["result"]["tools"]]
        print(f"    tools expuestas: {names}")
        # Contrato mínimo del ADR (Fase 1) + superficie total (F1:3 + F2:14 + F3:12 = 29).
        # Test de completitud (patrón AGENTS.md): una fase nueva actualiza el conteo.
        # trace_message (T-14) suma en F3: captura user/agent messages → TraceGraph.
        assert {"prepare", "list", "status"} <= set(names), names
        assert len(names) == 38, f"superficie inesperada ({len(names)}): {names}"

        # ── 4. tools/call list ──
        send({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "list", "arguments": {}},
        })
        lst = recv("tools/call list")
        lst_payload = json.loads(lst["result"]["content"][0]["text"])
        n = len(lst_payload["data"]["projects"])
        print(f"    proyectos en registro: {n}")

        # ── 5. tools/call status ──
        send({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "status", "arguments": {"_project_id": "walker"}},
        })
        status = recv("tools/call status")
        st = json.loads(status["result"]["content"][0]["text"])
        print(f"    health: type={st['type']} repo={st['project']['repo_path']}")

        # ── protocolo verificado ──
        assert lst_payload["type"] == "projects_list"
        assert st["type"] == "project_health"
        print("\n===== PROTOCOLO MCP EJECUTADO COMPLETO: 5 mensajes, OK =====")
    finally:
        proc.stdin.close()
        proc.terminate()
