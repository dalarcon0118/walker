"""DEPRECATED: funciones movidas a features.delta_tracking.application.index_service.

Usar DeltaGraphService.index_sprint() o index_all() en su lugar.
"""


def _project_root(chain_dir: str) -> str:
    """DEPRECATED."""
    import os
    return os.path.dirname(os.path.dirname(chain_dir))
