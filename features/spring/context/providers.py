"""spring/context/providers.py — Providers de contexto del sprint (state, task).

Providers de negocio de spring. SE REGISTRAN EXTERNAMENTE
via register_providers() — el engine NO los conoce.

Incluye:
  - TaskProvider:  la tarea actual desde plan.json (specifications)
  - StateProvider: estado del sprint (via QueryManager)
"""

from __future__ import annotations
import json
from typing import Any

from features.engine.context.models import ContextPiece
from features.engine.context.registry import ContextProvider
from features.engine.context.format import fmt_tokens, format_state_result


class TaskProvider(ContextProvider):
    """Capa: task — la tarea actual del sprint (desde plan.json).

    Entrega la spec activa (por current_spec_index) con todos sus campos:
    objective, type, domain, result_state, predicates, files, risks, etc.
    """

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: task — current task specification (objective, type, domain, result_state, predicates, files, risks). Base your work on this."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        chain_dir = getattr(ctx, "chain_dir", None)
        if not chain_dir:
            return None

        # Fuente unica: delta (SpringService). Fallback a plan.json si
        # el spring no esta en el delta.
        specs: list[dict] = []
        try:
            from features.delta_tracking.application.services.spring_service import (
                SpringService,
            )

            spring_svc = SpringService.from_chain_dir(chain_dir)
            if spring_svc:
                import os

                spring_id = os.path.basename(os.path.normpath(chain_dir))
                spring_svc.ensure_spring_imported(chain_dir, spring_id)
                specs = spring_svc.get_specs_legacy(spring_id)
        except Exception:
            pass
        if not specs:
            # Fallback legacy: plan.json
            plan_path = os.path.join(chain_dir, "plan.json")
            if not os.path.exists(plan_path):
                return None
            try:
                with open(plan_path) as f:
                    plan = json.load(f)
                specs = plan.get("specifications", plan.get("tasks", []))
            except (json.JSONDecodeError, OSError):
                return None
        if not specs:
            return None

        # Indice de la spec actual (SSOT: delta — nodo spring)
        current_idx = 0
        try:
            from features.delta_tracking.application.services.spring_service import (
                SpringService,
            )

            spring_svc = SpringService.from_chain_dir(chain_dir)
            if spring_svc:
                import os

                spring_id = os.path.basename(os.path.normpath(chain_dir))
                spring_svc.ensure_spring_imported(chain_dir, spring_id)
                ctx_state = spring_svc.get_spring_context(spring_id)
                current_idx = int(
                    ctx_state.get(
                        "current_spec_index",
                        ctx_state.get("current_task_index", 0),
                    )
                )
        except Exception:
            pass
        if current_idx >= len(specs):
            current_idx = len(specs) - 1

        t = specs[current_idx]
        sid = t.get("spec_id", t.get("task_id", "?"))
        lines = [
            f"task_id: {sid}",
            f"objective: {t.get('objective', '')}",
            f"type: {t.get('type', '')}",
            f"domain: {t.get('domain', '')}",
            f"status: {t.get('status', 'draft')}",
        ]
        if t.get("result_state"):
            lines.append(f"result_state: {t.get('result_state', '')}")
        if t.get("task_description"):
            lines.append(f"description: {t.get('task_description', '')[:300]}")
        preds = t.get("validation", {}).get("predicates", [])
        if preds:
            lines.append("predicates:")
            for p in preds:
                lines.append(f"  • {p}")
        files = t.get("impacted_artifacts", {}).get("files", [])
        if files:
            lines.append("files:")
            for f in files:
                lines.append(f"  • {f}")

        content = "\n".join(lines)
        return ContextPiece(
            provider="task",
            name=f"task: {sid}",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=5,
        )


class StateProvider(ContextProvider):
    """Capa: estado del sprint actual. Layer: state.

    Delega en QueryManager para consultar el estado del delta y workflow.
    """

    LAYER = "state"

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: state — current workflow position, sprint goal, task inventory, project identity."

    def collect(
        self, config: dict, ctx: Any, query_manager: Any = None
    ) -> ContextPiece | None:
        if query_manager is None:
            return None
        filters = config.get("query", {})
        if isinstance(filters, str):
            filters = {}
        result = query_manager.query_layer(self.LAYER, filters)
        if not result.get("ok", False):
            return None

        content = format_state_result(result)
        return ContextPiece(
            provider="state",
            name="sprint state",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=fmt_tokens(content),
            priority=0,
        )


def register_providers(registry: Any) -> None:
    """Registra los providers de spring en un ContextProviderRegistry."""
    registry.register("task", TaskProvider())
    registry.register("state", StateProvider())
