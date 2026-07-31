"""DeltaTracking domain — Modelos para el índice de contexto federado.

Cada entrada del índice asocia un token (palabra/clave) con una lista
de referencias a objetos que lo contienen.
"""

from features.delta_tracking.domain.models import TokenIndex, IndexEntry

__all__ = ["TokenIndex", "IndexEntry"]
