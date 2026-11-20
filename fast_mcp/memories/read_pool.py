"""memories/read_pool.py — lecturas pesadas en procesos, no en hilos.

Medido 2026-11-20: con el sustrato ya cacheado, `agent_context` caliente tarda
189 ms, pero **4 concurrentes cuestan 2,35×** una sola. El trabajo restante es
CPU (Graph.from_dict, construcción de relaciones, JSON) y el GIL lo serializa:
con hilos no hay forma de bajar de ~2×. AE-INV-07 pide ≤1,5×.

La salida es la que documenta AGENT-EFFICIENCY §2.7: **pool de procesos** para
los lectores. Cada request corre en un worker con su propio intérprete y sus
propias conexiones SQLite; el padre sólo serializa el payload y el resultado
(dicts JSON). No se comparte estado mutable entre procesos.

Desactivado bajo pytest (`PYTEST_CURRENT_TEST`) para que la suite no spawnee
workers; `WALKER_READ_POOL=0` lo apaga en runtime.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor

logger = logging.getLogger(__name__)

#: Tools de lectura que valen el salto de proceso (CPU-bound y repetidas).
TOOLS = frozenset({"brief", "agent_context", "preflight", "search_memories"})

_executor: ProcessPoolExecutor | None = None


def enabled() -> bool:
    if os.environ.get("WALKER_READ_POOL") == "0":
        return False
    return not os.environ.get("PYTEST_CURRENT_TEST")


def _workers() -> int:
    try:
        return max(1, int(os.environ.get("WALKER_READ_POOL_WORKERS", "4")))
    except ValueError:
        return 4


def _executor_for_reads() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=_workers())
    return _executor


def dispatch(name: str, chain_dir: str, kwargs: dict) -> dict:
    """Entry point del worker: resuelve el contexto y despacha la tool."""
    from fast_mcp.memories.context import MemoryContext
    from fast_mcp.memories.handlers import dispatch as dispatch_memory

    ctx = MemoryContext.resolve(chain_dir)
    if ctx is None:
        return {"error": f"Could not resolve project context for '{chain_dir}'"}
    return dispatch_memory(name, ctx, **kwargs)


def run(name: str, chain_dir: str, kwargs: dict) -> dict:
    """Ejecuta la tool en el pool y espera el dict. Sin fallback silencioso."""
    timeout = float(os.environ.get("WALKER_READ_POOL_TIMEOUT", "120"))
    future = _executor_for_reads().submit(dispatch, name, chain_dir, dict(kwargs))
    return future.result(timeout=timeout)


def _warm_worker() -> None:
    """Importa en el worker lo que los handlers importan en caliente."""
    import fast_mcp.features.code_analysis.application.services.code_context_service  # noqa: F401
    import fast_mcp.features.cognitive_knowledge.application.services.ckg_read  # noqa: F401
    import fast_mcp.features.event_tracing.application.services.trace_factory  # noqa: F401
    import fast_mcp.memories.handlers.knowledge  # noqa: F401


def warm() -> None:
    """Spawnea los workers y les hace los imports antes de la primera lectura.

    Sin esto, la primera ráfaga paga ~1,2 s por intérprete (spawn + imports).
    Es best-effort y en background: un fallo al precalentar no rompe el boot;
    la primera llamada real pagará el arranque.
    """
    if not enabled():
        return
    try:
        executor = _executor_for_reads()
        futures = [executor.submit(_warm_worker) for _ in range(_workers())]
        for future in futures:
            future.result(timeout=60)
    except Exception as exc:  # noqa: BLE001 — el pool sigue válido, sólo frío
        logger.warning("read_pool: warm falló (%s)", exc)


def shutdown() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
