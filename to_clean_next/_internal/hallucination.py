#!/usr/bin/env python3
"""Shim: hallucination.py — reexporta desde features (ADR-WALKER-010).

Implementación real migrada a features/code_analysis/infrastructure/hallucination.py.
Conservado para compatibilidad con qlty_check.py, task/research.py y test_basic.py.
"""
from __future__ import annotations

import sys
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from features.code_analysis.infrastructure.hallucination import (  # noqa: E402,F401
    eprint,
    find_project_root,
    check_python_import,
    scan_python_file,
    scan_ts_file,
    scan_file,
    scan_directory,
)

__all__ = [
    "eprint",
    "find_project_root",
    "check_python_import",
    "scan_python_file",
    "scan_ts_file",
    "scan_file",
    "scan_directory",
]
