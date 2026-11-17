"""test_trace_load_all_bdd.py — el trace no puede leerse por ventana de 50.

Bug medido 2026-11-17: `load_traces` llamaba `search(index, {})` sin `size`, y el
default de search es 50. Con el trace en 884 eventos, gates (`has_skill_load`,
`has_kg_query`) y auditorías evaluaban sobre una ventana arbitraria: el evento
que buscaban podía existir y no verse, con un "no encontrado" silencioso.
"""

from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.features.event_tracing.application.services.trace_factory import (
    build_trace_service,
    invalidate_trace_service,
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cognitive" / "projects" / "demo" / "data").mkdir(parents=True)
    (root / "project.json").write_text(
        json.dumps({"project_id": "demo", "repo_path": str(root)})
    )
    return root


def test_load_traces_devuelve_mas_de_50_eventos(tmp_path):
    root = _project(tmp_path)
    service = build_trace_service(str(root))
    assert service is not None

    for i in range(60):
        service.push_event(str(root), {
            "event_type": "probe",
            "source": "test",
            "data": {"i": i},
        })
    invalidate_trace_service(str(root))

    events = build_trace_service(str(root))._repo.load_traces(str(root))  # noqa: SLF001

    assert len(events) >= 60, f"solo se ven {len(events)} eventos de 60"
