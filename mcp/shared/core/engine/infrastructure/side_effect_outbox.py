"""infrastructure/side_effect_outbox.py — Outbox de side-effects (ADR-WALKER-067 F4/H, SPEC-009).

Outbox pattern para exactly-once execution de side-effects:

  - La INTENCION de cada efecto se persiste ANTES de ejecutarlo (status=pending).
  - El resultado se marca completed / failed / compensated.
  - replay_pending() permite reintentar tras un crash; clear_completed() limpia
    entradas viejas; stats() expone el estado del outbox.

Implementaciones:
  - InMemorySideEffectOutbox: por defecto en el engine (semantica de outbox
    activa sin persistencia). Implementa el puerto ISideEffectOutbox del saga.
  - SideEffectOutbox(chain_dir): durable (sqlite por chain_dir/proyecto),
    API completa SPEC-009. El host lo provee para durabilidad real.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..domain.ports import ISideEffectOutbox


class OutboxEntry:
    """Entrada del outbox (intencion de ejecutar un side-effect)."""

    def __init__(
        self,
        entry_id: str,
        effect_type: str,
        params: dict | None = None,
        idempotency_key: str | None = None,
        status: str = "pending",
        result: Any = None,
        error: str = "",
        compensation: Any = None,
        created_at: float | None = None,
        outbox: Any = None,
    ):
        self.entry_id = entry_id
        self.effect_type = effect_type
        self.params = params or {}
        self.idempotency_key = idempotency_key
        self.status = status
        self.result = result
        self.error = error
        self.compensation = compensation
        self.created_at = created_at or time.time()
        self._outbox = outbox

    def mark_completed(self, result: Any) -> None:
        if self._outbox:
            self._outbox.mark_completed(self, result)

    def mark_failed(self, error: str) -> None:
        if self._outbox:
            self._outbox.mark_failed(self, error)

    def mark_executing(self) -> None:
        if self._outbox:
            self._outbox.mark_executing(self)

    def mark_compensated(self, compensation: Any) -> None:
        if self._outbox:
            self._outbox.mark_compensated(self, compensation)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, OutboxEntry):
            return NotImplemented
        return (
            self.entry_id == other.entry_id
            and self.status == other.status
            and self.effect_type == other.effect_type
        )

    def __repr__(self) -> str:
        return (
            f"OutboxEntry(id={self.entry_id}, type={self.effect_type}, "
            f"status={self.status})"
        )

    def to_row(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "effect_type": self.effect_type,
            "params": self.params,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "compensation": self.compensation,
            "created_at": self.created_at,
        }


class InMemorySideEffectOutbox(ISideEffectOutbox):
    """Outbox en memoria (por defecto). Thread-safe. Implementa ISideEffectOutbox."""

    def __init__(self):
        self._entries: list[OutboxEntry] = []
        self._lock = threading.RLock()

    def enqueue(self, effect: Any, idempotency_key: str | None = None) -> OutboxEntry:
        with self._lock:
            if idempotency_key:
                for e in self._entries:
                    if e.idempotency_key == idempotency_key and e.status == "completed":
                        return e
            entry = OutboxEntry(
                entry_id=uuid.uuid4().hex[:12],
                effect_type=effect.type,
                params=dict(getattr(effect, "params", {}) or {}),
                idempotency_key=idempotency_key,
                outbox=self,
            )
            self._entries.append(entry)
            return entry

    def _set_status(self, entry: OutboxEntry, status: str, **extra) -> None:
        with self._lock:
            entry.status = status
            for k, v in extra.items():
                setattr(entry, k, v)

    def mark_executing(self, entry: OutboxEntry) -> None:
        self._set_status(entry, "executing")

    def mark_completed(self, entry: OutboxEntry, result: Any) -> None:
        self._set_status(entry, "completed", result=result)

    def mark_failed(self, entry: OutboxEntry, error: str) -> None:
        self._set_status(entry, "failed", error=str(error))

    def mark_compensated(self, entry: OutboxEntry, compensation: Any) -> None:
        self._set_status(entry, "compensated", compensation=compensation)

    def all(self) -> list:
        with self._lock:
            return list(self._entries)

    def pending(self) -> list:
        return [e for e in self.all() if e.status == "pending"]

    def completed(self) -> list:
        return [e for e in self.all() if e.status == "completed"]

    def failed(self) -> list:
        return [e for e in self.all() if e.status == "failed"]

    def replay_pending(self) -> list:
        return list(self.pending())

    def clear_completed(self, older_than_hours: float = 0) -> int:
        with self._lock:
            cutoff = time.time() - older_than_hours * 3600
            kept = [
                e
                for e in self._entries
                if not (e.status == "completed" and e.created_at <= cutoff)
            ]
            removed = len(self._entries) - len(kept)
            self._entries = kept
            return removed

    def stats(self) -> dict:
        all_ = self.all()
        return {
            "total": len(all_),
            "pending": len(self.pending()),
            "executing": len([e for e in all_ if e.status == "executing"]),
            "completed": len(self.completed()),
            "failed": len(self.failed()),
            "compensated": len([e for e in all_ if e.status == "compensated"]),
        }


class SideEffectOutbox(ISideEffectOutbox):
    """Outbox durable sobre SQLite (SPEC-009), por chain_dir.

    El host elige el path (p.ej. {chain_dir}/side_effect_outbox.sqlite3).
    Implementa ISideEffectOutbox (contrato del saga) + API completa SPEC-009
    (pending/completed/failed/replay/clear/stats).
    """

    def __init__(self, chain_dir: str, db_path: str | None = None):
        self._chain_dir = chain_dir
        self._db_path = db_path or f"{chain_dir}/side_effect_outbox.sqlite3"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS side_effect_outbox (
                entry_id TEXT PRIMARY KEY,
                effect_type TEXT NOT NULL,
                params_json TEXT,
                idempotency_key TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT,
                error TEXT,
                compensation_json TEXT,
                created_at REAL
            )
            """
        )
        self._conn.commit()

    def _row_to_entry(self, r: tuple) -> OutboxEntry:
        return OutboxEntry(
            entry_id=r[0],
            effect_type=r[1],
            params=json.loads(r[2]) if r[2] else {},
            idempotency_key=r[3],
            status=r[4],
            result=json.loads(r[5]) if r[5] else None,
            error=r[6] or "",
            compensation=json.loads(r[7]) if r[7] else None,
            created_at=r[8],
            outbox=self,
        )

    def enqueue(self, effect: Any, idempotency_key: str | None = None) -> OutboxEntry:
        with self._lock:
            if idempotency_key:
                row = self._conn.execute(
                    "SELECT * FROM side_effect_outbox WHERE idempotency_key=? AND status='completed'",
                    (idempotency_key,),
                ).fetchone()
                if row:
                    return self._row_to_entry(row)
            entry = OutboxEntry(
                entry_id=uuid.uuid4().hex[:12],
                effect_type=effect.type,
                params=dict(getattr(effect, "params", {}) or {}),
                idempotency_key=idempotency_key,
                created_at=time.time(),
                outbox=self,
            )
            self._conn.execute(
                "INSERT INTO side_effect_outbox "
                "(entry_id, effect_type, params_json, idempotency_key, status, created_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?)",
                (
                    entry.entry_id,
                    entry.effect_type,
                    json.dumps(entry.params, ensure_ascii=False),
                    entry.idempotency_key,
                    entry.created_at,
                ),
            )
            self._conn.commit()
            return entry

    def _update(self, entry_id: str, status: str, **cols) -> None:
        with self._lock:
            sets = ["status=?"]
            vals: list = [status]
            for col, val in cols.items():
                sets.append(f"{col}=?")
                vals.append(val)
            vals.append(entry_id)
            self._conn.execute(
                f"UPDATE side_effect_outbox SET {', '.join(sets)} WHERE entry_id=?",
                vals,
            )
            self._conn.commit()

    def mark_executing(self, entry: OutboxEntry) -> None:
        entry.status = "executing"
        self._update(entry.entry_id, "executing")

    def mark_completed(self, entry: OutboxEntry, result: Any) -> None:
        entry.status = "completed"
        entry.result = result
        self._update(
            entry.entry_id,
            "completed",
            result_json=json.dumps(result, default=str, ensure_ascii=False),
        )

    def mark_failed(self, entry: OutboxEntry, error: str) -> None:
        entry.status = "failed"
        entry.error = str(error)
        self._update(entry.entry_id, "failed", error=str(error))

    def mark_compensated(self, entry: OutboxEntry, compensation: Any) -> None:
        entry.status = "compensated"
        entry.compensation = compensation
        self._update(
            entry.entry_id,
            "compensated",
            compensation_json=json.dumps(compensation, default=str, ensure_ascii=False),
        )

    def all(self) -> list:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM side_effect_outbox").fetchall()
            return [self._row_to_entry(r) for r in rows]

    def pending(self) -> list:
        return [e for e in self.all() if e.status == "pending"]

    def completed(self) -> list:
        return [e for e in self.all() if e.status == "completed"]

    def failed(self) -> list:
        return [e for e in self.all() if e.status == "failed"]

    def replay_pending(self) -> list:
        return list(self.pending())

    def clear_completed(self, older_than_hours: float = 0) -> int:
        with self._lock:
            cutoff = time.time() - older_than_hours * 3600
            cur = self._conn.execute(
                "DELETE FROM side_effect_outbox WHERE status='completed' AND created_at<=?",
                (cutoff,),
            )
            self._conn.commit()
            return cur.rowcount

    def stats(self) -> dict:
        all_ = self.all()
        return {
            "total": len(all_),
            "pending": len(self.pending()),
            "executing": len([e for e in all_ if e.status == "executing"]),
            "completed": len(self.completed()),
            "failed": len(self.failed()),
            "compensated": len([e for e in all_ if e.status == "compensated"]),
        }

    def close(self) -> None:
        self._conn.close()