"""
domain/step_result.py — StepResult: monad del flujo de un sub-estado.

Compone en UN valor tipado todo lo que produce la ejecución de un
sub-estado del workflow:
  - task:       TaskResult de la tarea ejecutada (si hay action)
  - side_effects: envelopes estándar de los side-effects (ver
                  side_effect_contract.py)
  - signals:    agregado de señales semánticas de los envelopes
                (quality_passed, recommend, files_updated, ...)
  - advanced_to: sub-estado destino tras auto-advance (si aplica)
  - node_complete: si el nodo quedó completo

PROPÓSITO: que el PromptManager reciba ESTE valor (tipado, sin JSON
strings ni re-parseo del estado) y pueda inyectarlo en el prompt del
siguiente sub-estado. También permite componer: los signals de un
sub-estado se pueden propagar al siguiente sin re-leer el estado.

Es inmutable y construido con factories, siguiendo el patrón Result.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from shared.common.shared_domain.models import TaskResult


@dataclass
class StepResult:
    """Resultado tipado de la ejecución de un sub-estado."""

    ok: bool
    task: TaskResult | None = None
    side_effects: list[dict] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    advanced_to: str = ""
    node_complete: bool = False
    error: str = ""
    sub_state: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    # ── Factories ────────────────────────────────────────────────

    @classmethod
    def success(
        cls,
        task: TaskResult | None = None,
        side_effects: list[dict] | None = None,
        advanced_to: str = "",
        node_complete: bool = False,
        sub_state: str = "",
        fields: dict[str, Any] | None = None,
    ) -> "StepResult":
        return cls(
            ok=True,
            task=task,
            side_effects=side_effects or [],
            signals=aggregate_signals(side_effects or []),
            advanced_to=advanced_to,
            node_complete=node_complete,
            sub_state=sub_state,
            fields=fields or {},
        )

    @classmethod
    def failure(cls, error: str, task: TaskResult | None = None,
                sub_state: str = "") -> "StepResult":
        return cls(ok=False, error=error, task=task, sub_state=sub_state)

    # ── Conveniencia para el prompt ─────────────────────────────

    def quality(self) -> dict:
        """Señal de calidad del último index (si existe)."""
        q = self.signals.get("quality") or {}
        return q

    def to_prompt_dict(self) -> dict:
        """Dict plano listo para inyectar en placeholders de templates.

        Expone SOLO lo semántico (nunca metadata interna de envelopes):
          step_ok, step_sub_state, step_advanced_to, step_node_complete,
          step_quality_summary, step_quality_passed, step_failed_gates,
          step_recommend, step_task_summary, step_side_effect_summaries.
        """
        q = self.quality()
        failed = q.get("failed_gates", [])
        return {
            "step_ok": self.ok,
            "step_sub_state": self.sub_state,
            "step_advanced_to": self.advanced_to,
            "step_node_complete": self.node_complete,
            "step_error": self.error,
            "step_quality_summary": q.get("summary", ""),
            "step_quality_passed": q.get("quality_passed"),
            "step_failed_gates": ", ".join(failed) if failed else "",
            "step_recommend": q.get("recommend", ""),
            "step_task_summary": (
                str(self.task.data)[:200] if self.task and self.task.data else ""
            ),
            "step_side_effect_summaries": "\n".join(
                s.get("summary", "") for s in self.side_effects if s.get("summary")
            ),
        }

    # ── Persistencia tipada (checkpoint) ─────────────────────────

    def to_checkpoint(self) -> dict:
        """Serializa el StepResult a un checkpoint persistible CON tipos.

        Diferencia con to_prompt_dict(): este preserva el tipo de cada
        campo (bool, list, dict) y agrega un marcador __checkpoint__ para
        reconstrucción validada. El state.json guarda ESTE dict, no un
        dict aplanado a strings.
        """
        return {
            "__checkpoint__": "StepResult:v1",
            "ok": self.ok,
            "sub_state": self.sub_state,
            "advanced_to": self.advanced_to,
            "node_complete": self.node_complete,
            "error": self.error,
            "signals": dict(self.signals),
            "side_effect_summaries": [
                s.get("summary", "") for s in self.side_effects if s.get("summary")
            ],
            "task_summary": (
                str(self.task.data)[:500] if self.task and self.task.data else ""
            ),
        }

    @classmethod
    def from_checkpoint(cls, raw: dict | None) -> "TaskResult":
        """Reconstruye un StepResult desde un checkpoint, validando.

        Returns:
            TaskResult.success(StepResult) si el checkpoint es válido.
            TaskResult.failure(msj) si el checkpoint es corrupto/inválido
            (el workflow sabe que el checkpoint no es confiable).
        """
        from shared.common.shared_domain.models import TaskResult as TR

        if not isinstance(raw, dict):
            return TR.failure("checkpoint no es un dict")
        if raw.get("__checkpoint__") != "StepResult:v1":
            return TR.failure("checkpoint no es un StepResult:v1")

        signals = raw.get("signals", {})
        if not isinstance(signals, dict):
            return TR.failure("checkpoint.signals inválido")

        ok_flag = raw.get("ok")
        if not isinstance(ok_flag, bool):
            return TR.failure("checkpoint.ok inválido (no es bool)")

        return TR.success(cls(
            ok=ok_flag,
            signals=signals,
            advanced_to=str(raw.get("advanced_to", "")),
            node_complete=bool(raw.get("node_complete", False)),
            error=str(raw.get("error", "")),
            sub_state=str(raw.get("sub_state", "")),
        ))


def aggregate_signals(side_effects: list[dict]) -> dict[str, Any]:
    """Agrega las señales semánticas de los envelopes estándar.

    De cada envelope con scope=code_index, toma sus signals (quality_passed,
    recommend, files_updated, ...). El último index gana para quality.

    Returns:
        dict con {quality: {summary, quality_passed, failed_gates, recommend}, ...}
        y las demás señales bajo su clave.
    """
    agg: dict[str, Any] = {}
    quality = {}
    for se in side_effects:
        if not isinstance(se, dict):
            continue
        result = se.get("result", {}) or {}
        if not isinstance(result, dict):
            continue
        signals = result.get("signals", {}) or {}
        if not isinstance(signals, dict):
            continue
        if result.get("scope") == "code_index":
            quality = {
                "summary": result.get("summary", ""),
                "quality_passed": signals.get("quality_passed"),
                "failed_gates": signals.get("failed_gate_names", []),
                "recommend": signals.get("recommend", ""),
                "degraded": result.get("status") == "degraded",
            }
            agg["quality"] = quality
        for k, v in signals.items():
            if k not in ("quality_passed", "quality_degraded", "failed_gates",
                         "failed_gate_names", "recommend"):
                agg.setdefault("signals", {})[k] = v
    return agg
