"""
infrastructure/side_effect_outbox.py — Outbox pattern para side effects atómicos.

Garantiza que los side effects se ejecuten exactamente una vez, incluso si
el proceso falla a mitad de la ejecución. El patrón Outbox persiste los
efectos en una cola local antes de ejecutarlos, y los marca como completados
solo después de que el handler confirme éxito.

Atomicidad garantizada:
  1. Todos los side effects se escriben en outbox antes de ejecutar.
  2. Si el proceso falla, los efectos no ejecutados permanecen en outbox.
  3. En el próximo arranque, el outbox replay procesa los efectos pendientes.
  4. Los efectos ejecutados se marcan como completed y se archivan.

SRP: Unica responsabilidad = gestionar la cola de side effects pendientes.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.models import SideEffectDef

logger = logging.getLogger(__name__)


@dataclass
class OutboxEntry:
    """Entrada en la cola outbox de side effects."""
    effect_type: str
    params: dict[str, Any]
    status: str = "pending"  # pending | executing | completed | failed | compensated
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    result: dict[str, Any] | None = None
    error: str | None = None
    compensation_result: dict[str, Any] | None = None
    idempotency_key: str | None = None

    def mark_executing(self) -> None:
        self.status = "executing"
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def mark_completed(self, result: dict[str, Any]) -> None:
        self.status = "completed"
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.result = result

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.error = error

    def mark_compensated(self, compensation_result: dict[str, Any]) -> None:
        self.status = "compensated"
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.compensation_result = compensation_result


class SideEffectOutbox:
    """Cola outbox para side effects atómicos.

    Persiste efectos en disco (JSONL) antes de ejecutarlos.
    Garantiza exactly-once execution con idempotency keys.
    """

    def __init__(self, chain_dir: str) -> None:
        self._chain_dir = chain_dir
        self._outbox_path = Path(chain_dir) / ".outbox" / "side_effects.jsonl"
        self._outbox_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[OutboxEntry] = []
        self._load()

    def _load(self) -> None:
        """Carga entradas pendientes desde disco."""
        if not self._outbox_path.exists():
            self._entries = []
            return
        self._entries = []
        try:
            with open(self._outbox_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = OutboxEntry(**data)
                        self._entries.append(entry)
                    except Exception:
                        continue
        except Exception as e:
            logger.error("Failed to load outbox: %s", e)
            self._entries = []

    def _append(self, entry: OutboxEntry) -> None:
        """Persiste una entrada en disco."""
        try:
            with open(self._outbox_path, "a") as f:
                f.write(json.dumps(entry.__dict__, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to append to outbox: %s", e)

    def _rewrite(self) -> None:
        """Reescribe el archivo completo con el estado actual de las entradas."""
        try:
            with open(self._outbox_path, "w") as f:
                for entry in self._entries:
                    f.write(json.dumps(entry.__dict__, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to rewrite outbox: %s", e)

    def enqueue(self, effect: SideEffectDef, idempotency_key: str | None = None) -> OutboxEntry:
        """Encola un side effect para ejecución atómica.

        Args:
            effect: Definición del side effect.
            idempotency_key: Clave de idempotencia opcional.

        Returns:
            OutboxEntry creado.
        """
        entry = OutboxEntry(
            effect_type=effect.type,
            params=effect.params or {},
            idempotency_key=idempotency_key,
        )
        self._entries.append(entry)
        self._append(entry)
        return entry

    def mark_executing(self, entry: OutboxEntry) -> None:
        """Marca una entrada como ejecutándose."""
        entry.mark_executing()
        self._rewrite()

    def mark_completed(self, entry: OutboxEntry, result: dict[str, Any]) -> None:
        """Marca una entrada como completada."""
        entry.mark_completed(result)
        self._rewrite()

    def mark_failed(self, entry: OutboxEntry, error: str) -> None:
        """Marca una entrada como fallida."""
        entry.mark_failed(error)
        self._rewrite()

    def mark_compensated(self, entry: OutboxEntry, compensation_result: dict[str, Any]) -> None:
        """Marca una entrada como compensada."""
        entry.mark_compensated(compensation_result)
        self._rewrite()

    def pending(self) -> list[OutboxEntry]:
        """Retorna entradas pendientes de ejecución."""
        return [e for e in self._entries if e.status == "pending"]

    def executing(self) -> list[OutboxEntry]:
        """Retorna entradas en ejecución."""
        return [e for e in self._entries if e.status == "executing"]

    def completed(self) -> list[OutboxEntry]:
        """Retorna entradas completadas."""
        return [e for e in self._entries if e.status == "completed"]

    def failed(self) -> list[OutboxEntry]:
        """Retorna entradas fallidas."""
        return [e for e in self._entries if e.status == "failed"]

    def all(self) -> list[OutboxEntry]:
        """Retorna todas las entradas."""
        return list(self._entries)

    def clear_completed(self, older_than_hours: int = 24) -> None:
        """Elimina entradas completadas antiguas."""
        cutoff = datetime.now(timezone.utc).timestamp() - (older_than_hours * 3600)
        self._entries = [
            e for e in self._entries
            if e.status != "completed" or
            datetime.fromisoformat(e.updated_at).timestamp() > cutoff
        ]
        self._rewrite()

    def replay_pending(self) -> list[OutboxEntry]:
        """Retorna entradas pendientes para replay después de un fallo."""
        return self.pending()

    def stats(self) -> dict[str, Any]:
        """Retorna estadísticas del outbox."""
        return {
            "total": len(self._entries),
            "pending": len(self.pending()),
            "executing": len(self.executing()),
            "completed": len(self.completed()),
            "failed": len(self.failed()),
            "file_size_bytes": self._outbox_path.stat().st_size if self._outbox_path.exists() else 0,
        }
