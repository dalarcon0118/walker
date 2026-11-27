"""infrastructure/state_repository.py — WorkflowState persistence (SQLite SSOT).

SRP: single responsibility = load/save WorkflowState.
SSOT: cognitive.sqlite3 `workflow_state` table is the ONLY engine state store
      (ADR-WALKER-063: sqlite es el único backend runtime; JSON files eliminados).

Features:
  - CAS (compare-and-swap): save checks revision match, increments on write
  - Reconciliation on load: sub_state_index ↔ current_sub_state consistency
  - Per-spec state: spec_id tracked via contextvars; '' = global state
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sqlite3
from typing import Any

from ..domain.entities import WorkflowState
from ..domain.errors import StateConflictError

logger = logging.getLogger(__name__)

# Contextvar for per-spec state (mirrors orchestrator's _current_spec_id)
_current_spec_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "state_repo_spec_id", default=""
)
# Contextvar for per-session state (SPEC-023)
_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "state_repo_session_id", default=""
)

# Table name in the cognitive SQLite database
_TABLE = "workflow_state"


def _resolve_db_path(chain_dir: str, db_path: str | None = None) -> str:
    """Resolve the path to cognitive.sqlite3.

    Production: cognitive_data_dir(chain_dir) → .cognitive/projects/<id>/data/cognitive.sqlite3
    Tests/temp: chain_dir/data/cognitive.sqlite3 (isolated per tmp_path)
    """
    if db_path:
        return db_path

    # Detect test/temp paths
    is_temp = (
        "pytest" in chain_dir
        or "/tmp" in chain_dir
        or "var/folders" in chain_dir
        or "var\\folders" in chain_dir
    )

    if is_temp:
        data_dir = os.path.join(chain_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "cognitive.sqlite3")

    # Production: use cognitive_data_dir
    try:
        from fast_mcp.shared_kernel.paths import cognitive_data_dir
        data_dir = cognitive_data_dir(chain_dir)
        candidate = os.path.join(data_dir, "cognitive.sqlite3")
        os.makedirs(data_dir, exist_ok=True)
        return candidate
    except Exception:
        pass

    # Fallback: chain_dir/data/cognitive.sqlite3
    data_dir = os.path.join(chain_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "cognitive.sqlite3")


class StateRepository:
    """Load/save WorkflowState from SQLite (cognitive.sqlite3).

    CAS: save() checks state.revision matches stored revision.
    If mismatch → StateConflictError (lost update detected).
    On success, increments revision and persists.

    Reconciliation: load() reconciles sub_state_index ↔ current_sub_state
    using the provided graph. State is always consistent after load.

    Per-spec: spec_id tracked via contextvars _current_spec_id.
    '' = global state (backward compat).
    """

    def __init__(self, graph_repository: Any = None, db_path: str | None = None):
        """Args:
            graph_repository: GraphRepository for reconciliation on load.
                              If None, reconciliation is skipped.
            db_path: Path to cognitive.sqlite3. If None, resolved from chain_dir.
        """
        self._graph_repo = graph_repository
        self._db_path_override = db_path

    def _db_path(self, chain_dir: str) -> str:
        return _resolve_db_path(chain_dir, self._db_path_override)

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name='{_TABLE}'"
        )
        exists = cursor.fetchone() is not None

        if exists:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()]
            if "session_id" not in cols:
                # Old schema: drop and recreate (state is ephemeral, safe to lose)
                conn.execute(f"DROP TABLE {_TABLE}")
                conn.commit()
                exists = False

        if not exists:
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
                    spec_id TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    state_json TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (spec_id, session_id)
                )"""
            )
            conn.commit()

    def load(self, chain_dir: str) -> WorkflowState:
        """Load WorkflowState from SQLite.

        1. Read from workflow_state table (spec_id from contextvars)
        2. Parse via WorkflowState.from_dict()
        3. Reconcile index if graph available
        4. Return consistent state

        If no row found, returns empty WorkflowState.
        """
        spec_id = _current_spec_id.get()
        session_id = _current_session_id.get()
        db_path = self._db_path(chain_dir)

        try:
            conn = sqlite3.connect(db_path)
            self._ensure_table(conn)
            row = conn.execute(
                f"SELECT state_json FROM {_TABLE} WHERE spec_id = ? AND session_id = ?",
                (spec_id, session_id),
            ).fetchone()
            conn.close()

            if not row:
                return WorkflowState()

            data = json.loads(row[0])
            state = WorkflowState.from_dict(data)
        except Exception as e:
            logger.error("StateRepository: failed to load from %s: %s", db_path, e)
            return WorkflowState()

        # Reconcile sub_state_index ↔ current_sub_state (GAP-1)
        if state.current_node and self._graph_repo is not None:
            try:
                graph = self._graph_repo.load()
                state, warnings = state.reconcile_index(graph)
                for w in warnings:
                    logger.warning("StateRepository: %s", w)
            except Exception as e:
                logger.error("StateRepository: reconcile failed: %s", e)

        return state

    def save(self, chain_dir: str, state: WorkflowState) -> WorkflowState:
        """Persist WorkflowState to SQLite with CAS (optimistic concurrency).

        1. Read stored revision from SQLite
        2. If state.revision != stored → StateConflictError
        3. Increment revision
        4. Upsert into workflow_state table
        5. Return saved state (with new revision)

        Raises StateConflictError on concurrent modification.
        """
        spec_id = _current_spec_id.get()
        session_id = _current_session_id.get()
        db_path = self._db_path(chain_dir)

        conn = sqlite3.connect(db_path)
        self._ensure_table(conn)

        # Read stored revision
        stored_revision = 0
        row = conn.execute(
            f"SELECT revision FROM {_TABLE} WHERE spec_id = ? AND session_id = ?",
            (spec_id, session_id),
        ).fetchone()
        if row:
            stored_revision = int(row[0] or 0)

        # CAS check
        if state.revision != stored_revision:
            conn.close()
            raise StateConflictError(chain_dir, state.revision, stored_revision)

        # Increment revision
        new_revision = stored_revision + 1

        # Build saveable dict with incremented revision
        state_dict = state.to_dict()
        state_dict["revision"] = new_revision

        # Upsert
        conn.execute(
            f"""INSERT INTO {_TABLE} (spec_id, session_id, state_json, revision, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(spec_id, session_id) DO UPDATE SET
                    state_json=excluded.state_json,
                    revision=excluded.revision,
                    updated_at=excluded.updated_at""",
            (spec_id, session_id, json.dumps(state_dict, ensure_ascii=False), new_revision,
             state_dict.get("updated_at", "")),
        )
        conn.commit()
        conn.close()

        # Return state with new revision
        from dataclasses import replace
        return replace(state, revision=new_revision)
