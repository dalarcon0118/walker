"""tests/test_shared_kernel_no_prints_bdd.py — T-08 (H-2): cero prints en la
librería del shared kernel (canal MCP stdio intacto).

Meta-test grep-based (failure_action de T-08): cualquier print() que
reaparezca en los módulos de LIBRERÍA del runtime (cognitive_graph,
excluyendo `migrate_cli.py`, que es un CLI explícito one-shot) rompe este
test. La convención es `logging.getLogger(...)` — la configuración de
handlers la hace el composition root, nunca la librería.
"""

from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "fast_mcp" / "shared_kernel" / "cognitive_graph"
# CLI explícito one-shot: imprimir es su propósito (no entra a procesos runtime)
EXCLUDED = {"migrate_cli.py"}


def test_no_print_in_runtime_library():
    offenders: list[str] = []
    for path in sorted(PKG.glob("*.py")):
        if path.name in EXCLUDED:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\bprint\s*\(", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "prints en módulos de librería del shared kernel — corrompen el canal "
        f"MCP stdio (H-2/T-08):\n" + "\n".join(offenders)
    )