"""BDD: el delta no pierde escrituras por el CAS de generación (T-12a).

Contexto medido (2026-11-19): `implement_task` devolvía `ok: true` sin `spec` y
sin plan. El backend rechazaba el `store` con

    "SqliteIndex['delta']: el storage cambió bajo el cache (generación N !=
     committed) — otro proceso escribió. Remediación: reabrir/recargar el
     índice y reintentar (jamás sobrescribir ciego)."

y `CognitiveDeltaRepository.save_session`/`save_spec` descartaban ese
`{"ok": False}`. Un conflicto real se convertía en una escritura perdida
silenciosa (INV-016/M-09), y los gates de arriba (scope, cierre, plan) actuaban
sobre un estado vacío.

Contrato:
  1. Un conflicto de generación se resuelve como manda su propio error:
     refrescar el diff-cache y reintentar UNA vez.
  2. Si aun así el backend rechaza, el repo FALLA (`DeltaWriteError`): nunca
     más un "ok" que no persistió.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def project_dir(tmp_path: Path) -> str:
    """Repo desechable con layout cognitivo: el data_dir queda dentro de tmp."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "project.json").write_text(
        json.dumps(
            {
                "project": {
                    "meta": {"project_id": "retry-bdd"},
                    "location": {"repo_path": str(tmp_path)},
                }
            }
        )
    )
    (tmp_path / ".cognitive" / "projects" / "retry-bdd" / "data").mkdir(parents=True)
    return str(tmp_path)


def _service(project_dir: str):
    from fast_mcp.composition import _build_sprint_service

    return _build_sprint_service(project_dir)


def test_store_reintenta_cuando_otro_proceso_avanzo_la_generacion(project_dir, caplog):
    """Cache stale + otro proceso escribió ⇒ el write se refresca y persiste."""
    stale = _service(project_dir)
    # El primer store carga el diff-cache (generación N) y la deja en memoria.
    stale.create_spring("SPR-A", goal="primera escritura de este proceso")

    other = _service(project_dir)
    other.create_spring("SPR-OTRO", goal="escritura de otro proceso")

    # La generación del cache de `stale` ya no es la committed: sin la
    # remediación este store devolvía ok=False y se descartaba en silencio.
    with caplog.at_level("WARNING", logger="walker.cognitive_graph.sqlite_backend"):
        created = stale.create_spring("SPR-STALE", goal="cache vieja")

    assert created.get("spring_id") == "SPR-STALE"
    assert any("conflicto de generación" in r.message for r in caplog.records), (
        "el test no ejercitó el CAS: sin conflicto no hay nada que reintentar"
    )
    fresh = _service(project_dir)
    assert fresh.get_spring("SPR-STALE") is not None, "el reintento no persistió"


def test_save_spec_no_se_pierde_tras_conflicto(project_dir, caplog):
    """El caso real: add_spec en un cache stale debe quedar visible en get_specs."""
    stale = _service(project_dir)
    stale.create_spring("SPR-1", goal="sprint con cache")  # carga el cache

    other = _service(project_dir)
    other.create_spring("SPR-2", goal="otro proceso escribe")

    with caplog.at_level("WARNING", logger="walker.cognitive_graph.sqlite_backend"):
        spec = stale.add_spec("SPR-1", objective="spec tras conflicto", files=["a.py"])

    assert spec and spec.get("spec_id")
    assert any("conflicto de generación" in r.message for r in caplog.records)
    fresh = _service(project_dir)
    assert len(fresh.get_specs("SPR-1") or []) == 1, "la spec se perdió"


def test_save_session_falla_si_el_backend_rechaza(project_dir, monkeypatch):
    """Fail-fast (INV-016): un store rechazado no puede devolver "ok"."""
    from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import (
        DeltaWriteError,
    )

    svc = _service(project_dir)
    monkeypatch.setattr(
        svc._repo._cg,
        "store",
        lambda index, doc_id, data: {"ok": False, "error": "rechazado a propósito"},
    )

    with pytest.raises(DeltaWriteError) as exc:
        svc.create_spring("SPR-NOPE", goal="no debe persistir")

    assert "SPR-NOPE" in str(exc.value)
    assert "rechazado a propósito" in str(exc.value)


def test_require_ok_es_tolerante_a_fakes():
    """Los dobles de test devuelven None: el guard no debe romperlos."""
    from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import (
        DeltaWriteError,
        _require_ok,
    )

    _require_ok(None, "fake")
    _require_ok({"ok": True}, "ok")
    with pytest.raises(DeltaWriteError):
        _require_ok({"ok": False, "error": "boom"}, "x")
