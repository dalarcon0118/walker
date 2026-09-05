"""H0 — GATE §10.0 ADR-WALKER-075: thin CLI sobre la librería pre_migration.

T-04 (deuda del Anexo A #1): toda la lógica del gate (checks H-1..H-7,
guard v3, backup verificado, remediaciones) vive en
`fast_mcp/shared_kernel/cognitive_graph/pre_migration.py` (retorna
Result[CheckReport/GateReport, StorageStateError]). Este script solo
resuelve rutas (overrideables por env) e imprime el reporte.

Usage:
  python3 scripts/pre_migration_check.py
Env (tests/override):
  WALKER_REPO / WALKER_DATA_DIR / WALKER_DB_PATH / WALKER_BACKUP_DIR
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fast_mcp.shared_kernel.cognitive_graph.pre_migration import (  # noqa: E402
    Failure,
    run_gate,
)
from fast_mcp.shared_kernel.cognitive_graph.storage_errors import (  # noqa: E402
    StorageStateError,
)

REPO = os.environ.get("WALKER_REPO", ROOT)
DATA = os.environ.get(
    "WALKER_DATA_DIR",
    os.path.realpath(os.path.join(REPO, ".cognitive", "projects", "walker", "data")),
)
DB = os.environ.get("WALKER_DB_PATH", os.path.join(DATA, "cognitive.sqlite3"))
BACKUP_DIR = os.environ.get(
    "WALKER_BACKUP_DIR",
    os.path.realpath(os.path.join(REPO, ".cognitive", "backups")),
)


def main() -> int:
    result = run_gate(DB, DATA, BACKUP_DIR)
    if isinstance(result, Failure):
        err = result.failure()
        if isinstance(err, StorageStateError):
            print(f"GATE ABORTADO (StorageStateError): {err}", file=sys.stderr)
        else:
            print(f"GATE ABORTADO: {err}", file=sys.stderr)
        return 1
    report = result.unwrap()
    print("\n=== VEREDICTO FINAL H0 ===")
    for check in report.checks:
        print(f"{check.check}: {'✅ OK' if check.ok else '❌ FAIL'}  {check.detail}"
              + (f"  (remediación: {check.remediation})" if check.remediation else ""))
    print(f"backup verificado: {report.backup_path}")
    print(report.final_detail)
    print("H0: GATE VERDE — BD higienizada con backup pre-mutación verificado")
    return 0


if __name__ == "__main__":
    sys.exit(main())