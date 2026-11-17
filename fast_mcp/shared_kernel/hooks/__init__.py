"""shared_kernel/hooks — hooks de ciclo de vida y paquetes instalables.

- `bus.py`: contrato de ejecución, merge de scopes y semántica de fallo.
- `events.py`: catálogo de eventos (los eventos no son strings libres).
- `package.py`: manifiesto `hook.json`, registro de instalados y compatibilidad.
"""

from fast_mcp.shared_kernel.hooks.bus import (
    GLOBAL_HOOKS_REL,
    PROJECT_HOOKS_REL,
    HookBus,
    HookResult,
    HookSpec,
)
from fast_mcp.shared_kernel.hooks.events import (
    EVENTS,
    EventSpec,
    is_known,
    known_events,
    validate_events,
)
from fast_mcp.shared_kernel.hooks.package import (
    HookManifestError,
    HookPackage,
    check_compat,
    install,
    installed,
    read_manifest,
    remove,
)

__all__ = [
    "EVENTS",
    "GLOBAL_HOOKS_REL",
    "PROJECT_HOOKS_REL",
    "EventSpec",
    "HookBus",
    "HookManifestError",
    "HookPackage",
    "HookResult",
    "HookSpec",
    "check_compat",
    "install",
    "installed",
    "is_known",
    "known_events",
    "read_manifest",
    "remove",
    "validate_events",
]
