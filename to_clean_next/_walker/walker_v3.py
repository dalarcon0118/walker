#!/usr/bin/env python3
"""
walker_v3.py — Walker v3 Multi-Tenant CLI.

Pure entry point. Delegates all logic to features/cli_interface.
"""
from __future__ import annotations
import sys


def main() -> int:
    """Main entry point. Delegates to the CLI interface."""
    from features.cli_interface import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
