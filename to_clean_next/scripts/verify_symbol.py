#!/usr/bin/env python3
"""Verify code symbols exist using ast-grep (same mechanism as task.py)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from task import _verify_symbols_in_files, find_project_root, _extract_project_id

def main():
    if len(sys.argv) < 3:
        print("Usage: verify_symbol.py <file> <symbol> [<symbol> ...]")
        sys.exit(1)
    
    filepath = sys.argv[1]
    symbols = set(sys.argv[2:])
    chain_dir = os.path.dirname(os.path.abspath(filepath))
    project_root = find_project_root(chain_dir)
    project_id = _extract_project_id(chain_dir)
    
    result = _verify_symbols_in_files(symbols, [filepath], project_root, project_id)
    
    missing = symbols - set(result.keys())
    if missing:
        print(f"FAIL: symbols not found: {missing}")
        sys.exit(1)
    
    for sym in sorted(symbols):
        print(f"OK: {sym} -> {result[sym][0]}")
    sys.exit(0)

if __name__ == "__main__":
    main()
