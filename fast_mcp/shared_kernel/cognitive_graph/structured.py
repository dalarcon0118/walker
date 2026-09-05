"""cognitive_graph/structured.py — F2: columnas generadas + índices SQL + router (D3).

La normalización física para filtros estructurados (ADR-WALKER-075 D3):
columnas GENERATED VIRTUAL sobre `docs` para los campos calientes del
modelo + índices SQL por columna + `idx_edges_dst` para `get_edges` por
dst. El router de search despacha los filtros estructurados a `WHERE`
indexado y carga SOLO los docs matcheados (M-07) — sin materializar el
read model (los índices resuelven a C speed; el JSON se parsea por hit).

Columnas (Enmienda 1 — 4 genéricas + 2 trace/delta):
    doc_type    $.type          exacto      (paridad con _type_index: RAW)
    doc_name    $.name          lowered     (paridad con _field_index)
    doc_path    $.path          lowered
    doc_scope   $.scope         lowered
    doc_event   $.event_type    lowered     (trace)
    doc_sprint  $.sprint_id     lowered     (delta)

Semántica de paridad con el path Python (Index.search / _index_doc):
  - `type` → _type_index guarda el valor RAW y el filtro compara exacto.
  - el resto → _field_index guarda `val.lower()` y el filtro compara
    `qvalue.lower()` → la columna genera `LOWER(CAST(...))` y el router
    bindea el parámetro ya lowered.
  - fallback metadata: `_match` intenta `metadata.<campo>` cuando el
    top-level no matchea → las columnas lowered hacen COALESCE(top,
    metadata) para cubrir ambos emplazamientos con UN índice.

Todos los DDL son idempotentes por inspección (PRAGMA table_info /
CREATE INDEX IF NOT EXISTS) — sin marker: la columna/índice es su propio
estado (§11 F2: "no-op si ya existen").

VIRTUAL, nunca STORED (M-06): cero duplicación de payload — el índice usa
el json_extract en write time, el valor no se copia a la fila.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .storage_errors import StorageStateError

# L2: patron CANONICO de identificador SQL (ASCII puro). fts.py lo re-exporta;
# nunca definir un segundo validador de identificadores en el shared kernel.
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# rfield (resuelto por IndexSchema.resolve_field) → (columna, expresión, modo)
DOC_COLUMN_SPECS: dict[str, tuple[str, str, str]] = {
    "type": ("doc_type", "CAST(json_extract(data, '$.type') AS TEXT)", "exact"),
    "name": (
        "doc_name",
        "LOWER(CAST(COALESCE(json_extract(data, '$.name'), "
        "json_extract(data, '$.metadata.name')) AS TEXT))",
        "lower",
    ),
    "path": (
        "doc_path",
        "LOWER(CAST(COALESCE(json_extract(data, '$.path'), "
        "json_extract(data, '$.metadata.path')) AS TEXT))",
        "lower",
    ),
    "scope": (
        "doc_scope",
        "LOWER(CAST(COALESCE(json_extract(data, '$.scope'), "
        "json_extract(data, '$.metadata.scope')) AS TEXT))",
        "lower",
    ),
    "event_type": (
        "doc_event",
        "LOWER(CAST(COALESCE(json_extract(data, '$.event_type'), "
        "json_extract(data, '$.metadata.event_type')) AS TEXT))",
        "lower",
    ),
    "sprint_id": (
        "doc_sprint",
        "LOWER(CAST(COALESCE(json_extract(data, '$.sprint_id'), "
        "json_extract(data, '$.metadata.sprint_id')) AS TEXT))",
        "lower",
    ),
}

_COLUMN_INDEX: dict[str, str] = {
    spec[0]: f"idx_docs_{spec[0].removeprefix('doc_')}"
    for spec in DOC_COLUMN_SPECS.values()
}

EDGES_DST_INDEX = "idx_edges_dst"


def _validate_ident(name: str) -> str:
    """Los nombres llegan a SQL interpolado — whitelist estricta ASCII.

    Canonical (L2): el UNICO validador de identificadores del shared kernel —
    fts.py lo re-exporta. ASCII puro: `^[A-Za-z_][A-Za-z0-9_]*$` (isalnum
    aceptaría unicode y dígitos iniciales, que romperían SQL interpolado).
    """
    if not _SAFE_IDENT.match(name):
        raise StorageStateError(
            f"identificador SQL inválido: {name!r} (patrón {_SAFE_IDENT.pattern}) — "
            "solo ASCII simple, sin puntos ni comillas."
        )
    return name


def ensure_structured_indexes(conn: sqlite3.Connection) -> dict:
    """F2 (§11): DDL idempotente — columnas VIRTUAL faltantes + índices.

    Idempotente por inspección: columna en PRAGMA table_xinfo (table_info
    NO lista columnas generadas — son hidden), índice con IF NOT EXISTS.
    ALTER ADD COLUMN VIRTUAL es instantáneo (solo esquema, cero rewrite de
    filas — M-06).
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_xinfo(docs)")}
    added: list[str] = []
    with conn:
        for rfield, (col, expr, _mode) in DOC_COLUMN_SPECS.items():
            _validate_ident(col)
            if col not in existing:
                conn.execute(
                    f"ALTER TABLE docs ADD COLUMN {col} TEXT "
                    f"GENERATED ALWAYS AS ({expr}) VIRTUAL"
                )
                added.append(col)
        for col in _COLUMN_INDEX:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {_COLUMN_INDEX[col]} "
                f"ON docs (`index`, {col})"
            )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {EDGES_DST_INDEX} ON edges (`index`, dst)"
        )
    return {
        "added_columns": added,
        "columns": [s[0] for s in DOC_COLUMN_SPECS.values()],
        "indexes": list(_COLUMN_INDEX.values()) + [EDGES_DST_INDEX],
    }


def resolve_filters(
    schema: Any, filters: dict
) -> list[tuple[str, Any]] | None:
    """Filtros estructurados → cláusulas SQL, o None si alguno no es resoluble.

    Paridad con el path Python (M-08): los filtros con valores no-string
    (o operadores dict/list) no son indexados por _field_index → None =
    fallback a base (jamás semántica distinta entre rutas).
    """
    clauses: list[tuple[str, Any]] = []
    for qfield, qvalue in filters.items():
        if qfield == "_id":
            clauses.append(("id", str(qvalue)))
            continue
        if isinstance(qvalue, (dict, list)):
            return None  # operadores ($contains...) → path base
        if not isinstance(qvalue, str):
            return None  # _field_index solo indexa strings → path base
        rfield = schema.resolve_field(qfield)
        if rfield == "_id":
            clauses.append(("id", qvalue))
            continue
        spec = DOC_COLUMN_SPECS.get(rfield)
        if spec is None:
            return None  # campo sin columna generada → path base
        col, _expr, mode = spec
        if mode == "exact":
            clauses.append((col, qvalue))
        else:
            clauses.append((col, qvalue.lower()))
    return clauses


def search_structured(
    conn: sqlite3.Connection,
    index: str,
    clauses: list[tuple[str, Any]],
    max_results: int = 50,
    offset: int = 0,
) -> dict:
    """Resuelve por índices SQL y carga SOLO la página (M-07).

    total = COUNT(*) por el índice (C-speed); hits = la página pedida con
    json.loads SOLO de los docs matcheados — jamás `SELECT *` + filtro
    Python sobre todo el índice.
    """
    _validate_ident(index)
    where = ["`index` = ?"]
    params: list[Any] = [index]
    for col, value in clauses:
        _validate_ident(col)
        where.append(f"{col} = ?")
        params.append(value)
    where_sql = " AND ".join(where)
    total = int(conn.execute(
        f"SELECT COUNT(*) FROM docs WHERE {where_sql}", params
    ).fetchone()[0])
    rows = conn.execute(
        f"SELECT id, data FROM docs WHERE {where_sql} "
        f"ORDER BY rowid LIMIT ? OFFSET ?",
        [*params, int(max_results), int(offset)],
    ).fetchall()
    hits = [
        {"_id": r[0], "_index": index, "_score": 1.0, "_source": json.loads(r[1])}
        for r in rows
    ]
    return {"total": total, "hits": hits, "from": int(offset), "size": int(max_results)}
