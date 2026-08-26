"""
domain/side_effect_contract.py — Contrato estándar de respuesta de side-effects.

PROPÓSITO: que el workflow NO conozca la arquitectura interna de cada
side-effect. Todo side-effect retorna un ENVELOPE estándar con campos
semánticos que el workflow puede consumir:

    {
      "status":   "ok" | "failed" | "degraded",
      "type":     "update_cpg",            # tipo de efecto que corrió
      "scope":    "code_index",            # categoría semántica
      "summary":  "CPG indexado (705 files); 2 gates fallan",   # 1 línea humana
      "signals":  {                        # datos que el workflow usa para decidir
                   ...                      #   (quality_passed, failed_gates, recommend, ...)
                 },
      "metadata": { ... }                  # datos internos del efecto (opcionales, NO semánticos)
    }

REGLAS:
  - El workflow SÓLO lee: status, type, scope, summary, signals.
  - metadata es para debugging/auditoría; el workflow no debe depender de él.
  - Un side-effect "fallido" (status=failed) debe ser detectable SIN conocer
    su implementación: basta con `status`.
"""

from __future__ import annotations
from typing import Any

# Scopes semánticos estándar (categorías que el workflow entiende)
SCOPE_CODE_INDEX = "code_index"       # indexación de código (CPG)
SCOPE_KNOWLEDGE = "knowledge"         # conocimiento (CKG)
SCOPE_DECISION = "decision"           # decisiones capturadas
SCOPE_EVENT = "event"                 # eventos de dominio
SCOPE_SNAPSHOT = "snapshot"           # snapshots de estado
SCOPE_SCRIPT = "script"               # scripts externos
SCOPE_TRACKING = "tracking"           # tracking de estado

_VALID_SCOPES = {
    SCOPE_CODE_INDEX, SCOPE_KNOWLEDGE, SCOPE_DECISION, SCOPE_EVENT,
    SCOPE_SNAPSHOT, SCOPE_SCRIPT, SCOPE_TRACKING,
}


def standard_envelope(
    status: str,
    type: str,
    scope: str,
    summary: str = "",
    signals: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construye un envelope estándar de side-effect.

    Args:
        status: "ok" | "failed" | "degraded".
        type: Tipo de efecto (ej: "update_cpg").
        scope: Categoría semántica (ver SCOPE_*).
        summary: Resumen en 1 línea (para el agente).
        signals: Datos que el workflow usa para decidir transiciones.
        metadata: Datos internos del efecto (no semánticos).

    Returns:
        Envelope estándar. Si scope no es válido, se degrada a
        SCOPE_TRACKING para no romper el contrato.
    """
    if scope not in _VALID_SCOPES:
        scope = SCOPE_TRACKING
    if status not in ("ok", "failed", "degraded"):
        status = "failed"
    return {
        "status": status,
        "type": type,
        "scope": scope,
        "summary": summary,
        "signals": signals or {},
        "metadata": metadata or {},
    }


def ok(type: str, scope: str, summary: str = "",
       signals: dict[str, Any] | None = None,
       metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Envelope de éxito."""
    return standard_envelope("ok", type, scope, summary, signals, metadata)


def failed(type: str, scope: str, summary: str = "",
           signals: dict[str, Any] | None = None,
           metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Envelope de fallo."""
    return standard_envelope("failed", type, scope, summary, signals, metadata)


def degraded(type: str, scope: str, summary: str = "",
             signals: dict[str, Any] | None = None,
             metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Envelope degradado (ejecutó, pero con limitaciones — ej. métricas CPG parciales)."""
    return standard_envelope("degraded", type, scope, summary, signals, metadata)


def is_standard(envelope: dict[str, Any]) -> bool:
    """Valida si un dict es un envelope estándar."""
    return all(k in envelope for k in ("status", "type", "scope", "summary", "signals", "metadata"))


def normalize(result: Any, default_type: str, default_scope: str) -> dict[str, Any]:
    """Normaliza cualquier resultado de handler a un envelope estándar.

    Si el handler YA retornó un envelope estándar, se devuelve tal cual.
    Si retornó un dict arbitrario, se envuelve en metadata con status=ok
    (mejora del contrato: los handlers legacy siguen funcionando).
    Si retornó None/str, se envuelve de forma segura.
    """
    if isinstance(result, dict) and is_standard(result):
        return result
    if isinstance(result, dict):
        # SPEC-014 F2: un dict legacy con ok=False es un FALLO — no se envuelve
        # como éxito (asimetría silenciosa: el workflow lo registraba ok=True).
        # Mismo criterio que as_side_effect (status = failed si ok is False).
        if result.get("ok") is False:
            return failed(
                type=default_type, scope=default_scope,
                summary=f"{default_type} falló: {str(result.get('error', ''))[:200]}",
                metadata=result,
            )
        return ok(
            type=default_type, scope=default_scope,
            summary=f"{default_type} completado",
            metadata=result,
        )
    if result is None:
        return failed(
            type=default_type, scope=default_scope,
            summary=f"{default_type} devolvió None (fallo)",
        )
    return ok(
        type=default_type, scope=default_scope,
        summary=str(result)[:200], metadata={"raw": str(result)[:2000]},
    )


def as_side_effect(scope: str):
    """Decorador que envuelve un handler legacy al envelope estándar.

    Permite migrar un side-effect existente SIN tocar su lógica interna:
    el handler retorna su dict legacy, y este decorador lo envuelve en un
    envelope con el scope semántico correcto, moviendo el dict legacy a
    metadata y los campos "signals" explícitos a signals.

    Uso:
        @as_side_effect(scope=SCOPE_KNOWLEDGE)
        def save_ckg(ctx, params):
            ...  # lógica legacy, retorna {"ok": True, "nodes_saved": 5}

    Si el handler ya retorna un envelope estándar, se devuelve tal cual.
    """
    import functools

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(ctx, params):
            result = fn(ctx, params)
            if isinstance(result, dict) and is_standard(result):
                return result
            if isinstance(result, dict):
                status = "failed" if result.get("ok") is False else "ok"
                signals = {}
                for key in ("captured", "saved", "indexed_tasks", "reindexed_tasks",
                            "task_id", "status", "detected", "previous_status",
                            "current_task_index", "nodes_saved", "nodes_created",
                            "decision", "valid", "spec_count", "archived",
                            "deleted", "restored", "imported", "created"):
                    if key in result and result[key] is not None:
                        signals[key] = result[key]
                metadata = {k: v for k, v in result.items() if k not in signals}
                env = standard_envelope(
                    status=status,
                    type=fn.__name__,
                    scope=scope,
                    summary=str(result.get("error", f"{fn.__name__} completado"))[:200],
                    signals=signals,
                    metadata=metadata,
                )
                # Compatibilidad legacy: los campos semánticos del resultado se
                # exponen también en el top-level del envelope, para que los
                # consumidores que leen result['captured']/result['status']/etc.
                # sigan funcionando sin conocer el contrato.
                for key, val in signals.items():
                    env[key] = val
                env["ok"] = status != "failed"
                if "result" in result:
                    env["result"] = result["result"]
                return env
            return normalize(result, fn.__name__, scope)
        return wrapper
    return decorator
