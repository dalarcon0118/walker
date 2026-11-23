"""tests/test_registry_last_accessed_bdd.py — `last_accessed` deja de estar congelado.

Bug medido 2026-11-22: `data/projects.json` seguía con
`last_accessed: 2026-11-01` tras semanas de uso porque nadie lo actualizaba.
Escribir en cada resolución ensuciaría un archivo trackeado (un write por
tool), así que el refresco es debounced.

Contrato:
  - `touch` refresca un timestamp viejo y conserva el resto del registro.
  - Un timestamp reciente no provoca escritura.
  - Un timestamp ilegible se refresca (no rompe la resolución).
  - Un project_id ausente no crea entradas fantasma.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fast_mcp.shared_kernel.registry_store import RegistryStore


def _write_registry(root, pid: str, last_accessed: str) -> None:
    path = root / "data" / "projects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "projects": {
            pid: {
                "project_id": pid,
                "name": pid,
                "repo_path": "/tmp/repo",
                "active": True,
                "registered_at": "2026-11-01T00:00:00+00:00",
                "last_accessed": last_accessed,
            }
        },
        "updated_at": "2026-11-01T00:00:00+00:00",
    }))


def _read(root, pid: str) -> dict:
    path = root / "data" / "projects.json"
    return json.loads(path.read_text())["projects"][pid]


def test_touch_refresca_un_timestamp_viejo(tmp_path):
    _write_registry(tmp_path, "demo", "2026-11-01T00:00:00+00:00")

    wrote = RegistryStore(tmp_path).touch("demo")

    assert wrote is True
    assert _read(tmp_path, "demo")["last_accessed"] != "2026-11-01T00:00:00+00:00"


def test_touch_conserva_el_resto_del_registro(tmp_path):
    _write_registry(tmp_path, "demo", "2026-11-01T00:00:00+00:00")

    RegistryStore(tmp_path).touch("demo")

    entry = _read(tmp_path, "demo")
    assert entry["repo_path"] == "/tmp/repo"
    assert entry["registered_at"] == "2026-11-01T00:00:00+00:00"


def test_touch_no_escribe_si_es_reciente(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    _write_registry(tmp_path, "demo", now)

    assert RegistryStore(tmp_path).touch("demo") is False
    assert _read(tmp_path, "demo")["last_accessed"] == now


def test_touch_respeta_el_debounce_configurable(tmp_path):
    recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    _write_registry(tmp_path, "demo", recent)

    assert RegistryStore(tmp_path).touch("demo", max_age_seconds=60) is False
    assert RegistryStore(tmp_path).touch("demo", max_age_seconds=10) is True


def test_touch_con_timestamp_ilegible_refresca(tmp_path):
    _write_registry(tmp_path, "demo", "no-es-una-fecha")

    assert RegistryStore(tmp_path).touch("demo") is True
    assert _read(tmp_path, "demo")["last_accessed"] != "no-es-una-fecha"


def test_touch_de_proyecto_inexistente_no_inventa_entrada(tmp_path):
    _write_registry(tmp_path, "demo", "2026-11-01T00:00:00+00:00")

    assert RegistryStore(tmp_path).touch("fantasma") is False
    registry = json.loads((tmp_path / "data" / "projects.json").read_text())
    assert "fantasma" not in registry["projects"]
