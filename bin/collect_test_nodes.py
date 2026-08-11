#!/usr/bin/env python3
"""bin/collect_test_nodes.py — imprime los node IDs de la suite de tests.

Gate de estabilidad: permite a bin/check-test-stability.sh comparar la
recolección entre DOS procesos independientes. pytest 9 no lista los nodos
por CLI (--collect-only solo imprime el conteo), así que se capturan vía
el hook pytest_collection_finish.

Uso:
    python3 bin/collect_test_nodes.py [TEST_DIR]
"""
from __future__ import annotations

import contextlib
import io
import sys

import pytest


class _Collector:
    """Captura los node IDs recolectados (orden estable: sorted)."""

    def __init__(self) -> None:
        self.node_ids: list[str] = []

    def pytest_collection_finish(self, session) -> None:
        self.node_ids = sorted(item.nodeid for item in session.items)


def main(argv: list[str]) -> int:
    test_dir = argv[0] if argv and not argv[0].startswith("-") else "tests/"
    collector = _Collector()
    buf = io.StringIO()
    # pytest.main escribe su propio reporte a stdout; se captura para que el
    # stdout del helper contenga SOLO los node IDs (un nodo por línea).
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = pytest.main(
            [test_dir, "--collect-only", "-q", "-p", "no:cacheprovider"],
            plugins=[collector],
        )
    if rc != 0:
        print("pytest --collect-only falló:", file=sys.stderr)
        print(buf.getvalue(), file=sys.stderr)
        return rc
    for nid in collector.node_ids:
        print(nid)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
