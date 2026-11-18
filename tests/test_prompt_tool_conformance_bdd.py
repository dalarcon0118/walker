"""BDD: un prompt no puede mandar a una tool que el perfil no expone.

Medido 2026-11-18: los templates de workflow decían
`walker <project>/search_memories` y `recall_knowledge`, pero el perfil `agent`
no las exponía. El agente recibía una instrucción imposible y la tool
respondía "Unknown tool". Es una clase de error que se elimina en el build.

Contrato: toda tool referenciada como `walker <project>/<tool>` en un template
de nodo tiene que estar en `tool_profiles.AGENT`.
"""
from __future__ import annotations

import glob
import json
import re

from fast_mcp.tool_profiles import AGENT

TEMPLATES = "fast_mcp/features/workflow/application/prompts/templates/nodes/**/*.prompt.json"
COMMAND = re.compile(
    r"walker\s+(?:<[^>]+>|\$\{?[a-z_]+\}?|[\w.-]+)/([a-zA-Z_][\w]*)"
)


def _referenced_tools() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in glob.glob(TEMPLATES, recursive=True):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001 — un template roto lo detecta otro test
            continue
        text = str(data.get("template") or data.get("prompt") or "")
        for tool in COMMAND.findall(text):
            found.setdefault(tool, set()).add(path.split("nodes/")[-1])
    return found


def test_los_prompts_solo_referencian_tools_del_perfil():
    referenced = _referenced_tools()
    assert referenced, "no se extrajo ninguna tool: el test no está midiendo nada"

    missing = {tool: sorted(files) for tool, files in referenced.items() if tool not in AGENT}

    assert missing == {}, (
        "prompts que mandan a tools fuera del perfil agent "
        f"(agregalas a AGENT o reescribí el prompt): {missing}"
    )


def test_el_perfil_agent_no_expone_telemetria_ni_ciclo_de_vida():
    # El plugin escribe la traza y abre/cierra sesiones; el modelo no.
    for tool in ("trace_push", "trace_message", "session_open", "session_close"):
        assert tool not in AGENT, f"{tool} no debería estar en la superficie del agente"
