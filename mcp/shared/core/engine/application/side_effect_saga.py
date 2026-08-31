"""application/side_effect_saga.py — Orquestador Saga de side-effects vía ActionCatalog.

UNIFICACIÓN REAL (ADR-WALKER-067 §7.2): el engine consume SOLO `actions`
(ActionCatalog) como puerto de ejecución. Este servicio orquesta los
side-effects declarados en el grafo (envelopes, optional, idempotencia,
compensación en orden inverso) usando `actions.resolve(name).handler` para
dispatch — el mismo mecanismo que SideEffectEngine (infra) usaba vía su
`_action_catalog`. El engine ya NO recibe `side_effect_engine` en su
constructor/bundle.

Nota: los helpers de normalización/tracing se importan de infrastructure
(application → infrastructure es dependencia permitida, domain-first).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from returns.pipeline import is_successful

from shared.core.engine.domain.values import SideEffectDef
from shared.core.engine.domain.workflow_action import ActionCatalog
from shared.core.engine.infrastructure.side_effects import (
    _run_compensation_handler_core,
    _run_side_effect_handler_core,
    _trace_dispatch,
)

logger = logging.getLogger(__name__)

_CPG_BUILD_EFFECT_TYPES = {"update_cpg", "build_cpg"}


class SideEffectSaga:
    """Ejecuta side-effects declarativos con Saga pattern vía ActionCatalog.

    Saga: ejecuta efectos en orden; si falla uno NO optional, compensa los ya
    ejecutados en orden inverso (la compensación es la que DECLARA EL GRAFO,
    SideEffectDef.compensation — fuente única SPEC-014 Fase 0).
    """

    def __init__(self, actions: ActionCatalog | None = None, tracer: Any = None):
        self._actions = actions
        self._tracer = tracer
        self._idempotency_store: dict[str, dict[str, dict]] = {}

    def run(
        self,
        ctx: Any,
        effects: list[SideEffectDef] | None = None,
        outbox: Any = None,
    ) -> list[dict]:
        """Ejecuta side-effects en orden con Saga pattern + Outbox atómico.

        Args:
            outbox: SideEffectOutbox opcional. Si se provee, cada efecto se
            enqueue antes de ejecutar y se marca completed/failed/compensated
            (ADR-WALKER-067 F4/H).

        Returns:
            Lista de resultados con: type, ok, optional, result, compensated,
            compensation_result, idempotent, compensation.
        """
        if not effects:
            return []

        results: list[dict] = []
        executed_count = 0

        for i, effect in enumerate(effects):
            started = time.time()

            if outbox is not None:
                outbox_entry = outbox.enqueue(effect, effect.idempotency_key)

            if effect.idempotency_key:
                cached = self._get_tracked(ctx, effect.idempotency_key)
                if cached is not None:
                    self._trace_side_effect(
                        ctx, effect.type, cached, ok=True, started=started,
                        idempotent=True,
                    )
                    results.append({
                        "type": effect.type,
                        "ok": True,
                        "optional": effect.optional,
                        "result": cached,
                        "compensated": False,
                        "compensation_result": None,
                        "idempotent": True,
                        "compensation": effect.compensation,
                    })
                    if outbox is not None:
                        outbox.mark_completed(outbox_entry, cached)
                    executed_count += 1
                    continue

            try:
                result = self._execute_single(ctx, effect)
                failed = result is None and not effect.optional
                self._trace_side_effect(
                    ctx, effect.type, result, ok=result is not None, started=started,
                )
                entry = {
                    "type": effect.type,
                    "ok": result is not None,
                    "optional": effect.optional,
                    "result": result if result is not None else (
                        f"Side-effect '{effect.type}' returned None"
                    ),
                    "compensated": False,
                    "compensation_result": None,
                    "idempotent": False,
                    "compensation": effect.compensation,
                }
                results.append(entry)

                if result is not None:
                    executed_count += 1

                if effect.idempotency_key and result is not None:
                    self._track_result(ctx, effect.idempotency_key, result)

                if outbox is not None and result is not None:
                    outbox.mark_completed(outbox_entry, result)

                if failed:
                    if outbox is not None:
                        self._compensate_outbox(ctx, results, i, outbox)
                    else:
                        entry["compensation_result"] = self.compensate(ctx, results, i)
                    for prev in results[:i]:
                        if prev.get("ok"):
                            prev["compensated"] = True
                    break

            except Exception as e:
                self._trace_side_effect(
                    ctx, effect.type, {"error": str(e)}, ok=False, started=started,
                )
                entry = {
                    "type": effect.type,
                    "ok": False,
                    "optional": effect.optional,
                    "result": str(e),
                    "compensated": False,
                    "compensation_result": None,
                    "idempotent": False,
                }
                results.append(entry)
                if outbox is not None:
                    outbox.mark_failed(outbox_entry, str(e))

                if not effect.optional:
                    if outbox is not None:
                        self._compensate_outbox(ctx, results, i, outbox)
                    else:
                        entry["compensation_result"] = self.compensate(ctx, results, i)
                    for prev in results[:i]:
                        if prev.get("ok"):
                            prev["compensated"] = True
                else:
                    logger.warning(
                        "Side-effect '%s' (optional) failed: %s — continuing",
                        effect.type, e,
                    )
                break

        return results

    def _compensate_outbox(
        self, ctx: Any, results: list[dict], failed_index: int, outbox: Any,
    ) -> None:
        """Compensa los efectos exitosos previos y marca el outbox."""
        comps = self.compensate(ctx, results, failed_index)
        for prev in results[:failed_index]:
            if not prev.get("ok"):
                continue
            for outbox_entry in outbox.all():
                if (
                    outbox_entry.effect_type == prev.get("type")
                    and outbox_entry.status == "completed"
                ):
                    outbox.mark_compensated(outbox_entry, comps)
                    break

    def _execute_single(self, ctx: Any, effect: SideEffectDef) -> dict | str | None:
        """Ejecuta un único side-effect resolviendo el handler vía ActionCatalog."""
        etype = effect.type
        params = effect.params

        handler = None
        if self._actions is not None:
            action = self._actions.resolve(etype)
            if action is not None:
                handler = action.handler

        if handler is None:
            logger.warning("Unknown side-effect type: %s", etype)
            return None

        outcome = _run_side_effect_handler_core(handler, ctx, params)
        if is_successful(outcome):
            value = outcome.value_or({})
            if isinstance(value, dict) and (
                value.get("status") == "failed" or value.get("ok") is False
            ):
                logger.warning(
                    "Side-effect '%s' devolvió envelope fallido (%s) — tratado como None",
                    etype, value.get("summary", "") or value.get("error", ""),
                )
                return None
            return value
        logger.warning(
            "Side-effect '%s' handler falló (%s: %s) — tratado como None (fail-loud)",
            etype, type(outcome.failure()).__name__, outcome.failure(),
        )
        return None

    def compensate(
        self, ctx: Any, results: list[dict], failed_index: int,
    ) -> list[dict]:
        """Compensa efectos ejecutados exitosamente en orden inverso."""
        compensation_results: list[dict] = []
        for i in range(failed_index - 1, -1, -1):
            prev = results[i]
            if not prev.get("ok"):
                continue
            effect_type = prev["type"]
            prev_result = prev.get("result", {})

            try:
                comp_type = prev.get("compensation") or ""
                if not comp_type:
                    logger.warning(
                        "No compensation for '%s' — continuing (Saga non-blocking)",
                        effect_type,
                    )
                    compensation_results.append({
                        "type": f"compensate_{effect_type}",
                        "ok": True,
                        "result": "no compensation needed (logged warning)",
                        "skipped": True,
                    })
                    continue

                comp_result = self._compensate_single(ctx, comp_type, prev_result)
                compensation_results.append({
                    "type": f"compensate_{effect_type}",
                    "ok": True,
                    "result": comp_result,
                })
            except Exception as e:
                logger.error("Compensation for '%s' failed: %s", effect_type, e)
                compensation_results.append({
                    "type": f"compensate_{effect_type}",
                    "ok": False,
                    "result": str(e),
                })
        return compensation_results

    def _compensate_single(
        self, ctx: Any, compensation_type: str, original_result: dict | str | None,
    ) -> dict | str | None:
        """Ejecuta la compensación REAL declarada por el grafo (vía ActionCatalog)."""
        handler = None
        if self._actions is not None:
            action = self._actions.resolve(compensation_type)
            if action is not None:
                handler = action.handler
        if handler is None:
            logger.warning(
                "No handler registrado para compensación '%s' — rollback no ejecutado",
                compensation_type,
            )
            return {
                "compensation": compensation_type,
                "ok": False,
                "error": f"no handler registered for compensation '{compensation_type}'",
            }
        outcome = _run_compensation_handler_core(handler, ctx)
        if is_successful(outcome):
            return outcome.value_or({})
        failure = outcome.failure()
        logger.error("Compensación '%s' falló (%s: %s)", compensation_type,
                     type(failure).__name__, failure)
        return {"compensation": compensation_type, "ok": False, "error": str(failure)}

    # ── Idempotencia (in-memory) ────────────────────────────────────────

    def _track_result(self, ctx: Any, key: str, result: dict | str | None) -> None:
        chain_key = ctx.chain_dir
        if chain_key not in self._idempotency_store:
            self._idempotency_store[chain_key] = {}
        self._idempotency_store[chain_key][key] = {
            "result": result,
            "timestamp": getattr(ctx, "started_at", ""),
        }

    def _get_tracked(self, ctx: Any, key: str) -> dict | str | None:
        chain_store = self._idempotency_store.get(ctx.chain_dir, {})
        tracked = chain_store.get(key)
        if tracked:
            return tracked.get("result")
        return None

    def clear_idempotency(self, chain_dir: str) -> None:
        """Limpia idempotency store para un chain_dir."""
        self._idempotency_store.pop(chain_dir, None)

    # ── Trazabilidad ────────────────────────────────────────────────────

    def _trace_side_effect(
        self, ctx: Any, effect_type: str, result: dict | str | None,
        ok: bool, started: float, idempotent: bool = False,
    ) -> None:
        """Registra un evento side_effect_execute en el TraceGraph (G-W1)."""
        if not self._tracer or not getattr(ctx, "chain_dir", ""):
            return
        duration_ms = int((time.time() - started) * 1000)
        scope = ""
        signals_keys: list[str] = []
        if isinstance(result, dict):
            scope = result.get("scope", "") or ""
            signals = result.get("signals", {})
            if isinstance(signals, dict):
                signals_keys = sorted(k for k in signals if k != "data")[:10]
        data = {
            "tool": effect_type,
            "ok": bool(ok),
            "duration_ms": duration_ms,
            "channel": "workflow",
            "idempotent": bool(idempotent),
            "scope": scope or "tracking",
            "signals_keys": signals_keys,
        }
        _trace_dispatch(
            self._tracer, ctx.chain_dir, "side_effect_execute", data,
            task_id=getattr(ctx, "task_id", "") or effect_type,
            session_id=getattr(ctx, "session_id", ""),
        )