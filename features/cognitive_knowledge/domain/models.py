"""
domain/models.py — CKG domain models.

NodeType / EdgeType: tipos de nodos y edges del CKG.
ChangeDetected: tracks code changes detected by the file watcher.
  Written by watcher daemon, read by CLI status and workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(Enum):
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    CONVENTION = "convention"
    PATTERN = "pattern"
    DECISION = "decision"
    COMPONENT = "component"
    LAYER = "layer"
    QUALITY = "quality"


class EdgeType(Enum):
    DEPENDS_ON = "DEPENDS_ON"
    BELONGS_TO = "BELONGS_TO"
    CONTAINS = "CONTAINS"
    REFERENCES = "REFERENCES"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"


@dataclass
class ChangeDetected:
    """Snapshot of code changes detected since last session.

    Written by FileWatcher daemon (features/project/infrastructure/watcher.py).
    Read by CLI status to notify agent of stale CKG/CPG.
    Location: .cognitive/projects/<pid>/data/change_detected.json
    """
    timestamp: str = ""
    files_changed: list[str] = field(default_factory=list)
    ckg_status: str = "up_to_date"     # up_to_date | stale
    cpg_status: str = "up_to_date"     # up_to_date | stale
    total_changes: int = 0
