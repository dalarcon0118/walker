"""
_walker/ — BACKWARD COMPATIBILITY SHIM

This module re-exports from features/engine/ for backward compatibility.
New code should import directly from features.engine.
"""
import sys
import os

# Add project root to path so features/ and libs/ are importable
_walker_dir = os.path.dirname(os.path.abspath(__file__))      # .../scripts/_walker/
_scripts_dir = os.path.dirname(_walker_dir)                    # .../scripts/
_project_root = os.path.dirname(_scripts_dir)                  # .../walker/
for p in [_project_root, _scripts_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Re-export all names from features.engine
from features.engine import *

# Make specific submodules accessible
from features.engine import domain, application, infrastructure
