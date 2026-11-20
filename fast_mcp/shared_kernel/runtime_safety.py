"""fast_mcp.shared_kernel.runtime_safety — invariantes del proceso MCP.

AE-INV-09 (docs/AGENT-EFFICIENCY.md §3): una tool cancelada por el cliente no
puede tumbar el server.

Mecánica medida (mcp 1.23.3):

1. El cliente cancela (`notifications/cancelled`) al vencer su timeout.
2. `RequestResponder.cancel()` marca `_completed = True` y responde
   `ErrorData(code=0, message="Request cancelled")`.
3. El handler sigue corriendo (es síncrono, no ve la cancelación) y al terminar
   `_handle_request` llama `message.respond(result)`.
4. `RequestResponder.respond` hace `assert not self._completed` → AssertionError.
5. La excepción sale del `anyio` task group → el proceso termina con code 1 y el
   cliente retira el server del catálogo. Reproducido con `run_tests` (suite de
   37 s contra timeout de 30 s) y registrado en
   `~/.local/share/opencode/log/opencode.log:147578-147581`.

El fix no puede vivir en el SDK (se reinstala): se envuelve `respond` una vez
por proceso, de forma idempotente, y una respuesta tardía se descarta con un
warning en vez de matar el server.
"""

from __future__ import annotations

import logging

from anyio import BrokenResourceError, ClosedResourceError
from mcp.shared import session as _mcp_session

logger = logging.getLogger(__name__)

_PATCH_MARK = "_walker_cancellation_safe"


def install_cancellation_guard() -> bool:
    """Hace idempotente la respuesta de un request ya cancelado.

    Returns:
        True si aplicó el parche en esta llamada; False si ya estaba aplicado.
    """
    responder_cls = _mcp_session.RequestResponder
    patched = getattr(responder_cls.respond, _PATCH_MARK, False)
    if patched:
        return False

    original_respond = responder_cls.respond

    async def respond(self, response):  # type: ignore[no-untyped-def]
        # Mismo contrato de entrada que el original: fuera del context manager
        # sigue siendo un bug del caller.
        if not self._entered:  # pragma: no cover - contrato del SDK
            raise RuntimeError("RequestResponder must be used as a context manager")
        try:
            await original_respond(self, response)
        except AssertionError:
            # El cliente ya canceló y recibió `Request cancelled`. La respuesta
            # tardía no tiene destinatario: se descarta, no se propaga.
            logger.warning(
                "MCP request %s ya respondido (cancelado por el cliente); "
                "se descarta el resultado tardío",
                self.request_id,
            )
        except (BrokenResourceError, ClosedResourceError) as exc:
            # El stream murió (cliente cerrado): tampoco es fatal.
            logger.warning("MCP request %s: stream cerrado (%s)", self.request_id, exc)

    setattr(respond, _PATCH_MARK, True)
    responder_cls.respond = respond  # type: ignore[method-assign]
    logger.info("MCP cancellation guard instalado (AE-INV-09)")
    return True
