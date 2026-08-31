"""fast_mcp.registration — Helper de registro de tools FastMCP (SSOT).

Resuelve la tensión entre dos contratos de FastMCP (ADR-WALKER-071 R-03bis):

  - Lo que VE el agente (`Tool.parameters` servido en `tools/list`) debe ser
    el `inputSchema` declarativo SSOT de `tool_catalog` — NO el re-inferido de
    la firma de la función.
  - Lo que VALIDA en runtime FastMCP (`fn_metadata.arg_model`, inferido de la
    firma) debe aceptar los argumentos top-level que el agente envía (incluidos
    los `_project_id`/`_payload`, que empiezan por `_` y FastMCP rechaza como
    nombres de parámetro).

La solución (adapter delgado, sin shims): registrar cada tool con un runner
`**kwargs` y sustituir el `arg_model` por un modelo que tolera claves
arbitrarias (`extra="allow"`) y las inyecta al runner. El `Tool.parameters`
queda como el SSOT. Así `fast_mcp` no re-infiere ni diverge el schema del
agente, y el despacho recibe los kwargs reales.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, create_model

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase


def _build_open_arg_model() -> type:
    """ArgModel que tolera claves top-level arbitrarias (incl. `_`-prefijadas).

    - `extra="allow"`: acepta `_project_id`, `body`, etc. sin rechazarlas.
    - `model_dump_one_level` reinyecta las claves extra al runner (FastMCP
      solo vuelca los campos declarados; las claves extra viven en
      `__pydantic_extra__`).
    """

    class _OpenArgs(ArgModelBase):
        model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

        def model_dump_one_level(self) -> dict:
            data = super().model_dump_one_level()
            data.update(getattr(self, "__pydantic_extra__", None) or {})
            return data

    _OpenArgs.__name__ = "FastMCPOpenArgs"
    return _OpenArgs


_OPEN_ARG_MODEL = _build_open_arg_model()


def register_domain_tool(
    server: FastMCP,
    name: str,
    runner: Any,
    description: str,
    input_schema: dict,
) -> None:
    """Registra una tool del dominio en el FastMCP server (SSOT schema + passthrough).

    Args:
        server: el FastMCP server.
        name: nombre de la tool (SSOT, ej. "status").
        runner: callable `runner(**kwargs)` que despacha al dominio.
        description: descripción SSOT de la tool.
        input_schema: `inputSchema` declarativo SSOT (tool_catalog) — servido al
            agente en `tools/list` (R-03bis), NO re-inferido de la firma.
    """
    server.add_tool(runner, name=name, description=description)
    tool = server._tool_manager.get_tool(name)
    if tool is not None:
        # SSOT: el schema que ve el agente es el declarativo, no el inferido.
        tool.parameters = input_schema
        # Runtime: aceptar los kwargs top-level tal como los envía el agente.
        tool.fn_metadata.arg_model = _OPEN_ARG_MODEL
