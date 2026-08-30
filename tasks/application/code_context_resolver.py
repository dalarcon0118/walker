"""code_context service resolver.

Factory that builds CodeContextService for the task layer.
Keeps the task free of infrastructure concerns.
"""
from __future__ import annotations

from typing import Any


def resolve_code_context_service(chain_dir: str, service: Any | None = None) -> Any | None:
    """Resolves CodeContextService for code_context task.

    Args:
        chain_dir: sprint directory.
        service: injected instance (preferred). If None, builds from chain_dir.

    Returns:
        CodeContextService or None if it cannot be built.
    """
    if service is not None:
        return service
    try:
        from features.code_analysis.application.services.code_context_service import (
            CodeContextService,
        )
        return CodeContextService(chain_dir=chain_dir)
    except Exception:
        return None
