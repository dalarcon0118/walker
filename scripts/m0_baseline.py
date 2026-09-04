"""M0 — Baseline obligatorio de storage (§9 ADR-WALKER-075, cierra G5).

Regla del ADR: NINGÚN umbral (M-03, M-05, FS-5, compaction_trigger) se ACTIVA
sin baseline medido antes — umbral sin baseline = opinión. Este script mide el
protocolo §9 sobre (a) una COPIA REAL de la BD canónica y (b) un fixture
sintético determinista de 16K docs shape cpg real, y registra el baseline en
`docs/baselines/storage-v2.md` ANTES de tocar código (precondición de F1+).

Métricas §9 (tabla completa en el ADR):
  1. save() p50/p95/p99 — timer alrededor de `SqliteIndex._save` con 3 casos:
     1 doc nuevo / 1 edge nueva / 0 cambios
  2. load completo (arranque) — timer de `_do_load` sobre BD con 16K docs
  3. fulltext recall — timer de `search {"text": ...}` con índice poblado
     (estado post 6e96f58, pre-FTS5)
  4. query selectiva — timer de `search {"type": "function"}` materializado
  5. WAL growth — bytes de `-wal` tras 1 build completo (replace_blob 16K)

Precondición (H0 verde — §10.0): la copia real pasa H-1 (integrity ok),
H-3 (0 edges huérfanos), H-6 (cpg/ckg vacíos — derivables dropeados por el
gate) y delta/trace SSOT presentes. Si algo falla → exit(1) fail-fast:
NUNCA medir sobre una BD sucia (el baseline heredaría la suciedad — FS-9).

Nota H-6: tras H0 (commit 54f019e) la canónica tiene cpg/ckg VACÍOS (los
14376 docs derivables fueron dropeados; se reconstruirán post-migración con
build_code_index/extract_knowledge). Por eso la forma "16K docs" proviene del
fixture determinista (shape cpg real) — exactamente lo que prescribe §8/§9.

Uso:
    python3 scripts/m0_baseline.py            # mide y ESCRIBE el baseline
    python3 scripts/m0_baseline.py --dry      # mide y NO escribe archivos
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
import tempfile
import time

REPO = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from tests.benchmarks.cpg_fixture import (
    CPG_SCHEMA_CONFIG,
    build_cpg_fixture,
    sample_search_terms,
)

CANONICAL_DB = os.path.realpath(
    os.path.join(REPO, ".cognitive", "projects", "walker", "data", "cognitive.sqlite3")
)
OUT_MD = os.path.join(REPO, "docs", "baselines", "storage-v2.md")
OUT_JSON = os.path.join(REPO, "docs", "baselines", "storage-v2.json")

N_SAVE_DOC = 100   # iteraciones "1 doc nuevo"
N_SAVE_EDGE = 100  # iteraciones "1 edge nueva"
N_SAVE_NOOP = 50   # iteraciones "0 cambios"
N_LOAD_RUNS = 3
N_SEARCH = 100

DERIVABLES = ("cpg", "ckg")  # H-6: dropeados por el gate; delta/trace SSOT

# Schemas de PRODUCCIÓN (SSOT de cada fuente) para registrar en la copia real
# los índices que el catálogo __indexes__ no restauró (ver HALLAZGO M0-1 en
# el baseline: catálogo incompleto — solo ckg/config persistido).
PRODUCTION_INDEX_SCHEMAS: dict[str, dict] = {
    # fast_mcp/composition.py:385 (composition root, _build_delta_repo)
    "delta": {
        "fields": {
            "task": "spec_id",
            "spec": "spec_id",
            "spring": "spring_id",
            "domain": "domain",
            "type": "type",
        },
        "types": {"spec": {}, "spring": {}},
        "description": "DeltaGraph: specs and springs",
        "search": {
            "type": "fulltext",
            "fields": ["objective", "title", "description", "domain"],
        },
    },
    # fast_mcp/composition.py:334 (trace runner)
    "trace": {
        "fields": {
            "event_type": "keyword",
            "source": "keyword",
            "task_id": "keyword",
            "sprint_id": "keyword",
            "node": "keyword",
            "tool": "keyword",
        },
    },
    # fast_mcp/features/code_analysis/schema.py (register_cpg_index)
    "cpg": CPG_SCHEMA_CONFIG,
    # fast_mcp/features/cognitive_knowledge/infrastructure/cognitive_ckg_repo.py:80
    "ckg": {
        "fields": {
            "type": "type", "scope": "scope",
            "label": "statement", "description": "rationale",
            "tag": "tags", "task": "metadata.task_id",
        },
        "types": {
            "decision": {}, "refactor": {}, "convention": {},
            "pattern": {}, "rule": {}, "component": {},
        },
        "search": {"type": "fulltext", "fields": ["statement", "rationale"]},
    },
}


def pct(samples: list[float], p: float) -> float:
    """Percentil sobre muestra ordenada (p en [0, 100])."""
    s = sorted(samples)
    k = min(len(s) - 1, max(0, int(round(len(s) * p / 100.0)) - 1))
    return s[k]


def now_ms() -> float:
    return time.perf_counter_ns() / 1_000_000.0


# ── Copia real: snapshot consistente vía API de backup (read-only origen) ──

def snapshot_real_db(dest_path: str) -> None:
    src = sqlite3.connect(f"file:{CANONICAL_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(dest_path)
    src.backup(dst)
    dst.close()
    src.close()


def precondition_on_copy(db_path: str) -> tuple[bool, dict]:
    """H0 verde sobre la copia: H-1 ok, H-3 cero huérfanos, H-6 derivables
    vacíos, delta/trace SSOT presentes (conteos > 0)."""
    c = sqlite3.connect(db_path)
    integrity = [r[0] for r in c.execute("PRAGMA integrity_check")]
    orphans = c.execute(
        """SELECT COUNT(*) FROM edges e
           WHERE NOT EXISTS (SELECT 1 FROM docs d
                 WHERE d.`index`=e.`index` AND d.id=e.src)
              OR NOT EXISTS (SELECT 1 FROM docs d
                 WHERE d.`index`=e.`index` AND d.id=e.dst)"""
    ).fetchone()[0]
    counts = dict(c.execute("SELECT `index`, COUNT(*) FROM docs GROUP BY `index`"))
    edges = dict(c.execute("SELECT `index`, COUNT(*) FROM edges GROUP BY `index`"))
    c.close()
    ok = (
        integrity == ["ok"]
        and orphans == 0
        and counts.get("cpg", 0) == 0
        and counts.get("ckg", 0) == 0
        and counts.get("delta", 0) > 0
        and counts.get("trace", 0) > 0
    )
    return ok, {
        "integrity": integrity,
        "orphan_edges": orphans,
        "docs_by_index": counts,
        "edges_by_index": edges,
    }


# ── Fixture 16K: build completo (WAL growth) + reopen (load completo) ──────

def build_fixture(work_dir: str) -> dict:
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{work_dir}")
    backend = resolve_storage_backend(conn)
    backend.register_index("cpg", CPG_SCHEMA_CONFIG)
    docs, edges = build_cpg_fixture()
    t0 = time.perf_counter_ns()
    result = backend.replace_blob("cpg", docs, edges, {"source": "m0-fixture", "seed": 42})
    build_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
    db_path = backend._db_path
    wal_bytes = os.path.getsize(db_path + "-wal") if os.path.exists(db_path + "-wal") else 0
    backend._db.close()
    return {
        "db_dir": work_dir,
        "db_path": db_path,
        "build_ms": build_ms,
        "wal_bytes": wal_bytes,
        "result": result,
        "n_docs": len(docs),
        "n_edges": len(edges),
    }


def reopen_index(db_dir: str, name: str):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{db_dir}")
    backend = resolve_storage_backend(conn)
    idx = backend._indexes[name]
    return backend, idx


# ── Métricas ────────────────────────────────────────────────────────────────

def measure_load(fixture: dict) -> list[float]:
    """Métrica 2: timer de _do_load sobre BD con 16K docs (arranque)."""
    runs = []
    for i in range(N_LOAD_RUNS):
        backend, idx = reopen_index(fixture["db_dir"], "cpg")
        t0 = time.perf_counter_ns()
        idx._load()
        runs.append((time.perf_counter_ns() - t0) / 1_000_000.0)
        assert len(idx._docs) == fixture["n_docs"], "load incompleto"
        backend._db.close()
    return runs


def measure_save(idx) -> dict[str, list[float]]:
    """Métrica 1: timer alrededor de _save (3 casos §9).

    Caso doc: mutación en-memoria (docs+grafo) SIN persistir, luego _save
    cronometrado — mide EXACTAMENTE el costo del delta transaccional.
    """
    samples = {"doc_nuevo": [], "edge_nueva": [], "sin_cambios": []}
    for i in range(N_SAVE_DOC):
        doc_id = f"cpg:function:bench_doc_{i:04d}"
        doc = {
            "name": f"bench_doc_{i}", "type": "function",
            "path": f"src/bench/doc_{i}.py", "line": 1 + i % 500,
            "signature": f"def bench_doc_{i}(a: int) -> bool:", "kind": "def",
        }
        idx._docs[doc_id] = doc
        idx._graph.add_node(doc_id, **doc)
        t0 = time.perf_counter_ns()
        idx._save()
        samples["doc_nuevo"].append((time.perf_counter_ns() - t0) / 1_000_000.0)

    fn_ids = [d for d in list(idx._docs) if ":function:" in d and d.startswith("cpg:f")]
    for i in range(N_SAVE_EDGE):
        u = fn_ids[i % len(fn_ids)]
        v = fn_ids[(i * 7 + 13) % len(fn_ids)]
        if u == v:
            v = fn_ids[(i * 7 + 13 + 1) % len(fn_ids)]
        idx._graph.add_edge(u, v, rel="calls")
        t0 = time.perf_counter_ns()
        idx._save()
        samples["edge_nueva"].append((time.perf_counter_ns() - t0) / 1_000_000.0)

    for _ in range(N_SAVE_NOOP):
        t0 = time.perf_counter_ns()
        idx._save()
        samples["sin_cambios"].append((time.perf_counter_ns() - t0) / 1_000_000.0)
    return samples


def measure_search(idx, terms: list[str]) -> dict[str, dict]:
    """Métricas 3 y 4: fulltext recall + query selectiva materializada."""
    fulltext_ms, fulltext_hits = [], 0
    for term in terms:
        t0 = time.perf_counter_ns()
        res = idx.search({"text": term})
        fulltext_ms.append((time.perf_counter_ns() - t0) / 1_000_000.0)
        if res.get("hits", {}).get("total", 0) > 0:
            fulltext_hits += 1

    selective_ms, sel_total = [], 0
    for _ in range(N_SEARCH):
        t0 = time.perf_counter_ns()
        res = idx.search({"type": "function"})
        selective_ms.append((time.perf_counter_ns() - t0) / 1_000_000.0)
        sel_total = res.get("hits", {}).get("total", 0)
    assert sel_total > 0, "query selectiva sin hits — índice sordo"
    return {
        "fulltext": {
            "samples_ms": fulltext_ms,
            "hit_rate": f"{fulltext_hits}/{len(terms)}",
        },
        "selective": {"samples_ms": selective_ms, "total_hits": sel_total},
    }


def measure_real_copy_load(snapshot_path: str, docs_by_index: dict) -> dict:
    """Load de la COPIA REAL (arranque §9) por índice con datos.

    Comportamiento de arranque fiel: el backend restaura lo que el catálogo
    `__indexes__` tenga (hoy SOLO ckg — HALLAZGO M0-1) y el composition root
    re-registra el resto con el schema de producción. Se cronometra `_load`
    de cada índice que tenga docs en el snapshot (delta/trace SSOT hoy).
    """
    assert os.path.exists(snapshot_path), f"snapshot no existe: {snapshot_path}"
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{snapshot_path}")
    backend = resolve_storage_backend(conn)
    for name, schema in PRODUCTION_INDEX_SCHEMAS.items():
        if name not in backend.list_indexes():
            backend.register_index(name, schema)
    per_index = {}
    for name in sorted(backend.list_indexes()):
        if docs_by_index.get(name, 0) == 0:
            per_index[name] = {"load_ms": 0.0, "docs": 0}
            continue
        idx = backend._indexes[name]
        t0 = time.perf_counter_ns()
        idx._load()
        per_index[name] = {
            "load_ms": (time.perf_counter_ns() - t0) / 1_000_000.0,
            "docs": len(idx._docs),
        }
    backend._db.close()
    data_loaded = {k: v for k, v in per_index.items() if v["docs"] > 0}
    assert data_loaded, "copia real sin índices con datos cargados — snapshot inválido"
    return per_index


def inspect_catalog(snapshot_path: str) -> None:
    """Diagnóstico del catálogo (HALLAZGO M0-1): qué hay en __indexes__ vs datos.

    Además: procesos con la BD canónica ABIERTA (H-7/rollout §10.1 — un
    proceso vivo con estado en-memoria pre-H0 puede re-materializar la
    suciedad purgada y desalinear el catálogo).
    """
    c = sqlite3.connect(snapshot_path)
    print("[inspect] raw meta.__indexes__ (key → value[:100]):")
    for key, value in c.execute(
        "SELECT key, value FROM meta WHERE `index`='__indexes__'"
    ):
        print(f"    {key!r} → {str(value)[:100]!r}")
    catalog = sorted(
        key[: -len("/config")]
        for (key,) in c.execute("SELECT key FROM meta WHERE `index`='__indexes__'")
        if key.endswith("/config")
    )
    docs_idx = sorted(r[0] for r in c.execute("SELECT DISTINCT `index` FROM docs"))
    hashes = sorted(r[0] for r in c.execute(
        "SELECT DISTINCT `index` FROM meta WHERE key='schema_hash'"))
    counts = dict(c.execute("SELECT `index`, COUNT(*) FROM docs GROUP BY `index`"))
    print(f"[inspect] catálogo __indexes__: {catalog}")
    print(f"[inspect] índices con docs:    {docs_idx}")
    print(f"[inspect] schema_hash rows:    {hashes}")
    print(f"[inspect] docs por índice:     {counts}")
    print(f"[inspect] catálogo sin docs:   {sorted(set(catalog) - set(docs_idx))}")
    print(f"[inspect] docs sin catálogo:   {sorted(set(docs_idx) - set(catalog))}")
    c.close()

    import subprocess
    out = subprocess.run(["lsof", "-t", CANONICAL_DB], capture_output=True, text=True)
    pids = [p for p in out.stdout.split() if p]
    print(f"[inspect] procesos con la canónica ABIERTA: {pids or 'ninguno'}")
    st = os.stat(CANONICAL_DB)
    print(f"[inspect] canónica: {st.st_size:,} bytes, mtime={time.strftime('%H:%M:%S', time.localtime(st.st_mtime))}")


def pstats(samples: list[float]) -> dict[str, float]:
    return {"p50": round(pct(samples, 50), 3), "p95": round(pct(samples, 95), 3),
            "p99": round(pct(samples, 99), 3), "n": len(samples)}


def main() -> int:
    dry = "--dry" in sys.argv
    assert os.path.exists(CANONICAL_DB), f"BD canónica no existe: {CANONICAL_DB}"

    work = tempfile.mkdtemp(prefix="m0-baseline-")
    print(f"[M0] workspace: {work}")

    # 0) Precondición H0 (§10.0) sobre snapshot de la canónica
    snap_path = os.path.join(work, "canonical-copy.sqlite3")
    snapshot_real_db(snap_path)
    ok, evid = precondition_on_copy(snap_path)
    print(f"[M0] H0 precondición sobre copia real: {'✅ VERDE' if ok else '❌ FAIL'}")
    print(f"     integrity={evid['integrity']} orphans={evid['orphan_edges']} "
          f"docs={evid['docs_by_index']}")
    if "--inspect" in sys.argv:
        inspect_catalog(snap_path)
        return 0
    if not ok:
        print("[M0] GATE FAIL — no se mide baseline sobre BD sucia (FS-9). Abort.")
        return 1

    # 1) Fixture 16K + WAL growth (métrica 5)
    fixture = build_fixture(work)
    print(f"[M0] fixture 16K construido en {fixture['build_ms']:.0f} ms — "
          f"WAL tras build: {fixture['wal_bytes']} bytes")

    # 2) load completo (métrica 2)
    load_runs = measure_load(fixture)
    print(f"[M0] load 16K docs (3 corridas): "
          f"{[round(r, 1) for r in load_runs]} ms")

    # 3) save() p50/p95/p99 (métrica 1) sobre el índice poblado cargado
    _, idx = reopen_index(fixture["db_dir"], "cpg")
    idx._load()
    save_samples = measure_save(idx)
    for case, s in save_samples.items():
        print(f"[M0] save {case}: p50={pct(s,50):.2f} p95={pct(s,95):.2f} "
              f"p99={pct(s,99):.2f} ms (n={len(s)})")

    # 4) búsquedas (métricas 3 y 4) — términos que SÍ tienen hits (nombres reales)
    terms = sample_search_terms(dict(idx._docs))
    res = measure_search(idx, terms)
    ft = pstats(res["fulltext"]["samples_ms"])
    sel = pstats(res["selective"]["samples_ms"])
    print(f"[M0] fulltext recall: p50={ft['p50']:.2f} p95={ft['p95']:.2f} "
          f"p99={ft['p99']:.2f} ms (hits {res['fulltext']['hit_rate']})")
    print(f"[M0] query selectiva: p50={sel['p50']:.2f} p95={sel['p95']:.2f} "
          f"p99={sel['p99']:.2f} ms (total {res['selective']['total_hits']} hits)")

    # 5) load de la copia REAL por índice (arranque fiel: restaurar catálogo
    #    + re-registrar schemas de producción; delta/trace SSOT)
    real_loads = measure_real_copy_load(snap_path, evid["docs_by_index"])
    print(f"[M0] load copia real: "
          f"{ {k: (round(v['load_ms'],1), v['docs']) for k, v in real_loads.items()} }")

    idx._db.close()  # cierra la conexión del backend del fixture

    results = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "canonical": {
            "db": CANONICAL_DB,
            "size_bytes": os.path.getsize(CANONICAL_DB),
            "h0_precondition": evid,
        },
        "fixture": {
            "n_docs": fixture["n_docs"], "n_edges": fixture["n_edges"],
            "build_ms": round(fixture["build_ms"], 1),
            "seed": 42,
        },
        "metrics": {
            "save_doc_nuevo_ms": pstats(save_samples["doc_nuevo"]),
            "save_edge_nueva_ms": pstats(save_samples["edge_nueva"]),
            "save_sin_cambios_ms": pstats(save_samples["sin_cambios"]),
            "load_16k_ms": {"runs": [round(r, 1) for r in load_runs],
                            "min": round(min(load_runs), 1)},
            "wal_after_build_bytes": fixture["wal_bytes"],
            "fulltext_recall_ms": {**ft, "hit_rate": res["fulltext"]["hit_rate"]},
            "selective_query_ms": {**sel, "total_hits": res["selective"]["total_hits"]},
            "real_copy_load_ms": {
                k: {"load_ms": round(v["load_ms"], 1), "docs": v["docs"]}
                for k, v in real_loads.items()
            },
        },
        "raw": {
            "save_doc_nuevo": save_samples["doc_nuevo"],
            "save_edge_nueva": save_samples["edge_nueva"],
            "save_sin_cambios": save_samples["sin_cambios"],
            "fulltext": res["fulltext"]["samples_ms"],
            "selective": res["selective"]["samples_ms"],
            "load_16k": load_runs,
        },
    }

    if dry:
        print("[M0] --dry: baseline NO escrito")
        return 0

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render_md(results))
    print(f"[M0] baseline escrito: {OUT_MD}\n     raw: {OUT_JSON}")
    return 0


def render_md(r: dict) -> str:
    m = r["metrics"]
    h0 = r["canonical"]["h0_precondition"]
    rl = ", ".join(
        f"{k}: {v['load_ms']} ms / {v['docs']} docs" for k, v in m["real_copy_load_ms"].items()
    )
    return f"""# Baseline storage v2 — M0 (ADR-WALKER-075 §9, cierra G5 — Enmienda 1)

**Regla §9:** ningún umbral de este ADR (M-03, M-05, FS-5, `compaction_trigger`, p99)
se ACTIVA sin baseline medido antes — umbral sin baseline = opinión.
Recalibración: `umbral = max(baseline_p99 × 2, valor propuesto)`; si el baseline
ya cumple el umbral propuesto, el umbral pasa a ser de no-regresión.
M0 se ejecuta UNA vez; re-ejecutar solo si cambia el formato de payload (v3).

- Fecha: {r['date']}
- Entorno: Python {r['env']['python']} / sqlite {r['env']['sqlite']} / {r['env']['machine']}
- Canónica: `{r['canonical']['db']}` ({r['canonical']['size_bytes']:,} bytes)
- Fixture: {r['fixture']['n_docs']:,} docs + {r['fixture']['n_edges']:,} edges deterministas
  (seed {r['fixture']['seed']}, shape cpg real — `tests/benchmarks/cpg_fixture.py`),
  build {r['fixture']['build_ms']} ms.

## Precondición H0 (§10.0 — gate verde, commit 54f019e)

| Check | Evidencia |
|---|---|
| H-1 integrity | `{h0['integrity'][0]}` |
| H-3 huérfanos | {h0['orphan_edges']} |
| H-6 derivables | cpg={h0['docs_by_index'].get('cpg', 0)}, ckg={h0['docs_by_index'].get('ckg', 0)} (dropeados — rebuild post-migración) |
| SSOT delta/trace | delta={h0['docs_by_index'].get('delta', 0)} docs, trace={h0['docs_by_index'].get('trace', 0)} docs — intactos |

## Métricas (protocolo §9)

| Métrica | p50 | p95 | p99 | n | Detalle |
|---|---|---|---|---|---|
| `save()` 1 doc nuevo | {m['save_doc_nuevo_ms']['p50']} ms | {m['save_doc_nuevo_ms']['p95']} ms | {m['save_doc_nuevo_ms']['p99']} ms | {m['save_doc_nuevo_ms']['n']} | timer alrededor de `_save` |
| `save()` 1 edge nueva | {m['save_edge_nueva_ms']['p50']} ms | {m['save_edge_nueva_ms']['p95']} ms | {m['save_edge_nueva_ms']['p99']} ms | {m['save_edge_nueva_ms']['n']} | idem |
| `save()` 0 cambios | {m['save_sin_cambios_ms']['p50']} ms | {m['save_sin_cambios_ms']['p95']} ms | {m['save_sin_cambios_ms']['p99']} ms | {m['save_sin_cambios_ms']['n']} | diff-scan completo (costo O(docs+edges)) |
| `load` completo (16K docs) | — | — | {m['load_16k_ms']['min']} ms (min de {len(m['load_16k_ms']['runs'])}) | {len(m['load_16k_ms']['runs'])} | `_do_load`: corridas {m['load_16k_ms']['runs']} |
| fulltext recall `{{"text": ...}}` | {m['fulltext_recall_ms']['p50']} ms | {m['fulltext_recall_ms']['p95']} ms | {m['fulltext_recall_ms']['p99']} ms | {m['fulltext_recall_ms']['n']} | hits {m['fulltext_recall_ms']['hit_rate']} — post 6e96f58, pre-FTS5 |
| query selectiva `{{"type": "function"}}` | {m['selective_query_ms']['p50']} ms | {m['selective_query_ms']['p95']} ms | {m['selective_query_ms']['p99']} ms | {m['selective_query_ms']['n']} | índice materializado, {m['selective_query_ms']['total_hits']} hits |
| WAL growth tras 1 build | — | — | — | — | {m['wal_after_build_bytes']:,} bytes (replace_blob 16K+24K) |

Load de la COPIA REAL (SSOT post-H0): {rl}.

## Hallazgos M0 (de la medición, no de la interpretación)

**M0-1 — Catálogo `__indexes__` incompleto en la canónica.** El snapshot real
tiene `__indexes__` = `['ckg']` únicamente: `delta` (8 docs) y `trace` (872 docs)
— SSOT de dominio con datos y `schema_hash` — NO tienen config persistido en el
catálogo (y `cpg` perdió el config con el drop H-6). El runtime lo auto-repara
porque el composition root re-registra delta/trace en cada arranque
(`register_index` → `INSERT OR REPLACE` del config). **Acción para la cadena
v3 (§10.1):** tras la migración, verificar en el primer boot que los configs
delta/trace quedan persistidos en `__indexes__` — H-4 estricto (catálogo
consistente) debe salir verde POST-migración también.

**M0-2 — El costo dominante de `_save` es el diff-scan O(docs+edges).** Los 3
casos de `save()` (1 doc nuevo / 1 edge nueva / 0 cambios) cuestan lo mismo
(~110 ms p50): `_save` re-serializa (canonical dumps) TODOS los docs y edges
del índice en cada llamada (~40K dumps con 16K docs + 24K edges). El costo
marginal del write real (1 INSERT) es ruido frente al scan. Este es el
objetivo natural de M-03: umbral de no-regresión = p99 × 2 (~290 ms); toda
optimización del diff (hash incremental, dirty-set) se mide contra este
baseline.

## Notas de interpretación

- El caso `save() 0 cambios` mide el costo del **diff-scan**: `_save` re-serializa
  (canonical dumps) TODOS los docs y edges en cada llamada — es el piso del costo
  por write y el objetivo natural de M-03 (no-regresión p99 × 2).
- `load` completo incluye JSON parse + sha1 del raw + grafo nx + rebuild de
  índices secundarios y token index (fix 6e96f58: FTS sordo tras restart).
- El fixture es determinista (seed 42): cualquier cambio de estos números entre
  corridas es ruido de máquina o cambio de código, nunca de datos.
- Raw samples: `docs/baselines/storage-v2.json` (reproducibilidad / CI nocturno).
"""


if __name__ == "__main__":
    sys.exit(main())
