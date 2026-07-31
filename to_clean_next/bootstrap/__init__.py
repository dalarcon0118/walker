#!/usr/bin/env python3
"""
walker_v3.py — CLI del Walker v3 Multi-Tenant.

Entry point puro. Delega toda la lógica a _walker/cli/router.py.
"""
from __future__ import annotations
import sys


def main() -> int:
    """Punto de entrada principal.

    Configura el PATH y delega al router CLI.
    """
    from bootstrap.cli.router import main as router_main
    return router_main()


if __name__ == "__main__":
    sys.exit(main())
