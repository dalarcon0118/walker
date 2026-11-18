"""side_effects/__init__.py — Auto-register all side effect handlers.

Same pattern as handlers: discover + register at startup.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry

# Ensure the spec side effect is importable alongside the others.
try:
    from . import spec as _spec  # noqa: F401
except Exception:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)


def register_all_side_effects(registry: SideEffectRegistry) -> None:
    """Discover and register all side effect handlers in this package."""
    import fast_mcp.features.tasks.side_effects as pkg

    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        if name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{pkg.__name__}.{name}")
            register_fn = getattr(module, "register", None)
            if register_fn:
                register_fn(registry)
        except Exception as exc:
            logger.warning("side_effects: failed to register '%s': %s", name, exc)
