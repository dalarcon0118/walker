"""shared_kernel/hooks — hooks/plugins de ciclo de vida (global + proyecto).

Ver `bus.py` para el contrato, el merge de scopes y la semántica de fallo.
"""

from fast_mcp.shared_kernel.hooks.bus import (
    GLOBAL_HOOKS_REL,
    PROJECT_HOOKS_REL,
    HookBus,
    HookResult,
    HookSpec,
)

__all__ = [
    "GLOBAL_HOOKS_REL",
    "PROJECT_HOOKS_REL",
    "HookBus",
    "HookResult",
    "HookSpec",
]
