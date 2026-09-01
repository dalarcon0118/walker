"""infrastructure/state_repository.py — WorkflowState persistence.

SRP: single responsibility = load/save WorkflowState.
SSOT: .workflow-state.json is the ONLY engine state file.

Features:
  - CAS (compare-and-swap): save checks revision match, increments on write
  - Reconciliation on load: sub_state_index ↔ current_sub_state consistency
  - Atomic write: write to .tmp then os.replace() to prevent corruption
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

from ..domain.entities import WorkflowState
from ..domain.errors import StateConflictError

logger = logging.getLogger(__name__)

STATE_FILE = ".workflow-state.json"


class StateRepository:
    """Load/save WorkflowState from .workflow-state.json.

    CAS: save() checks state.revision matches stored revision.
    If mismatch → StateConflictError (lost update detected).
    On success, increments revision and persists.

    Reconciliation: load() reconciles sub_state_index ↔ current_sub_state
    using the provided graph. State is always consistent after load.
    """

    def __init__(self, graph_repository: Any = None):
        """Args:
            graph_repository: GraphRepository for reconciliation on load.
                              If None, reconciliation is skipped.
        """
        self._graph_repo = graph_repository

    def load(self, chain_dir: str) -> WorkflowState:
        """Load WorkflowState from disk.

        1. Read .workflow-state.json
        2. Parse via WorkflowState.from_dict()
        3. Reconcile index if graph available
        4. Return consistent state

        If file missing or corrupt, returns empty WorkflowState.
        """
        path = os.path.join(chain_dir, STATE_FILE)
        if not os.path.exists(path):
            return WorkflowState()

        try:
            with open(path) as f:
                data = json.load(f)
            state = WorkflowState.from_dict(data)
        except Exception as e:
            logger.error("StateRepository: failed to load %s: %s", path, e)
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
        """Persist WorkflowState with CAS (optimistic concurrency).

        1. Read stored revision from disk
        2. If state.revision != stored → StateConflictError
        3. Increment revision
        4. Atomic write (tmp + replace)
        5. Return saved state (with new revision)

        Raises StateConflictError on concurrent modification.
        """
        path = os.path.join(chain_dir, STATE_FILE)

        # Read stored revision
        stored_revision = 0
        if os.path.exists(path):
            try:
                with open(path) as f:
                    stored_revision = int((json.load(f).get("revision") or 0))
            except Exception:
                stored_revision = 0

        # CAS check
        if state.revision != stored_revision:
            raise StateConflictError(chain_dir, state.revision, stored_revision)

        # Increment revision
        new_revision = stored_revision + 1

        # Build saveable dict with incremented revision
        state_dict = state.to_dict()
        state_dict["revision"] = new_revision

        # Atomic write: tmp file + os.replace
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(path) or ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(state_dict, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            # Clean up tmp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Return state with new revision
        from dataclasses import replace
        return replace(state, revision=new_revision)
