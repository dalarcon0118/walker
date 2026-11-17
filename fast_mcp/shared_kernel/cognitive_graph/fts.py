"""cognitive_graph/fts.py — FTS5 external content por índice (F1, ADR-WALKER-075 D2).

El "inverted index" nativo de sqlite por cada índice lógico con fulltext en
schema (`cpg_fts`, `ckg_fts`, `delta_fts`, ...). Reemplaza al `TokenIndex`
RAM como camino de producción (D2): `MATCH` nativo C-speed, restart-proof,
sin tokenizar/filtrar en Python (M-01).

Física (D2 "sobre docs, rowid de sqlite como vínculo"):
  - VIEW `{index}_fts_src` = proyección de `docs` del índice con los campos
    fulltext extraídos por `json_extract` (COALESCE '' para NULL).
  - VIRTUAL TABLE `{index}_fts` = fts5 EXTERNAL CONTENT sobre esa view
    (`content=` view, `content_rowid='rowid'`) — solo guarda postings, el
    texto vive en `docs`. `fts.rowid == docs.rowid` (upsert de `_save`
    PRESERVA el rowid: ON CONFLICT DO UPDATE, no INSERT OR REPLACE).
  - Columnas FTS = `schema.search.fields` del índice (cpg: name/path/
    signature/kind; ckg: statement/rationale; delta: objective/title/...).

Invariantes (fail-fast, AGENTS.md):
  - M-02: el sync de FTS ocurre en la MISMA transacción del `_save`
    (apply_delete con valores VIEJOS capturados ANTES de tocar docs;
    apply_insert con valores NUEVOS después de escribirlos) y `replace_blob`
    resincroniza completo (es un _save con diff completo).
  - M-04: backfill verificado por conteo `count(docs) == count(fts)`.
  - FS-1: drift de conteos al abrir → `StorageStateError` accionable
    (remediación: `drop_fts` + reabrir = re-backfill, o restaurar backup).
  - FS-2: create+backfill+marker en UNA transacción — jamás una tabla FTS
    sin marker ('migración a medias' → error accionable, no auto-heal
    silencioso).
  - Identificadores validados por regex antes de interpolar SQL (los nombres
    de índice/campo jamás llegan crudos a un f-string SQL).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time

from .models import tokenize
from .storage_errors import StorageStateError
from .structured import _validate_ident  # L2: canonico ASCII (reexportado)

FTS_BATCH = 2000  # §8 `fts_backfill_batch`: backfill de migración acotado
# Marker de ciclo de vida en meta (system-key: excluida del passthrough de
# metadata de dominio — _save la preserva junto a __schema__/schema_hash).
MARKER_KEY = "fts_config"

# nombres de índice del runtime (cpg, ckg, delta, trace, cpg-ref-*) — el
# patron de ÍNDICE es distinto del de identificador (permite '.', '-', dígitos)
_SAFE_INDEX = re.compile(r"^[a-z][a-z0-9_.-]*$")


def fts_table(index: str) -> str:
    """Nombre de la VIRTUAL TABLE fts5 del índice (`cpg` → `cpg_fts`)."""
    return f"{index}_fts"


def fts_src(index: str) -> str:
    """Nombre de la VIEW external-content del índice (`cpg_fts_src`)."""
    return f"{index}_fts_src"


def _validate(index: str, fields: list[str]) -> None:
    """Fail-fast: identificadores jamás llegan crudos a SQL interpolado."""
    if not _SAFE_INDEX.match(index):
        raise StorageStateError(
            f"nombre de índice inválido para FTS: {index!r} (patrón "
            f"{_SAFE_INDEX.pattern}) — nunca interpolar identificadores crudos."
        )
    for f in fields:
        _validate_ident(f)  # L2: canonico compartido con structured.py


def _fts5_available(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT sqlite_compileoption_used('ENABLE_FTS5')"
        ).fetchone()
        return bool(row and row[0])
    except sqlite3.Error:
        return False


def counts(conn: sqlite3.Connection, index: str) -> tuple[int, int]:
    """(docs del índice, filas insertadas en el FTS).

    ATENCIÓN (external content): `COUNT(*) FROM fts` SE DELEGA al contenido
    (la view) — refleja docs, NO postings. Para el estado REAL del índice
    usar `reconcile()` (fts5vocab + sample-verify).
    """
    n_docs = conn.execute(
        "SELECT COUNT(*) FROM docs WHERE `index`=?", (index,)
    ).fetchone()[0]
    n_fts = conn.execute(f"SELECT COUNT(*) FROM {fts_table(index)}").fetchone()[0]
    return int(n_docs), int(n_fts)


SAMPLE_VERIFY_SIZE = 25  # FS-3 pattern: sample-verify determinista por índice


def _vocab_terms(conn: sqlite3.Connection, index: str) -> list[str]:
    """Términos del índice (fts5vocab 'col', ORDER BY term — determinista).

    El vocab lista TODOS los términos (incl. los de postings tombstoned por
    deletes pendientes de merge) — sirven como semillas de sampleo, la
    verdad de cada término la da `MATCH` (que aplica las eliminaciones
    vivas). El vocab NO expone rowids fiables: en modo 'row' su columna
    `doc` es un CONTEO de filas por término, no un rowid.
    """
    vocab = f"_fts_vocab_{index.replace('.', '_').replace('-', '_')}"
    try:
        conn.execute(
            f"CREATE VIRTUAL TABLE {vocab} USING fts5vocab('{fts_table(index)}', 'col')"
        )
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT term FROM {vocab} ORDER BY term"
        )]
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {vocab}")


def reconcile(
    conn: sqlite3.Connection,
    index: str,
    fields: list[str],
    sample_size: int = SAMPLE_VERIFY_SIZE,
) -> dict:
    """M-04/FS-1: verificación REAL de paridad docs↔FTS — 100% vía MATCH.

    (fts5vocab expone postings crudos INCLUYENDO tombstones de deletes
    pendientes de merge — no es fuente de rowids vivos; la verdad es MATCH,
    que aplica las eliminaciones.)

    1) términos→docs: sample determinista de términos; todo rowid que
       MATCH devuelva DEBE existir en la view (posting de doc borrado sin
       sync = hit muerto).
    2) docs→postings: sample determinista de docs con tokens; cada uno debe
       responder MATCH por su rowid (doc sin tokens → sin postings por
       diseño, se salta).

    Returns {"docs": n, "checked": n}. Raises StorageStateError accionable.
    """
    _validate(index, fields)
    view_ids: dict[int, str] = {
        int(r[0]): r[1] for r in conn.execute(
            f"SELECT rowid, id FROM {fts_src(index)}"
        )
    }
    fts_name = fts_table(index)

    # 1) términos sampleados → rowids vivos ⊆ view
    terms = _vocab_terms(conn, index)
    if terms:
        stride = max(1, len(terms) // sample_size)
        sampled = terms[::stride][:sample_size]
        orphans: list[int] = []
        for term in sampled:
            expr = '"' + term.replace('"', '""') + '"'
            for (rowid,) in conn.execute(
                f"SELECT rowid FROM {fts_name} WHERE {fts_name} MATCH ?", (expr,)
            ):
                if rowid not in view_ids and rowid not in orphans:
                    orphans.append(int(rowid))
        if orphans:
            raise StorageStateError(
                f"drift FTS en '{index}' (FS-1): {len(orphans)} postings "
                f"huérfanos (rowids en el índice sin doc en docs): "
                f"{orphans[:5]}{' …' if len(orphans) > 5 else ''} — hits "
                f"muertos. Remediación: drop_fts('{index}') y reabrir "
                f"(re-backfill), o restaurar backup."
            )

    # 2) docs sampleados con tokens → postings vivos por rowid
    ids = sorted(view_ids)
    checked = 0
    if ids:
        stride = max(1, len(ids) // sample_size)
        sampled_ids = ids[::stride][:sample_size]
        missing = []
        for rowid in sampled_ids:
            values = conn.execute(
                f"SELECT {', '.join(fields)} FROM {fts_src(index)} WHERE rowid=?",
                (rowid,),
            ).fetchone()
            tokens = tokenize(" ".join(str(v) for v in values if v))
            if not tokens:
                continue  # doc sin tokens → sin postings por diseño
            checked += 1
            found = conn.execute(
                f"SELECT 1 FROM {fts_name} "
                f"WHERE {fts_name} MATCH ? AND rowid=?",
                (match_expr(tokens[0]), rowid),
            ).fetchone()
            if found is None:
                missing.append(view_ids[rowid])
        if missing:
            raise StorageStateError(
                f"drift FTS en '{index}' (FS-1): {len(missing)} docs "
                f"sampleados sin postings: {missing[:5]}"
                f"{' …' if len(missing) > 5 else ''} — writer pre-F1 tocó "
                f"esta BD sin sync. Remediación: drop_fts('{index}') y "
                f"reabrir (re-backfill), o restaurar backup."
            )
    return {"docs": len(view_ids), "checked": checked}


def fts_marker(conn: sqlite3.Connection, index: str) -> dict | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE `index`=? AND key=?", (index, MARKER_KEY)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return {"corrupt": True, "raw": row[0]}


def _fts_object_exists(conn: sqlite3.Connection, index: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name=? AND sql LIKE 'CREATE VIRTUAL TABLE%'",
        (fts_table(index),),
    ).fetchone() is not None


# ── Match expression (sintaxis FTS segura) ─────────────────────────────────

def match_expr(text: str, fields_scoped: list[str] | None = None,
               fts_columns: list[str] | None = None) -> str:
    """`text` → expresión MATCH segura con paridad de tokenización (D2).

    Los tokens vienen de `tokenize()` (SSOT de TokenIndex, models.py): split
    por `-_/` + camelCase, lowercase, sin stopwords, ≥2 chars — así el SET de
    recall FTS refleja el mismo análisis léxico que el backend File. Cada
    token se emite como PREFIX (`"tok"*`): cubre el recall de prefijos del
    bigram-index; el substring INTERNO (bigram fuzzy puro) no se replica —
    FTS5 no soporta infix (desviación documentada, Enmienda 1).

    Identificadores camelCase (fix 2026-11-16): el índice FTS guarda
    "AppProviders" como UN token, mientras `tokenize()` lo parte en
    [app, providers] y el AND de prefijos no lo matchea (medido: FTS crudo 2
    hits, `"app"* "providers"*` 0). Para queries de UNA palabra se agrega el
    token LITERAL en OR con el grupo de prefijos; las multi-término mantienen
    el AND estricto (no se relaja la semántica de búsqueda).

    fields_scoped → column-scoped (`campo:"tok"*`). El texto del usuario
    jamás llega crudo a MATCH (operadores/comillas neutralizados por la
    citación y por tokenize, que solo conserva \\w).
    """
    tokens = tokenize(str(text))
    if not tokens:
        return ""
    terms = [f'"{t}"*' for t in tokens]
    and_terms = " ".join(terms)
    inner = and_terms
    # OR con el literal solo si la palabra se partió (camelCase/snake/dash).
    words = [
        w.strip("\"'()[]{},;:!?")
        for w in re.split(r"\s+", str(text).strip())
        if w.strip("\"'()[]{},;:!?")
    ]
    if len(words) == 1 and words[0].lower() not in tokens:
        # El literal va entre comillas: una comilla interna rompería la
        # sintaxis MATCH (inyección de operadores), así que se elimina.
        literal = words[0].replace('"', "")
        if literal:
            inner = f'("{literal}" OR ({and_terms}))'
    if fields_scoped:
        cols = set(fts_columns or [])
        valid = [f for f in fields_scoped if f in cols]
        if not valid:
            return ""
        body = inner if inner.startswith("(") and inner.endswith(")") else f"({inner})"
        return " ".join(f"{f}:{body}" for f in valid)
    return inner


# ── Ciclo de vida: ensure / backfill / drop ────────────────────────────────

def _sample_verify(
    conn: sqlite3.Connection,
    index: str,
    fields: list[str],
    sample_size: int = SAMPLE_VERIFY_SIZE,
) -> dict:
    """Verificación acotada O(sample) para el boot en estado sano (T-06/M-08).

    Sin scans O(docs+terms): ni view completa ni fts5vocab. Sample
    determinista de docs con tokens (ORDER BY rowid LIMIT) → cada uno DEBE
    responder MATCH por su primer token; un doc que no responde = término
    corrupto / writer sin sync → el caller escala a `reconcile` completo
    (que eleva el error accionable FS-1).

    Returns {"docs": n, "checked": k, "missing": [...]} — `missing` no
    vacío ⇒ sospecha real (el conteo de COUNT es la primera señal, no
    prueba de integridad; postings huérfanos por delete-sin-sync solo los
    ve MATCH/vocab y quedan fuera del path barato — documentado).
    """
    n_docs, _ = counts(conn, index)
    missing: list[str] = []
    checked = 0
    for rowid, doc_id in conn.execute(
        f"SELECT rowid, id FROM {fts_src(index)} ORDER BY rowid LIMIT ?",
        (sample_size,),
    ):
        values = conn.execute(
            f"SELECT {', '.join(fields)} FROM {fts_src(index)} WHERE rowid=?",
            (rowid,),
        ).fetchone()
        tokens = tokenize(" ".join(str(v) for v in values if v))
        if not tokens:
            continue  # doc sin tokens → sin postings por diseño
        checked += 1
        found = conn.execute(
            f"SELECT 1 FROM {fts_table(index)} "
            f"WHERE {fts_table(index)} MATCH ? AND rowid=?",
            (match_expr(tokens[0]), rowid),
        ).fetchone()
        if found is None:
            missing.append(doc_id)
    return {"docs": n_docs, "checked": checked, "missing": missing[:5]}


def ensure_fts(
    conn: sqlite3.Connection,
    index: str,
    fields: list[str],
    batch: int = FTS_BATCH,
) -> dict:
    """Crea+backfill (una transacción, FS-2) o verifica (M-04) el FTS del índice.

    Returns:
        {"status": "created"|"verified", "docs": n, ...}

    Raises:
        StorageStateError: tabla sin marker (FS-2), schema cambió (marker
            ≠ fields), o drift de conteos (FS-1) — siempre accionable.
    """
    _validate(index, fields)
    fields = list(fields)
    if not _fts5_available(conn):
        raise StorageStateError(
            "sqlite sin FTS5 (compileoption ENABLE_FTS5 ausente) — F1 "
            "(ADR-WALKER-075 D2) requiere FTS5 nativo. Verificar el build "
            "de Python/sqlite."
        )

    marker = fts_marker(conn, index)
    table_exists = _fts_object_exists(conn, index)

    if table_exists and marker is None:
        raise StorageStateError(
            f"{fts_table(index)} existe sin marker '{MARKER_KEY}' en meta — "
            f"migración a medias (FS-2). Remediación: drop_fts('{index}') y "
            "reabrir (re-backfill), o restaurar backup."
        )

    if marker is not None and not table_exists:
        raise StorageStateError(
            f"marker '{MARKER_KEY}' de '{index}' sin tabla {fts_table(index)} "
            f"— estado inconsistente (FS-2). Remediación: borrar la fila "
            f"meta ('{index}', '{MARKER_KEY}') y reabrir."
        )

    if marker is not None:
        cfg = marker
        if cfg.get("fields") != fields:
            raise StorageStateError(
                f"schema fulltext de '{index}' cambió: FTS indexa "
                f"{cfg.get('fields')} pero IndexSchema declara {fields} — "
                f"hits parciales si se sigue usando (FS-1). Remediación: "
                f"drop_fts('{index}') y reabrir (re-backfill con el schema nuevo)."
            )
        # T-06 (M-08): verificación BARATA por defecto — COUNT docs↔fts +
        # sample-verify acotado; reconcile completo (O(docs+terms)) SOLO si
        # hay sospecha (conteo roto o doc sampleado sin postings).
        n_docs, n_fts = counts(conn, index)
        if n_docs != n_fts:
            # primera señal de drift (writer pre-F1 / sync roto) → full
            report = reconcile(conn, index, fields)  # eleva FS-1 si drift real
            return {"status": "verified", **report,
                    "fields": fields, "created_at": cfg.get("created_at")}
        sample = _sample_verify(conn, index, fields)
        if sample["missing"]:
            # término corrupto sin tocar conteos → escalar (FS-1 accionable)
            report = reconcile(conn, index, fields)
            return {"status": "verified", **report,
                    "fields": fields, "created_at": cfg.get("created_at")}
        return {"status": "verified", "docs": n_docs,
                "checked": sample["checked"],
                "fields": fields, "created_at": cfg.get("created_at")}

    # create + backfill + marker — UNA transacción (FS-2: DDL transaccional)
    src, fts_name = fts_src(index), fts_table(index)
    cols = ", ".join(
        f"COALESCE(json_extract(data, '$.\"{f}\"'), '') AS {f}" for f in fields
    )
    fts_cols = ", ".join(fields)
    with conn:
        conn.execute(f"DROP VIEW IF EXISTS {src}")
        conn.execute(
            f"CREATE VIEW {src} AS "
            f"SELECT rowid AS rowid, id AS id, {cols} "
            f"FROM docs WHERE `index` = '{index}'"
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE {fts_name} USING fts5("
            f"id UNINDEXED, {fts_cols}, "
            f"content='{src}', content_rowid='rowid')"
        )
        n_backfilled = _backfill(conn, index, fields, batch)
        n_docs, n_fts = counts(conn, index)
        if n_docs != n_fts or n_backfilled != n_fts:
            raise StorageStateError(
                f"backfill FTS de '{index}' inconsistente: docs={n_docs} vs "
                f"fts={n_fts} (backfilled={n_backfilled}) — M-04 violado; "
                "rollback completo (una transacción)."
            )
        # M-04 real: postings verificados por sample (no el COUNT delegado)
        reconcile(conn, index, fields)
        conn.execute(
            "INSERT OR REPLACE INTO meta (`index`, key, value) VALUES (?,?,?)",
            (index, MARKER_KEY, json.dumps({
                "version": 1,
                "fields": fields,
                "docs_at_backfill": n_docs,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })),
        )
    return {"status": "created", "docs": n_docs, "fields": fields}


def _backfill(
    conn: sqlite3.Connection, index: str, fields: list[str], batch: int
) -> int:
    """Backfill por lotes (§8 `fts_backfill_batch`) dentro de la transacción del caller.

    Lee la view (json_extract de docs) por rangos de rowid y ejecuta
    executemany por pase. Returns total insertado.
    """
    fts_name, src = fts_table(index), fts_src(index)
    cols = ["rowid", "id"] + list(fields)
    cols_sql = ", ".join(cols)
    phs = ", ".join("?" * len(cols))
    last_rowid = -1
    total = 0
    while True:
        rows = conn.execute(
            f"SELECT {cols_sql} FROM {src} "
            f"WHERE rowid > ? ORDER BY rowid LIMIT {int(batch)}",
            (last_rowid,),
        ).fetchall()
        if not rows:
            break
        conn.executemany(
            f"INSERT INTO {fts_name}({cols_sql}) VALUES ({phs})",
            [tuple(r) for r in rows],
        )
        last_rowid = rows[-1][0]
        total += len(rows)
    return total


def drop_fts(conn: sqlite3.Connection, index: str) -> None:
    """Remediación explícita (FS-1/FS-2): DROP tabla+view+marker.

    Nunca borrado silencioso de DATOS (los docs quedan intactos — solo se
    elimina el índice derivable FTS, que se reconstruye al reabrir).
    """
    _validate(index, [])
    with conn:
        conn.execute(f"DROP TABLE IF EXISTS {fts_table(index)}")
        conn.execute(f"DROP VIEW IF EXISTS {fts_src(index)}")
        conn.execute(
            "DELETE FROM meta WHERE `index`=? AND key=?", (index, MARKER_KEY)
        )


# ── Sync transaccional (M-02: MISMA transacción del _save) ─────────────────

def fetch_view_rows(
    conn: sqlite3.Connection,
    index: str,
    fields: list[str],
    doc_ids: list[str],
    chunk: int = 1000,
) -> dict[str, tuple]:
    """Valores (rowid, id, f1..fn) de la view para los ids dados (chunked IN).

    Los valores VIEJOS se capturan ANTES de escribir docs (el 'delete' de
    external content exige exactamente los valores indexados); los NUEVOS,
    después de escribirlos.
    """
    _validate(index, fields)
    out: dict[str, tuple] = {}
    ids = list(doc_ids)
    fts_cols = ["rowid", "id"] + list(fields)
    cols_sql = ", ".join(fts_cols)
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        in_phs = ", ".join("?" * len(part))
        for row in conn.execute(
            f"SELECT {cols_sql} FROM {fts_src(index)} WHERE id IN ({in_phs})",
            [*part],
        ):
            out[row[1]] = tuple(row)
    return out


def apply_delete(
    cur: sqlite3.Cursor, index: str, fields: list[str], row: tuple
) -> None:
    """Comando 'delete' de external content con los valores indexados originales.

    `('delete', ...)` es un LITERAL en la primera columna (la oculta del
    propio fts); los placeholders cubren solo las columnas de datos.
    """
    _validate(index, fields)
    cols = ["rowid", "id"] + list(fields)
    phs = ", ".join("?" * len(cols))
    cur.execute(
        f"INSERT INTO {fts_table(index)}({fts_table(index)}, {', '.join(cols)}) "
        f"VALUES ('delete', {phs})",
        [*row],
    )


def apply_insert(
    cur: sqlite3.Cursor, index: str, fields: list[str], rows: list[tuple]
) -> None:
    """Inserta las filas nuevas (rowid = docs.rowid — vínculo D2)."""
    _validate(index, fields)
    if not rows:
        return
    cols = ["rowid", "id"] + list(fields)
    phs = ", ".join("?" * len(cols))
    cur.executemany(
        f"INSERT INTO {fts_table(index)}({', '.join(cols)}) VALUES ({phs})",
        rows,
    )


# ── Búsqueda (M-01: MATCH nativo, C-speed) ──────────────────────────────────

def search_fts(
    conn: sqlite3.Connection,
    index: str,
    text: str,
    max_results: int = 50,
    fields_scoped: list[str] | None = None,
    offset: int = 0,
) -> tuple[list[tuple[str, float]], int]:
    """MATCH nativo con orden bm25. Returns ([(doc_id, bm25_rank)], total).

    `total` = TODOS los matches (G8: paginación real), independiente del
    LIMIT de la página. `bm25` es COSTO (menor = mejor). `fields_scoped` →
    column-scoped MATCH (solo columnas FTS del índice).
    """
    _validate(index, [])
    marker = fts_marker(conn, index)
    cols = marker.get("fields", []) if marker else []
    match = match_expr(text, fields_scoped, cols)
    if not match:
        return [], 0
    fts_name = fts_table(index)
    total = int(conn.execute(
        f"SELECT COUNT(*) FROM {fts_name} WHERE {fts_name} MATCH ?",
        (match,),
    ).fetchone()[0])
    rows = conn.execute(
        f"SELECT d.id, bm25({fts_name}) AS rank "
        f"FROM {fts_name} f JOIN docs d ON d.rowid = f.rowid "
        f"WHERE {fts_name} MATCH ? ORDER BY rank LIMIT ? OFFSET ?",
        (match, int(max_results), int(offset)),
    ).fetchall()
    return [(r[0], r[1]) for r in rows], total
