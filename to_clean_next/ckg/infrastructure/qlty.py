from __future__ import annotations

from ckg.infrastructure.scanners import QltyScanner


def run_quality_gates(project_root: str) -> dict:
    scanner = QltyScanner(project_root)
    return scanner.run()
