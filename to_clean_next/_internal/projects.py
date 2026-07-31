#! /usr/bin/env python3
"""projects.py — Internal projects module (PROXY).

PROXY MODULE: Re-exports from _walker/workflow_engine/domain/project_registry.py.
Mirror of scripts/projects.py for backward compat.
"""
from __future__ import annotations

from _walker.workflow_engine.domain.project_registry import (
    find_cognitive_root,
    COGNITIVE_DIR, PROJECTS_DIR, DELTA_DIR,
    PROJECTS_REGISTRY, BACKLOG_FILE, PROJECT_FILE, CURRENT_JSON,
    ProjectRegistry,
    ProjectProfile,
)
