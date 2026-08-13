"""features/delta_tracking/side_effects/ — Side effects de dominio del sprint."""

from __future__ import annotations

from typing import Any

from core.engine.infrastructure.side_effects import SideEffectEngine


def register_delta_tracking_side_effects(engine: SideEffectEngine) -> None:
    """Registra todos los side effects de dominio de delta_tracking en el engine."""
    from features.delta_tracking.side_effects.planning import (
        register as register_planning,
    )
    from features.delta_tracking.side_effects.spec_transform import (
        register as register_spec_transform,
    )
    from features.delta_tracking.side_effects.create_adr import (
        register as register_create_adr,
    )

    register_planning(engine)
    register_spec_transform(engine)
    register_create_adr(engine)
