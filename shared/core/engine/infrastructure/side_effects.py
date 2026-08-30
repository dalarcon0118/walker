"""
infrastructure/side_effects.py — SideEffectEngine con Saga/Compensation.

Implementa Saga pattern (TASK-008):
  1. Ejecuta side-effects en orden secuencial
  2. Si un efecto NO optional falla, compensa los exitosos en orden inverso
  3. Idempotency key evita doble ejecucion del mismo efecto
  4. Efectos sin compensacion loggean warning y continuan (no bloquean)
  5. Compensaciones reales (SPEC-014 Fase 0): la compensación es la que
     DECLARA EL GRAFO (SideEffectDef.compensation) y se resuelve contra el
     MISMO catálogo de handlers por nombre — nunca un mapa hardcodeado ni
     dicts fabricados de éxito.

SRP: Unica responsabilidad = ejecutar y compensar side-effects declarativos.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from ..domain.models import SideEffectDef, ExecutionContext

logger = logging.getLogger(__name__)

# Efectos cuyo build del CPG certifica su espejo post-index (SPEC-002).
# update_cpg (full/incremental), build_project_graph y update_code_index
# producen el evento cpg_indexed además del side_effect_execute genérico.
_CPG_BUILD_EFFECT_TYPES = {
    "update_cpg",
    "build_project_graph",
    "update_code_index",
}


@safe
def _run_side_effect_handler_core(handler: Any, ctx: Any, params: dict) -> dict:
    """Core puro: ejecuta un handler modular de side effect (puede lanzar).

    Normaliza al envelope estándar. @safe captura el fallo como Failure y el
    caller decide (convention-001, sin try/except).
    """
    from ..domain.side_effect_contract import normalize

    result = handler(ctx, params)
    return normalize(
        result,
        default_type=getattr(handler, "__name__", "side_effect"),
        default_scope="tracking",
    )


@safe
def _run_compensation_handler_core(handler: Any, ctx: Any) -> dict:
    """Core puro: ejecuta el handler REAL de compensación del catálogo.

    El handler recibe (ctx, params={}) y su resultado se normaliza al
    envelope estándar (SPEC-014 Fase 0: la compensación ejecuta el handler
    registrado, nunca fabrica dicts de éxito). Puede lanzar → @safe lo
    captura como Failure y el wrapper decide (convention-001).
    """
    from ..domain.side_effect_contract import normalize

    result = handler(ctx, {})
    return normalize(
        result,
        default_type=getattr(handler, "__name__", "compensation"),
        default_scope="tracking",
    )


@safe
def _trace_dispatch_core(
    tracer: Any,
    chain_dir: str,
    event_type: str,
    data: dict,
    task_id: str,
    session_id: str,
) -> None:
    """Core puro: persiste un evento en el TraceGraph (puede lanzar).

    Se aisla en un core @safe para que el engine no necesite try/except:
    la pérdida de un evento es un fallo visible (G-W4), nunca silenciado,
    y el wrapper _trace_dispatch degrada con log por nivel.
    """
    tracer.trace(
        chain_dir,
        event_type,
        data=data,
        task_id=task_id,
        session_id=session_id,
    )


def _trace_dispatch(
    tracer: Any,
    chain_dir: str,
    event_type: str,
    data: dict,
    task_id: str = "",
    session_id: str = "",
) -> None:
    """Wrapper convention-001: nunca lanza; log visible ante pérdida (G-W4)."""
    outcome = _trace_dispatch_core(tracer, chain_dir, event_type, data, task_id, session_id)
    if not is_successful(outcome):
        logger.error(
            "side_effects: fallo al registrar %s (%s: %s)",
            event_type,
            type(outcome.failure()).__name__,
            outcome.failure(),
        )


class SideEffectEngine:
    """Ejecuta side-effects declarativos con Saga pattern.

    Saga: ejecuta efectos en orden, si falla uno NO optional,
    compensa los ya ejecutados en orden inverso.

    Metodos:
        execute(ctx, effects): Ejecuta efectos en orden con Saga.
        compensate(ctx, results, failed_index): Compensa los exitosos.
        _execute_single(ctx, effect): Ejecuta un unico efecto.
        _compensate_single(ctx, effect_type, params): Ejecuta compensacion.
        _track_result(ctx, key, result): Trackea resultado para idempotencia.
        _get_tracked(ctx, key): Obtiene resultado previo si existe.
    """

    def __init__(self, task_registry: Any = None, tracer: Any = None, action_catalog: Any = None) -> None:
        self._task_registry = task_registry
        self._tracer = tracer
        self._action_catalog = action_catalog
        self._handlers: dict[str, Any] = {}
        self._idempotency_store: dict[str, dict[str, dict]] = {}

    def register(self, effect_type: str, handler: Any) -> None:
        """Registra un handler para un tipo de side-effect.

        Args:
            effect_type: Nombre del side-effect (ej: 'update_cpg').
            handler: Funcion (ctx, params) -> dict.
        """
        self._handlers[effect_type] = handler

    def run_compensation_handler(self, handler_name: str, ctx: Any) -> Any:
        """Adapter del puerto ISideEffectEngine para compensacion BPMN.

        Ejecuta un compensation handler por nombre y retorna un Result
        (Success(handler) / Failure). Si el handler no esta registrado,
        retorna None (el dominio lo trata como 'not found' y loggea).

        No usa self._task_registry/_tracer (los compensation handlers son
        funciones puras del dominio, sin dependencias de infra en runtime).
        """
        handler = self._handlers.get(handler_name)
        if handler is None:
            return None
        return _run_compensation_handler_core(handler, ctx)

    def execute(
        self,
        ctx: ExecutionContext,
        effects: list[SideEffectDef] | None = None,
        outbox: Any | None = None,
    ) -> list[dict]:
        """Ejecuta side-effects en orden con Saga pattern + Outbox atomicidad.

        Args:
            ctx: ExecutionContext del sub-estado actual.
            effects: Lista de SideEffectDef a ejecutar.
            outbox: SideEffectOutbox opcional para atomicidad garantizada.
                    Si se provee, los efectos se enqueuen antes de ejecutar
                    y se marcan completed/compensated en el outbox.

        Returns:
            Lista de resultados, cada uno con:
                type: str
                ok: bool
                result: dict | str | None
                compensated: bool — True si fue compensado
                compensation_result: dict | None — resultado de compensacion
                idempotent: bool — True si fue servido de cache
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
                        ctx, effect.type, cached,
                        ok=True, started=started, idempotent=True,
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
                    ctx, effect.type, result,
                    ok=result is not None, started=started,
                )
                entry = {
                    "type": effect.type,
                    "ok": result is not None,
                    "optional": effect.optional,
                    "result": result if result is not None else f"Side-effect '{effect.type}' returned None",
                    "compensated": False,
                    "compensation_result": None,
                    "idempotent": False,
                    # Fuente única (SPEC-014 Fase 0): la compensación es la
                    # que DECLARA EL GRAFO (SideEffectDef.compensation). El
                    # engine ya no usa un mapa interno hardcodeado.
                    "compensation": effect.compensation,
                }
                results.append(entry)

                if result is not None:
                    executed_count += 1

                if effect.idempotency_key and result is not None:
                    self._track_result(ctx, effect.idempotency_key, result)

                if failed:
                    if outbox is not None:
                        self._compensate_outbox(ctx, results, i, outbox)
                    else:
                        compensation = self.compensate(ctx, results, i)
                        entry["compensation_result"] = compensation
                    for prev in results[:i]:
                        if prev.get("ok"):
                            prev["compensated"] = True
                    break

                if outbox is not None:
                    outbox.mark_completed(outbox_entry, result if result is not None else {})

            except Exception as e:
                self._trace_side_effect(
                    ctx, effect.type, {"error": str(e)},
                    ok=False, started=started,
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
                        compensation = self.compensate(ctx, results, i)
                        entry["compensation_result"] = compensation
                    for prev in results[:i]:
                        if prev.get("ok"):
                            prev["compensated"] = True
                else:
                    logger.warning(
                        "Side-effect '%s' (optional) failed: %s — continuing",
                        effect.type,
                        e,
                    )
                break

        return results

    def _compensate_outbox(
        self,
        ctx: ExecutionContext,
        results: list[dict],
        failed_index: int,
        outbox: Any,
    ) -> None:
        """Compensa efectos exitosos usando el outbox.

        Marca las entradas del outbox como compensadas en orden inverso.
        """
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
                    continue

                comp_result = self._compensate_single(ctx, comp_type, prev_result)

                for outbox_entry in outbox.all():
                    if outbox_entry.effect_type == effect_type and outbox_entry.status == "completed":
                        outbox.mark_compensated(outbox_entry, comp_result)
                        break

                prev["compensation_result"] = [{
                    "type": f"compensate_{effect_type}",
                    "ok": True,
                    "result": comp_result,
                }]
            except Exception as e:
                logger.error(
                    "Compensation for '%s' failed: %s",
                    effect_type,
                    e,
                )
                prev["compensation_result"] = [{
                    "type": f"compensate_{effect_type}",
                    "ok": False,
                    "result": str(e),
                }]

    def _trace_side_effect(
        self,
        ctx: ExecutionContext,
        effect_type: str,
        result: dict | str | None,
        ok: bool,
        started: float,
        idempotent: bool = False,
    ) -> None:
        """Registra un evento side_effect_execute en el TraceGraph (G-W1).

        Se emite desde el punto de paso (execute) para TODOS los side-effects
        del workflow (update_cpg, index_delta, save_ckg, run_script, etc.)
        sin acoplar a los handlers modulares: ellos solo devuelven su
        contrato; este engine registra la telemetria.

        Contrato del evento (SPEC-002):
            data: {tool, ok, duration_ms, channel: "workflow", idempotent,
                   scope, signals_keys}
        """
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
        if not scope:
            # Contrato SPEC-002: scope siempre presente en el evento.
            # Degradación segura a "tracking" (misma política que normalize).
            scope = "tracking"
        data = {
            "tool": effect_type,
            "ok": bool(ok),
            "duration_ms": duration_ms,
            "channel": "workflow",
            "idempotent": bool(idempotent),
            "scope": scope,
            "signals_keys": signals_keys,
        }
        _trace_dispatch(
            self._tracer,
            ctx.chain_dir,
            "side_effect_execute",
            data,
            task_id=getattr(ctx, "task_id", "") or effect_type,
            session_id=getattr(ctx, "session_id", ""),
        )
        # SPEC-002 G-W1: el build del CPG certifica su espejo con un evento
        # cpg_indexed post-build (fingerprint_match + stats + quality).
        if (
            ok
            and isinstance(result, dict)
            and result.get("scope") == "code_index"
            and str(result.get("type", "")) in _CPG_BUILD_EFFECT_TYPES
        ):
            self._trace_cpg_indexed(ctx, result)

    def _trace_cpg_indexed(
        self,
        ctx: ExecutionContext,
        result: dict,
    ) -> None:
        """Registra un evento cpg_indexed post-build en el TraceGraph (SPEC-002).

        Contrato del evento:
            data: {fingerprint_match, scope, files_updated, total_nodes,
                   total_edges, quality_passed}
            metadata: {source_fingerprint}

        Best-effort con log visible ante pérdida (G-W4): el fingerprint del
        build es la certificación de espejo CPG↔código (ADR-043).
        """
        if not self._tracer or not getattr(ctx, "chain_dir", ""):
            return
        signals = result.get("signals", {}) or {}
        metadata = result.get("metadata", {}) or {}
        data = {
            "fingerprint_match": bool(signals.get("fingerprint_match", False)),
            "scope": result.get("scope", "code_index"),
            "files_updated": signals.get("files_updated", 0),
            "total_nodes": signals.get("total_nodes", 0),
            "total_edges": signals.get("total_edges", 0),
            "quality_passed": bool(signals.get("quality_passed", True)),
            "source_fingerprint": metadata.get("source_fingerprint", ""),
        }
        _trace_dispatch(
            self._tracer,
            ctx.chain_dir,
            "cpg_indexed",
            data,
            task_id=getattr(ctx, "task_id", "") or result.get("type", "update_cpg"),
            session_id=getattr(ctx, "session_id", ""),
        )

    def compensate(
        self,
        ctx: ExecutionContext,
        results: list[dict],
        failed_index: int,
    ) -> list[dict]:
        """Compensa efectos ejecutados exitosamente en orden inverso.

        Para cada efecto exitoso antes del fallo:
          1. Busca su compensacion registrada en el SideEffectDef original
          2. Si tiene compensacion, la ejecuta
          3. Si no tiene compensacion, loggea warning y continua

        Args:
            ctx: ExecutionContext.
            results: Lista de resultados de execute().
            failed_index: Indice del efecto que fallo.

        Returns:
            Lista de resultados de compensacion.
        """
        compensation_results: list[dict] = []
        for i in range(failed_index - 1, -1, -1):
            prev = results[i]
            if not prev.get("ok"):
                continue

            effect_type = prev["type"]
            prev_result = prev.get("result", {})

            try:
                # Fuente única (SPEC-014 Fase 0): la compensación es la que
                # declara EL GRAFO (SideEffectDef.compensation). Si el grafo no
                # declara compensation → no compensa (warning y continuar).
                comp_type = prev.get("compensation") or ""
                if not comp_type:
                    # Sin compensacion — warning y continuar
                    logger.warning(
                        "No compensation for '%s' — continuing (Saga non-blocking)",
                        effect_type,
                    )
                    compensation_results.append(
                        {
                            "type": f"compensate_{effect_type}",
                            "ok": True,
                            "result": "no compensation needed (logged warning)",
                            "skipped": True,
                        }
                    )
                    continue

                # Ejecutar compensacion real
                comp_result = self._compensate_single(
                    ctx,
                    comp_type,
                    prev_result,
                )
                compensation_results.append(
                    {
                        "type": f"compensate_{effect_type}",
                        "ok": True,
                        "result": comp_result,
                    }
                )
            except Exception as e:
                logger.error(
                    "Compensation for '%s' failed: %s",
                    effect_type,
                    e,
                )
                compensation_results.append(
                    {
                        "type": f"compensate_{effect_type}",
                        "ok": False,
                        "result": str(e),
                    }
                )

        return compensation_results

    def _execute_single(
        self,
        ctx: ExecutionContext,
        effect: SideEffectDef,
    ) -> dict | str | None:
        """Ejecuta un unico side-effect con resultado real.

        Dispatch (SPEC-014 Fase 1):
          - handler modular registrado en `tasks/side_effects/` y resuelto vía
            ActionCatalog (puerto único) → se ejecuta y normaliza al envelope estándar.
          - name SIN handler → warning + None (fail-loud). Los stubs legacy
            (emit_event, persist_snapshot, run_script, update_cpg, save_ckg,
            update_delta_graph, save_to_ckg) fueron ELIMINADOS: duplicaban
            handlers modulares o no los declaraba ningún nodo.

        Returns:
            dict | str con resultado, o None si el efecto fallo.
            El caller (execute()) interpreta None como fallo.
        """
        etype = effect.type
        params = effect.params

        # ── Resolver handler via ActionCatalog (SPEC-014 Fase 1: puerto único) ──
        handler = None
        if self._action_catalog is not None:
            action = self._action_catalog.resolve(etype)
            if action is not None:
                handler = action.handler
        if handler is None:
            handler = self._handlers.get(etype)

        if handler is not None:
            outcome = _run_side_effect_handler_core(handler, ctx, params)
            if is_successful(outcome):
                value = outcome.value_or({})
                if isinstance(value, dict) and (
                    value.get("status") == "failed" or value.get("ok") is False
                ):
                    logger.warning(
                        "Side-effect '%s' devolvió envelope fallido (%s) — tratado como None",
                        etype,
                        value.get("summary", "") or value.get("error", ""),
                    )
                    return None
                return value
            logger.warning(
                "Side-effect '%s' handler falló (%s: %s) — tratado como None (fail-loud)",
                etype,
                type(outcome.failure()).__name__,
                outcome.failure(),
            )
            return None

        logger.warning("Unknown side-effect type: %s", etype)
        return None

    def _compensate_single(
        self,
        ctx: ExecutionContext,
        compensation_type: str,
        original_result: dict | str | None,
    ) -> dict | str | None:
        """Ejecuta la compensación REAL declarada por el grafo.

        Fuente única (SPEC-014 Fase 0+F1): la compensación se resuelve contra
        ActionCatalog (puerto único) o, si no está disponible, contra
        self._handlers. Nunca un mapa hardcodeado ni dicts fabricados.
        """
        handler = None
        if self._action_catalog is not None:
            action = self._action_catalog.resolve(compensation_type)
            if action is not None:
                handler = action.handler
        if handler is None:
            handler = self._handlers.get(compensation_type)
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
        logger.error(
            "Compensación '%s' falló (%s: %s)",
            compensation_type,
            type(failure).__name__,
            failure,
        )
        return {
            "compensation": compensation_type,
            "ok": False,
            "error": str(failure),
        }

    # ── Helpers ──────────────────────────────────────────────────────

    def _track_result(
        self,
        ctx: ExecutionContext,
        key: str,
        result: dict | str | None,
    ) -> None:
        """Trackea resultado para idempotencia."""
        chain_key = ctx.chain_dir
        if chain_key not in self._idempotency_store:
            self._idempotency_store[chain_key] = {}
        self._idempotency_store[chain_key][key] = {
            "result": result,
            "timestamp": ctx.started_at,
        }

    def _get_tracked(
        self,
        ctx: ExecutionContext,
        key: str,
    ) -> dict | str | None:
        """Obtiene resultado previo si existe para idempotencia."""
        chain_store = self._idempotency_store.get(ctx.chain_dir, {})
        tracked = chain_store.get(key)
        if tracked:
            return tracked.get("result")
        return None

    def clear_idempotency(self, chain_dir: str) -> None:
        """Limpia idempotency store para un chain_dir."""
        self._idempotency_store.pop(chain_dir, None)
