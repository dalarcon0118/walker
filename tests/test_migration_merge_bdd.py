"""tests/test_migration_merge_bdd.py — merge v1→v2 sobre una BD ya poblada.

El corte directo (ADR-063) asume importar sobre un índice vacío: el CLI hace
SKIP si la BD ya tiene docs, porque re-importar pisaría datos más nuevos con un
snapshot viejo y no hay forma segura de compararlos.

Medido 2026-11-16 en react-native-frontend: la v2 tenía 23 trazas y el blob v1
548, así que 548 eventos históricos quedaban inalcanzables por el SKIP.

Escenarios:
  1. merge=True importa SOLO los docs ausentes y NO toca los existentes.
  2. merge=True filtra aristas duplicadas y con extremos colgantes.
  3. sin merge, importar sobre una BD poblada falla y hace rollback (es la
     razón por la que el CLI hace SKIP en vez de intentarlo).
"""

from __future__ import annotations

import gzip
import json

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from fast_mcp.shared_kernel.cognitive_graph.migrations import import_json_gz_to_sqlite
from fast_mcp.shared_kernel.cognitive_graph.schemas import PRODUCTION_INDEX_SCHEMAS

def _backend(tmp_path, index: str = "ckg"):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index(index, PRODUCTION_INDEX_SCHEMAS[index])
    return backend


def _blob(tmp_path, docs: dict, edges: list | None = None, index: str = "ckg") -> str:
    payload = {
        "index": index,
        "docs": [{"_id": key, **value} for key, value in docs.items()],
        "edges": edges or [],
    }
    path = tmp_path / f"{index}.json.gz"
    with gzip.open(path, "wt") as fh:
        fh.write(json.dumps(payload))
    return str(path)


def test_merge_importa_solo_lo_que_falta_y_no_pisa_v2(tmp_path):
    backend = _backend(tmp_path)
    backend.store_many(
        "ckg",
        {
            "a": {"statement": "v2-vigente", "type": "decision"},
            "b": {"statement": "v2-otro", "type": "decision"},
        },
    )
    blob = _blob(
        tmp_path,
        {
            "a": {"statement": "v1-viejo", "type": "decision"},
            "c": {"statement": "v1-faltante", "type": "decision"},
        },
    )

    summary = import_json_gz_to_sqlite(backend, blob, "ckg", merge=True)

    assert summary["docs"] == 1, "solo debe importarse el doc ausente"
    assert summary["skipped_existing"] == 1, "la colisión se reporta, no se pisa"

    docs = backend.get_docs("ckg")
    assert docs["a"]["statement"] == "v2-vigente", "v2 SIEMPRE gana en conflicto"
    assert docs["c"]["statement"] == "v1-faltante"


def test_merge_filtra_aristas_duplicadas_y_colgantes(tmp_path):
    backend = _backend(tmp_path)
    backend.store_many(
        "ckg",
        {
            "a": {"statement": "v2", "type": "decision"},
            "b": {"statement": "v2b", "type": "decision"},
        },
    )
    # La arista pre-existente se escribe en la tabla `edges`, que es el
    # contrato que lee la migración (`sync_edges` en índice ligero no la
    # materializa hasta el save; el merge debe deduplicar contra el storage).
    backend._db.execute(  # noqa: SLF001 — mismo puente que usa la migración
        "INSERT INTO edges (`index`, src, dst, attributes) VALUES (?,?,?,?)",
        ("ckg", "a", "b", json.dumps({"type": "SUPERSEDES"})),
    )
    blob = _blob(
        tmp_path,
        {"c": {"statement": "nuevo", "type": "decision"}},
        edges=[
            {"from": "a", "to": "b", "type": "SUPERSEDES"},  # ya existe
            {"from": "c", "to": "a", "type": "RELATES"},     # válida (ambos existen)
            {"from": "c", "to": "zzz", "type": "RELATES"},   # colgante: se descarta
        ],
    )

    summary = import_json_gz_to_sqlite(backend, blob, "ckg", merge=True)
    assert summary["docs"] == 1
    assert summary["edges"] == 1, "solo la arista nueva con ambos extremos conocidos"


def test_sin_merge_sobre_bd_poblada_pisa_v2(tmp_path):
    """Documenta POR QUÉ el CLI hace SKIP: sin merge, el import sobreescribe.

    El guard de conteo (CA-6) NO atrapa este caso: los ids que ya existen se
    reemplazan sin cambiar el total, así que un snapshot viejo gana sobre los
    datos nuevos sin ningún error. Esta es la razón por la que `migrate_cli`
    salta los índices poblados salvo con --merge explícito.
    """
    backend = _backend(tmp_path)
    backend.store_many("ckg", {"a": {"statement": "v2-vigente", "type": "decision"}})
    blob = _blob(
        tmp_path,
        {
            "a": {"statement": "v1-viejo", "type": "decision"},
            "c": {"statement": "v1-faltante", "type": "decision"},
        },
    )

    import_json_gz_to_sqlite(backend, blob, "ckg")

    docs = backend.get_docs("ckg")
    assert docs["a"]["statement"] == "v1-viejo", (
        "sin merge el blob viejo pisa el doc vigente (por eso el CLI hace SKIP)"
    )
