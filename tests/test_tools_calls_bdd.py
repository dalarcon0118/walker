"""test_tools_calls_bdd.py — Llama las 3 tools del MCP walker (stdio real)."""

from __future__ import annotations

import asyncio
import json
import os

from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_ROOT, "bin", "walker-mcp")


def _params() -> StdioServerParameters:
    return StdioServerParameters(
        command="python3", args=[_BIN], cwd=_ROOT, env={**os.environ}
    )


def test_llamar_las_3_tools():
    async def _go():
        async with stdio_client(_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                lst = await session.call_tool("list", {})
                status = await session.call_tool("status", {"_project_id": "walker"})
                prepare = await session.call_tool(
                    "prepare", {"path": _ROOT}  # ya preparado → guard idempotente
                )
                return lst, status, prepare

    lst, status, prepare = asyncio.run(_go())

    print("\n===== 1/3 tools/call 'list' {} =====")
    p1 = json.loads(lst.content[0].text)
    projects = p1.get("data", {}).get("projects", [])
    print(f"type={p1.get('type')} status_code={p1.get('status_code')} "
          f"message={p1.get('message')}")
    for proj in projects:
        print(f"  - {proj['project_id']} → {proj['repo_path']}")

    print("\n===== 2/3 tools/call 'status' {{_project_id: walker}} =====")
    p2 = json.loads(status.content[0].text)
    print(f"type={p2.get('type')} project_id={p2.get('project_id')}")
    print(f"project={p2.get('project')}")
    print(f"checks={p2.get('checks')} | sprint={p2.get('sprint')}")
    print(f"warnings={p2.get('warnings')}")

    print("\n===== 3/3 tools/call 'prepare' {path: walker (ya preparado)} =====")
    p3 = json.loads(prepare.content[0].text)
    print(f"type={p3.get('type')} project_id={p3.get('project_id')}")
    print(f"message={p3.get('message')}")
    print("===== fin =====")

    # Contratos verificados
    assert p1.get("type") == "projects_list" and len(projects) > 0
    assert p2.get("type") == "project_health" and p2.get("checks", {}).get("project_json") == "ok"
    assert p3.get("type") == "already_exists", p3
