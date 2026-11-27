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

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # solo type hint: el modo CLI no debe requerir `mcp`
    from mcp.server.fastmcp import FastMCP


def _build_open_arg_model() -> type:
    """ArgModel que tolera claves top-level arbitrarias (incl. `_`-prefijadas).

    - `extra="allow"`: acepta `_project_id`, `body`, etc. sin rechazarlas.
    - `model_dump_one_level` reinyecta las claves extra al runner (FastMCP
      solo vuelca los campos declarados; las claves extra viven en
      `__pydantic_extra__`).
    """

    # Imports perezosos: `pydantic` y `mcp` NO deben exigirse para el modo CLI
    # (el plugin usa `bin/walker3` en entornos sin esas deps). Se importan solo
    # al construir el modelo, al registrar tools en el server MCP.
    from pydantic import ConfigDict

    from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase

    class _OpenArgs(ArgModelBase):
        model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

        def model_dump_one_level(self) -> dict:
            data = super().model_dump_one_level()
            data.update(getattr(self, "__pydantic_extra__", None) or {})
            return data

    _OpenArgs.__name__ = "FastMCPOpenArgs"
    return _OpenArgs


_OPEN_ARG_MODEL = None


def _open_arg_model() -> type:
    """ArgModel cacheado, construido la primera vez que se registra una tool."""
    global _OPEN_ARG_MODEL
    if _OPEN_ARG_MODEL is None:
        _OPEN_ARG_MODEL = _build_open_arg_model()
    return _OPEN_ARG_MODEL


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

    async def _offload_runner(**kwargs: Any) -> Any:
        # FIX -32001: los runners del dominio son SYNC y pesados (scan CPG,
        # saves sqlite, json de blobs). Ejecutarlos inline en el event loop
        # bloqueaba TODAS las tools del server mientras uno corría (sample
        # nativo: task_step → json encoder → sqlite en el hilo principal).
        # El offload a worker thread libera el loop para I/O (sqlite3,
        # subprocess ast-grep y hashlib liberan el GIL); el CPU-bound puro
        # sigue siendo GIL-bound pero ya no_serializa blobs gigantes por
        # operación (fixes de storage). Los contextos se resuelven POR
        # LLAMADA (composition: sin caché) → cada conexión sqlite se crea y
        # usa en el MISMO worker thread (sin problema de thread-affinity).
        import anyio

        return await anyio.to_thread.run_sync(lambda: runner(**kwargs))

    _offload_runner.__name__ = name
    server.add_tool(_offload_runner, name=name, description=description)
    tool = server._tool_manager.get_tool(name)
    if tool is not None:
        # SSOT: el schema que ve el agente es el declarativo, no el inferido.
        tool.parameters = input_schema
        # Runtime: aceptar los kwargs top-level tal como los envía el agente.
        tool.fn_metadata.arg_model = _open_arg_model()
