#!/usr/bin/env python3
"""Shim: deep_analysis.py — reexporta desde features (ADR-WALKER-010).

Implementación real migrada a features/code_analysis/application/deep_analysis.py.
Conservado para compatibilidad con spring/create.py (_compile_analysis).
"""
from __future__ import annotations

import sys
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from features.code_analysis.application.deep_analysis import (  # noqa: E402,F401
    eprint,
    load_json,
    write_json,
    validate_layer_data,
    build_layer_description,
    init_analysis,
    submit_layer,
    compile_analysis,
    finalize_analysis,
    analysis_status,
    LAYERS,
    LAYER_ORDER,
)

# Compatibilidad con spring/create.py (referencias _HAVE_ANALYZE / _compile_analysis)
_compile_analysis = compile_analysis
_HAVE_ANALYZE = True

__all__ = [
    "eprint",
    "load_json",
    "write_json",
    "validate_layer_data",
    "build_layer_description",
    "init_analysis",
    "submit_layer",
    "compile_analysis",
    "finalize_analysis",
    "analysis_status",
    "LAYERS",
    "LAYER_ORDER",
    "_compile_analysis",
    "_HAVE_ANALYZE",
]
