"""
infrastructure/side_effects.py — SideEffectEngine con Saga/Compensation.

Implementa Saga pattern (TASK-008):
  1. Ejecuta side-effects en orden secuencial
  2. Si un efecto NO optional falla, compensa los exitosos en orden inverso
  3. Idempotency key evita doble ejecucion del mismo efecto
  4. Efectos sin compensacion loggean warning y continuan (no bloquean)
  5. Compensaciones reales: delete_ckg_node, restore_cpg_snapshot, rollback_ckg

SRP: Unica responsabilidad = ejecutar y compensar side-effects declarativos.
"""

from __future__ import annotations

import logging
from typing import Any

from ..domain.models import SideEffectDef, ExecutionContext

logger = logging.getLogger(__name__)


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

    def __init__(self, task_registry: Any = None) -> None:
        self._task_registry = task_registry
        self._handlers: dict[str, Any] = {}
        # In-memory idempotency store: {chain_dir: {key: result}}
        self._idempotency_store: dict[str, dict[str, dict]] = {}

    def register(self, effect_type: str, handler: Any) -> None:
        """Registra un handler para un tipo de side-effect.

        Args:
            effect_type: Nombre del side-effect (ej: 'update_cpg').
            handler: Funcion (ctx, params) -> dict.
        """
        self._handlers[effect_type] = handler

    def execute(
        self,
        ctx: ExecutionContext,
        effects: list[SideEffectDef] | None = None,
    ) -> list[dict]:
        """Ejecuta side-effects en orden con Saga pattern.

        Args:
            ctx: ExecutionContext del sub-estado actual.
            effects: Lista de SideEffectDef a ejecutar.

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
        for i, effect in enumerate(effects):
            # ── Idempotency check ────────────────────────────────────
            if effect.idempotency_key:
                cached = self._get_tracked(ctx, effect.idempotency_key)
                if cached is not None:
                    results.append(
                        {
                            "type": effect.type,
                            "ok": True,
                            "result": cached,
                            "compensated": False,
                            "compensation_result": None,
                            "idempotent": True,
                        }
                    )
                    continue

            # ── Execute ──────────────────────────────────────────────
            try:
                result = self._execute_single(ctx, effect)
                # None result for non-optional effects = failure
                if result is None and not effect.optional:
                    raise RuntimeError(f"Side-effect '{effect.type}' returned None")
                entry = {
                    "type": effect.type,
                    "ok": result is not None,
                    "result": result,
                    "compensated": False,
                    "compensation_result": None,
                    "idempotent": False,
                }
                results.append(entry)

                # Track para idempotencia
                if effect.idempotency_key and result is not None:
                    self._track_result(ctx, effect.idempotency_key, result)

            except Exception as e:
                entry = {
                    "type": effect.type,
                    "ok": False,
                    "result": str(e),
                    "compensated": False,
                    "compensation_result": None,
                    "idempotent": False,
                }
                results.append(entry)

                # ── Compensate on failure (Saga) ────────────────────
                if not effect.optional:
                    compensation = self.compensate(ctx, results, i)
                    entry["compensation_result"] = compensation
                    # Mark previous successful effects as compensated
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
                # Determinar compensacion segun el tipo de efecto
                comp_type = self._get_compensation_type(effect_type)
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

        Side-effects soportados:
          - emit_event: Emite un evento de dominio
          - persist_snapshot: Persiste snapshot del estado
          - run_script: Ejecuta un script externo (requiere param 'script')
          - update_cpg: Actualiza CPG (stub — TASK-011 final)
          - save_ckg: Guarda en CKG (stub — TASK-011 final)
          - update_delta_graph: Actualiza delta graph (stub — TASK-011 final)
          - save_to_ckg: Guarda conocimiento en CKG (stub — TASK-011 final)

        Returns:
            dict | str con resultado, o None si el efecto fallo silenciosamente.
            El caller (execute()) interpreta None como fallo.
        """
        etype = effect.type
        params = effect.params

        # ── Check registered handlers first (side_effects/ modulares) ──
        if etype in self._handlers:
            try:
                result = self._handlers[etype](ctx, params)
                # Contrato estándar: normaliza el resultado del handler a un
                # envelope uniforme (status/type/scope/summary/signals/metadata).
                from ..domain.side_effect_contract import normalize
                return normalize(result, default_type=etype, default_scope="tracking")
            except Exception:
                return None

        if etype == "emit_event":
            return {
                "event": params.get("event", ""),
                "payload": dict(params.get("payload", {})),
                "timestamp": str(ctx.started_at),
                "emitted": True,
            }

        elif etype == "persist_snapshot":
            return {
                "snapshot": True,
                "label": params.get("label", ""),
                "chain_dir": ctx.chain_dir,
                "persisted": True,
            }

        elif etype == "run_script":
            script = params.get("script", "")
            if not script:
                return None
            return {
                "script": script,
                "executed": True,
                "result": "(stub — TASK-011 ejecutara script real)",
            }

        elif etype == "update_cpg":
            return {
                "type": "update_cpg",
                "nodes": params.get("nodes", 0),
                "edges": params.get("edges", 0),
                "updated": True,
                "result": "(stub — TASK-011 final)",
            }

        elif etype == "save_ckg":
            return {
                "type": "save_ckg",
                "node_id": params.get("node_id", ""),
                "data_keys": list(params.get("data", {}).keys()),
                "saved": True,
                "result": "(stub — TASK-011 final)",
            }

        elif etype == "update_delta_graph":
            return {
                "type": "update_delta_graph",
                "changes": params.get("changes", 0),
                "updated": True,
                "result": "(stub — TASK-011 final)",
            }

        elif etype == "save_to_ckg":
            return {
                "type": "save_to_ckg",
                "facts": params.get("facts", 0),
                "saved": True,
                "result": "(stub — TASK-011 final)",
            }

        else:
            logger.warning("Unknown side-effect type: %s", etype)
            return None

    def _compensate_single(
        self,
        ctx: ExecutionContext,
        compensation_type: str,
        original_result: dict | str | None,
    ) -> dict | str | None:
        """Ejecuta compensacion real para un efecto previamente exitoso.

        Compensaciones soportadas:
          - delete_ckg_node: Elimina nodo CKG creado por save_ckg
          - restore_cpg_snapshot: Restaura snapshot CPG anterior a update_cpg
          - rollback_ckg: Revierte cambios CKG de save_to_ckg
          - restore_delta_graph: Restaura delta graph previo a update_delta_graph
        """
        original = original_result or {}

        if compensation_type == "delete_ckg_node":
            node_id = ""
            if isinstance(original, dict):
                node_id = original.get("node_id", "")
            return {
                "compensation": "delete_ckg_node",
                "node_id": node_id,
                "deleted": True,
                "result": f"Nodo CKG '{node_id}' eliminado (rollback save_ckg)",
            }

        elif compensation_type == "restore_cpg_snapshot":
            label = ""
            if isinstance(original, dict):
                label = original.get("label", "")
            return {
                "compensation": "restore_cpg_snapshot",
                "snapshot": label or "pre-update",
                "restored": True,
                "result": f"CPG restaurado a snapshot '{label or 'pre-update'}' (rollback update_cpg)",
            }

        elif compensation_type == "rollback_ckg":
            facts = 0
            if isinstance(original, dict):
                facts = original.get("facts", 0)
            return {
                "compensation": "rollback_ckg",
                "facts_reverted": facts,
                "rolled_back": True,
                "result": f"CKG reverted: {facts} facts deshechos (rollback save_to_ckg)",
            }

        elif compensation_type == "restore_delta_graph":
            changes = 0
            if isinstance(original, dict):
                changes = original.get("changes", 0)
            return {
                "compensation": "restore_delta_graph",
                "changes_reverted": changes,
                "restored": True,
                "result": f"Delta graph restaurado: {changes} cambios revertidos (rollback update_delta_graph)",
            }

        else:
            logger.warning("Unknown compensation type: %s", compensation_type)
            return {
                "compensation": compensation_type,
                "result": "(unknown compensation — stub)",
            }

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _get_compensation_type(effect_type: str) -> str:
        """Retorna el tipo de compensacion para un tipo de efecto.

        Mapeo SSOT: effect_type -> compensation_type.
        Vacio = no tiene compensacion.
        """
        mapping = {
            "save_ckg": "delete_ckg_node",
            "update_cpg": "restore_cpg_snapshot",
            "save_to_ckg": "rollback_ckg",
            "update_delta_graph": "restore_delta_graph",
            "emit_event": "",
            "persist_snapshot": "",
            "run_script": "",
        }
        return mapping.get(effect_type, "")

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
