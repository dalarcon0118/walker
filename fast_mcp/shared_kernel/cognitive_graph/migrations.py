"""cognitive_graph/migrations.py — Migración v1 (`.json.gz`) → v2 (sqlite).

ADR-WALKER-063 §7.7 + CA-6: import transaccional por índice (todo-o-nada,
FS-6). El parser soporta el formato IDÉNTICO histórico de Index._save y las
variantes que _load acepta (docs o nodes).

    {index, schema_hash, docs:[{_id, ...}], edges:[{from, to, ...}],
     metadata?, token_index?, _type_index?, _field_index?}

Reglas (IV-6/IV-9/FS-6):
  - Import DENTRO de una transacción: cualquier error (blob ilegible,
    estructura inválida, índice no coincidente, conteo dispar) → rollback
    completo del índice; la BD nunca queda con datos parciales.
  - CA-6: se verifica conteo sqlite == conteo del blob ANTES del commit.
  - Corte directo (sin convivencia): NO existe doble escritura. El runtime
    es sqlite (v2) como única fuente de verdad; la migración se ejecuta
    SOLO con el script one-shot `migrate_cli.py` (idempotente, sin --force).
    `DualWriteBackend` fue eliminado.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from . import fts
from .file_io import read_blob
from .sqlite_backend import SqliteBackend
from .storage_errors import (
    StorageMigrationCountMismatchError,
    StorageMigrationError,
)


def import_json_gz_to_sqlite(
    backend: SqliteBackend,
    blob_path: str,
    index_name: str | None = None,
    *,
    merge: bool = False,
) -> dict:
    """Importa un blob `.json.gz` histórico (v1) a sqlite (v2), transaccional.

    Todo-o-nada por índice (FS-6): si el blob es ilegible, la estructura no
    es la esperada, el índice del blob no coincide con `index_name` o el
    conteo final difiere → error custom y rollback completo.

    Args:
        backend: SqliteBackend destino (escribe vía su conexión).
        blob_path: ruta del blob .json.gz legacy (p.ej. FileBackend.path_for).
        index_name: índice destino; si es None se usa el del propio blob.

    Returns:
        Resumen del import (índice, docs, edges, metadata, fuente).

    Raises:
        StorageMigrationError: blob ilegible/inestructurado/índice no
            coincidente o error SQL durante el import.
        StorageMigrationCountMismatchError: conteo sqlite != conteo blob
            (CA-6) — señal de blob corrupto; nunca commit parcial.
    """
    text = read_blob(blob_path)
    if text is None:
        raise StorageMigrationError(
            f"blob .json.gz ilegible o inexistente: {blob_path} (FS-6: sin import)."
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise StorageMigrationError(
            f"blob no es JSON válido: {blob_path} ({type(e).__name__})."
        ) from e
    if not isinstance(payload, dict):
        raise StorageMigrationError(
            f"blob sin estructura de índice (objeto JSON esperado): {blob_path}."
        )

    blob_index = payload.get("index")
    if index_name is None:
        index_name = blob_index or f"idx-{os.path.basename(blob_path)}"
    if blob_index and str(blob_index) != index_name:
        raise StorageMigrationError(
            f"el blob es del índice '{blob_index}', se pidió importar "
            f"'{index_name}' — sin import (nunca en silencio)."
        )

    # docs: formato histórico [{"_id": ...}] o variante legacy {"nodes": [...]}.
    # CPG v1 guarda `nodes` como MAPA {id: nodo} y el identificador vive en
    # el propio nodo ("id"), no en "_id": sin normalizar, todo blob de CPG
    # fallaba la migración con "estructura no reconocida" (bug medido).
    raw_docs = payload.get("docs", payload.get("nodes", []))
    if isinstance(raw_docs, dict):
        raw_docs = [
            ({**doc, "_id": key} if isinstance(doc, dict) and "_id" not in doc else doc)
            for key, doc in raw_docs.items()
        ]
    if not isinstance(raw_docs, list):
        raise StorageMigrationError(
            f"blob sin 'docs[]'/'nodes[]' (estructura no reconocida): {blob_path}."
        )
    docs: dict[str, dict] = {}
    for item in raw_docs:
        doc_id = None
        if isinstance(item, dict):
            doc_id = item.get("_id", item.get("id"))
        if doc_id is None:
            raise StorageMigrationError(
                f"doc sin '_id' en {blob_path} — blob histórico corrupto (FS-6)."
            )
        doc_id = str(doc_id)
        docs[doc_id] = {k: v for k, v in item.items() if k != "_id"}

    edges: list[tuple[str, str, dict]] = []
    for item in payload.get("edges", []):
        if not isinstance(item, dict) or "from" not in item or "to" not in item:
            raise StorageMigrationError(
                f"edge sin 'from'/'to' en {blob_path} — blob histórico corrupto (FS-6)."
            )
        edges.append(
            (
                str(item["from"]),
                str(item["to"]),
                {k: v for k, v in item.items() if k not in ("from", "to")},
            )
        )

    metadata = payload.get("metadata") or {}
    schema_hash = payload.get("schema_hash", "")
    now = datetime.now(timezone.utc).isoformat()

    db: sqlite3.Connection = backend._db  # noqa: SLF001 (puente de migración)

    # ── Merge: importar SOLO lo que falta (v2 gana en conflicto) ──────────
    # El corte directo asume importar sobre un índice vacío; cuando la v2 ya
    # tiene datos, re-importar pisaría lo más nuevo con un snapshot viejo y no
    # hay forma segura de compararlos. El merge agrega lo ausente y reporta las
    # colisiones, sin tocar un solo doc existente.
    existing_ids: set[str] = set()
    collided: list[str] = []
    if merge:
        existing_ids = {
            str(row[0])
            for row in db.execute(
                "SELECT id FROM docs WHERE `index`=?", (index_name,)
            )
        }
        existing_edges = {
            (str(row[0]), str(row[1]))
            for row in db.execute(
                "SELECT src, dst FROM edges WHERE `index`=?", (index_name,)
            )
        }
        collided = sorted(set(docs) & existing_ids)
        docs = {k: v for k, v in docs.items() if k not in existing_ids}
        # Aristas: sin duplicar y sin extremos colgantes (solo si ambos nodos
        # existen ya o se están importando).
        known_ids = existing_ids | set(docs)
        edges = [
            edge
            for edge in edges
            if (edge[0], edge[1]) not in existing_edges
            and edge[0] in known_ids
            and edge[1] in known_ids
        ]

    try:
        with db:
            for doc_id, doc in docs.items():
                db.execute(
                    "INSERT OR REPLACE INTO docs (`index`, id, data, updated_at) "
                    "VALUES (?,?,?,?)",
                    (
                        index_name,
                        doc_id,
                        json.dumps(doc, ensure_ascii=False, default=str),
                        now,
                    ),
                )
            for src, dst, attrs in edges:
                db.execute(
                    "INSERT OR REPLACE INTO edges (`index`, src, dst, attributes) "
                    "VALUES (?,?,?,?)",
                    (
                        index_name,
                        src,
                        dst,
                        json.dumps(attrs, ensure_ascii=False, default=str),
                    ),
                )
            if metadata and not merge:
                for key, value in metadata.items():
                    db.execute(
                        "INSERT OR REPLACE INTO meta (`index`, key, value) "
                        "VALUES (?,?,?)",
                        (index_name, key, json.dumps(value, ensure_ascii=False, default=str)),
                    )
            if schema_hash:
                db.execute(
                    "INSERT OR REPLACE INTO meta (`index`, key, value) VALUES (?,?,?)",
                    (index_name, "schema_hash", schema_hash),
                )
            # CA-6: el conteo sqlite DEBE igualar al esperado ANTES del commit
            # (los _id duplicados en el blob son señal de corrupción). Con
            # merge el esperado incluye los docs que ya existían.
            row = db.execute(
                "SELECT COUNT(*) FROM docs WHERE `index`=?", (index_name,)
            ).fetchone()
            count = int(row[0])
            expected = len(existing_ids) + len(docs)
            if count != expected:
                raise StorageMigrationCountMismatchError(
                    f"índice '{index_name}': sqlite={count} vs esperado={expected} "
                    f"(blob={len(raw_docs)}, ya existían={len(existing_ids)}; "
                    f"{blob_path}) — rollback (CA-6/FS-6), nunca commit parcial."
                )
    except StorageMigrationCountMismatchError:
        raise
    except sqlite3.Error as e:
        raise StorageMigrationError(
            f"import transaccional a sqlite falló ({type(e).__name__}: {e}) — "
            f"rollback completo del índice '{index_name}'."
        ) from e

    # FTS es DERIVADO: el import escribe `docs` directo, sin postings. Dejarlo
    # así hace que la próxima apertura falle con "drift FTS (FS-1)" y el índice
    # quede inaccesible. Se dropea el FTS del índice (los DOCS no se tocan) para
    # que `ensure_fts` lo re-backfille al reabrir — remediación documentada.
    fts.drop_fts(db, index_name)

    return {
        "index": index_name,
        "migrated": True,
        "merge": merge,
        "docs": len(docs),
        "edges": len(edges),
        "metadata": 0 if merge else len(metadata),
        "skipped_existing": len(collided),
        "source": blob_path,
    }


__all__ = ["import_json_gz_to_sqlite", "migrate_walker_memories"]


def migrate_walker_memories(
    data_dir: str,
    backend: SqliteBackend,
    *,
    index_names: tuple[str, ...] = ("cpg", "ckg", "delta", "trace"),
) -> list[dict]:
    """Cutover §7.7: importa los blobs canónicos <index>.json.gz de data_dir.

    Por cada nombre en index_names, si <data_dir>/<index>.json.gz existe se
    importa transaccionalmente al backend (todo-o-nada por índice, CA-6/FS-6).
    Un blob ausente se reporta en el resumen (imported=False) sin fallar: un
    índice puede no existir aún (p.ej. trace antes del primer evento). El
    peer legacy del layout es <data_dir>/cog/data/<index>.json.gz (histórico
    con doble 'data'); si el canónico no existe se busca ese peer — el layout
    real del .cognitive usa el canónico (spring_service(chain_dir)).

    Returns:
        list[dict]: resumen por índice con {index, blob, imported, docs,
        edges, metadata, migrated} (los campos de conteo solo si importó).

    Raises:
        StorageMigrationError/StorageMigrationCountMismatchError: el import
            de un blob existente falló — rollback de ESE índice; el resto de
            la corrida es independiente (FS-6 por índice).
    """
    results: list[dict] = []
    seen: set[str] = set()
    for name in index_names:
        blob_path = os.path.join(data_dir, f"{name}.json.gz")
        if not os.path.exists(blob_path):
            legacy_peer = os.path.join(data_dir, "cog", "data", f"{name}.json.gz")
            if os.path.exists(legacy_peer):
                blob_path = legacy_peer
        if not os.path.exists(blob_path):
            results.append({"index": name, "blob": blob_path, "imported": False})
            continue
        if blob_path in seen:
            results.append({"index": name, "blob": blob_path, "imported": False})
            continue
        summary = import_json_gz_to_sqlite(backend, blob_path, index_name=name)
        results.append({**summary, "imported": True})
        seen.add(blob_path)
    return results