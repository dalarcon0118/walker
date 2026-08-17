"""Spec transforms side-effect: transform_spec.

Thin adapter: delega la orquestación Delta (spring_service, EDM, MemoryOrchestrator)
en SpecTransformService (orchestration/).
"""
from __future__ import annotations

import logging
from typing import Any

from core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING

logger = logging.getLogger(__name__)


@as_side_effect(scope=SCOPE_TRACKING)
def transform_spec(ctx: Any, params: dict) -> dict:
    """Transforma una Spec aplicando campos del EDM para el nodo actual.

    Thin adapter: delega en SpecTransformService (orchestration/).
    """
    from orchestration.spec_transform_service import SpecTransformService

    return SpecTransformService.transform_spec(ctx, params)


def register(engine: Any) -> None:
    engine.register("transform_spec", transform_spec)
