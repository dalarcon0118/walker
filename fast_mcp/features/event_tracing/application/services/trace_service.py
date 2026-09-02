"""application/services/trace_service.py — TraceService (application facade).

Thin orchestrator over TraceRepository. Handles:
- load_traces: canonical, deduped, sorted
- push_event: store event + invalidate cache
- build_reflection_context: evidence package for REFINE.DIAGNOSE

DI-injected: TraceRepository is the only dependency.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fast_mcp.features.event_tracing.application.ports.trace_repository import (
    TraceRepository,
)
from fast_mcp.features.event_tracing.domain.entities import TraceEvent

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3


class TraceService:
    """Application facade for event tracing.

    Pure domain logic (reflection context building) lives here.
    IO is delegated to TraceRepository.
    """

    def __init__(self, repo: TraceRepository) -> None:
        self._repo = repo

    def load_traces(self, chain_dir: str) -> list[TraceEvent]:
        """Load canonical, deduped, sorted traces for a sprint."""
        return self._repo.load_traces(chain_dir)

    def push_event(self, chain_dir: str, data: dict) -> dict:
        """Store an event in the trace index."""
        return self._repo.push_event(chain_dir, data)

    def build_reflection_context(
        self,
        chain_dir: str,
        task_id: str = "",
        last_error: str = "",
        last_error_strategy: str = "",
        last_reason: str = "",
        failed_gates: str = "",
    ) -> dict:
        """Build evidence package for REFINE.DIAGNOSE.

        Derives REFINE metadata (source_node, attempt, error_context)
        from TraceGraph events and generic workflow state — the engine
        doesn't inject anything.
        """
        events = [ev.to_dict() for ev in self._repo.load_traces(chain_dir)]

        if task_id:
            task_events = [
                e for e in events if task_id in str(e.get("task_id", ""))
            ]
        else:
            task_events = events

        errors = [e for e in task_events if e.get("event_type") == "ERROR"]
        submits = [e for e in task_events if e.get("event_type") == "SUBMIT"]
        transitions = [
            e for e in task_events if e.get("event_type") == "TRANSITION"
        ]
        auto_adv = [
            e for e in task_events if e.get("event_type") == "AUTO_ADVANCE"
        ]

        source_node = self._derive_source_node(transitions)
        attempt = self._derive_attempt(transitions)
        error_context = self._compose_error_context(
            last_error=last_error,
            last_error_strategy=last_error_strategy,
            last_reason=last_reason,
        )

        past_attempts: list[dict] = []
        for ev in errors:
            past_attempts.append({
                "type": "ERROR",
                "timestamp": ev.get("timestamp", ev.get("started_at", "")),
                "detail": self._fmt_error_detail(ev),
            })
        for ev in submits:
            past_attempts.append({
                "type": "SUBMIT",
                "timestamp": ev.get("timestamp", ev.get("started_at", "")),
                "detail": self._fmt_submit_detail(ev),
            })
        for ev in auto_adv:
            d = ev.get("data") or {}
            past_attempts.append({
                "type": "AUTO_ADVANCE",
                "timestamp": ev.get("timestamp", ev.get("started_at", "")),
                "detail": (
                    f"auto-advance: {d.get('from', '')} -> {d.get('to', '')}"
                    if d else "auto-advance"
                ),
            })
        past_attempts.sort(key=lambda x: x["timestamp"])

        transition_gates = [
            (ev.get("data") or {}).get("gate", "") for ev in transitions
        ]

        return {
            "task_id": task_id,
            "source_node": source_node,
            "attempt": attempt or 1,
            "max_attempts": DEFAULT_MAX_ATTEMPTS,
            "error_context": error_context,
            "failed_gates": failed_gates,
            "past_attempts": past_attempts[-10:],
            "transition_gates": [g for g in transition_gates if g][-5:],
            "hint": self._build_hint(source_node, errors, failed_gates),
        }

    def format_reflection_context(self, package: dict) -> str:
        """Format reflection package as structured text."""
        if not package:
            return "(no reflection context)"

        lines: list[str] = []
        lines.append(f"  task: {package.get('task_id', '')}")
        lines.append(f"  source_node: {package.get('source_node', '')}")
        lines.append(
            f"  attempt: {package.get('attempt')}/{package.get('max_attempts')}"
        )
        if package.get("error_context"):
            lines.append(f"  error_context: {package['error_context'][:200]}")
        if package.get("failed_gates"):
            lines.append(f"  failed_gates: {package['failed_gates']}")

        past = package.get("past_attempts") or []
        if past:
            lines.append("  past_attempts:")
            for p in past:
                ts = (p.get("timestamp") or "")[:19]
                lines.append(f"    [{ts}] {p.get('type')}: {p.get('detail', '')[:140]}")

        gates = package.get("transition_gates") or []
        if gates:
            lines.append(f"  gates usadas: {', '.join(gates)}")

        if package.get("hint"):
            lines.append(f"  hint: {package['hint']}")

        return "\n".join(lines)

    # ── Private helpers (pure) ──

    @staticmethod
    def _derive_source_node(transitions: list[dict]) -> str:
        """Derive the node that triggered the refine from TraceGraph."""
        for ev in reversed(transitions):
            d = ev.get("data") or {}
            if d.get("to") == "REFINE" and d.get("from"):
                return d["from"]
        return ""

    @staticmethod
    def _derive_attempt(transitions: list[dict]) -> int:
        """Count how many times REFINE was entered."""
        return sum(
            1 for ev in transitions
            if (ev.get("data") or {}).get("to") == "REFINE"
        )

    @staticmethod
    def _compose_error_context(
        last_error: str = "",
        last_error_strategy: str = "",
        last_reason: str = "",
    ) -> str:
        """Compose error_context from generic workflow keys."""
        parts = []
        if last_error:
            parts.append(f"error={last_error}")
        if last_error_strategy and last_error_strategy != "none":
            parts.append(f"strategy={last_error_strategy}")
        if last_reason:
            parts.append(f"reason={last_reason}")
        return " | ".join(parts)

    @staticmethod
    def _fmt_error_detail(ev: dict) -> str:
        """Extract readable detail from an ERROR event."""
        d = ev.get("data") or {}
        err_type = d.get("error_type", ev.get("event_type", "ERROR"))
        msg = (d.get("message", "") or "").strip()
        return f"{err_type}: {msg}" if msg else err_type

    @staticmethod
    def _fmt_submit_detail(ev: dict) -> str:
        """Extract readable detail from a SUBMIT event."""
        d = ev.get("data") or {}
        decision = d.get("decision", "")
        evidence = (d.get("evidence", "") or "").strip()
        if decision:
            return f"decision={decision}" + (f" — {evidence[:80]}" if evidence else "")
        return evidence[:80] if evidence else "(submit sin detalles)"

    @staticmethod
    def _build_hint(
        source_node: str, errors: list[dict], failed_gates: str
    ) -> str:
        """Deterministic hint (no LLM) to orient the diagnosis."""
        hints: list[str] = []
        if errors:
            last = errors[-1]
            d = last.get("data") or {}
            err_type = d.get("error_type", last.get("event_type", "ERROR"))
            msg = (d.get("message", "") or "").strip()
            detail = f"{err_type}: {msg}" if msg else err_type
            hints.append(f"último error registrado: {detail}")
        if failed_gates:
            hints.append(f"gates fallidos del paso anterior: {failed_gates}")
        node_hints = {
            "IMPLEMENT": "vienes de IMPLEMENT → sospecha de calidad/refactor: target sugerido 'implement'",
            "ANALYZE": "vienes de ANALYZE → análisis incompleto/incorrecto: target sugerido 'analyze'",
            "DESIGN": "vienes de DESIGN → diseño insuficiente: target sugerido 'analyze' o 'implement'",
            "VERIFY": "vienes de VERIFY → tests/QA fallaron: target sugerido 'verify' o 'implement'",
        }
        if source_node in node_hints:
            hints.append(node_hints[source_node])
        return " | ".join(hints) or "sin contexto suficiente; investiga en la capa TRACE"
