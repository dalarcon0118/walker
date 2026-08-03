#!/usr/bin/env python3
"""Shim: ceremonies.py — reexporta desde features (ADR-WALKER-010).

Implementación real migrada a features/spring/application/ceremonies.py.
Conservado para compatibilidad con cualquier CLI legacy que despache
spring.py backlog / spring.py planning.
"""
from __future__ import annotations

import sys
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from features.spring.application.ceremonies import (  # noqa: E402,F401
    eprint,
    load_json,
    cmd_backlog,
    cmd_planning,
    backlog_init,
    backlog_add,
    backlog_list,
    backlog_prioritize,
    backlog_refine,
    backlog_select,
    planning_propose,
    planning_refine,
    planning_commit,
    _next_backlog_id,
    _compute_priority,
    _prioritize,
    _load_backlog,
    _save_backlog,
)

__all__ = [
    "eprint",
    "load_json",
    "cmd_backlog",
    "cmd_planning",
    "backlog_init",
    "backlog_add",
    "backlog_list",
    "backlog_prioritize",
    "backlog_refine",
    "backlog_select",
    "planning_propose",
    "planning_refine",
    "planning_commit",
    "_next_backlog_id",
    "_compute_priority",
    "_prioritize",
    "_load_backlog",
    "_save_backlog",
]
