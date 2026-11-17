"""Verificación post-migración de la canónica v3 (ADR-WALKER-075 §10.1).

1. Re-boot idempotente (v3 marcado, sin re-migración, FTS reconcile limpio).
2. Fulltext delta (F1): search sobre término real → hits SSOT.
3. Structured delta (F2/M-07): filtro por type → router SQL, SIN
   materializar el read model (M-09).
4. Trace quirk pre-existente (event_type → 'keyword'): fallback seguro al
   path base (0 hits, sin resultados inventados — M-08 paridad).
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA_DIR = os.environ.get(
    "WALKER_DATA_DIR",
    os.path.join(ROOT, ".cognitive", "projects", "walker", "data"),
)

from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph  # noqa: E402

# 1) Idempotencia: 2ª apertura limpia
g1 = build_cognitive_graph(chain_dir=DATA_DIR)
b1 = g1._backend  # noqa: SLF001
sv = b1._db.execute(  # noqa: SLF001
    "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
).fetchone()
# T-03 (M-7): la rama FAIL es exit-code real — un CI no puede dar verde
# ante una BD que no quedó marcada v3 (FS-2).
if not (sv and sv[0] == "v3"):
    print(f"[boot2] FAIL schema={sv[0] if sv else None} — BD sin marcar v3; "
          "la migración no completó (FS-2). Remediación: revisar los logs "
          "del boot y restaurar el backup v2 si procede.", file=sys.stderr)
    sys.exit(1)
print(f"[boot2] schema={sv[0]} — sin re-migración (idempotente) ✓")

# FTS parity check after reopen (FS-1: drift → StorageStateError). T-03
# (M-7): el loop es fail-loud — marker ausente (FS-2) o paridad rota
# (reconcile docs != docs tabla) = exit ≠ 0 con remediación accionable.
from fast_mcp.shared_kernel.cognitive_graph import fts as fts_mod  # noqa: E402
for name in ("delta", "cpg", "ckg"):
    marker = fts_mod.fts_marker(b1._db, name)  # noqa: SLF001
    assert marker is not None, (
        f"[fts-reconcile] {name}: marker FTS ausente (FS-2) — remediación: "
        f"drop_fts('{name}') y reabrir (re-backfill), o restaurar backup"
    )
    rep = fts_mod.reconcile(b1._db, name, marker["fields"])  # noqa: SLF001
    docs_in_idx = b1._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM docs WHERE `index`=?", (name,)
    ).fetchone()[0]
    assert rep["docs"] == docs_in_idx, (
        f"[fts-reconcile] {name}: reconcile docs={rep['docs']} != docs "
        f"tabla={docs_in_idx} — drift FTS (FS-1); remediación: "
        f"drop_fts('{name}') y reabrir, o restaurar backup"
    )
    print(f"[fts-reconcile] {name}: {rep} — paridad docs OK")

# 2) Fulltext delta — término real del SSOT
docs_delta = b1.materialize_all("delta")
delta_docs = b1.get_docs("delta")
# construir query desde un término real: título primario o el primer doc
sample_obj = next(
    (str(v.get("objective", "")) for v in delta_docs.values() if v.get("objective")),
    None,
)
print(f"\n[delta] sample objective: {sample_obj!r}" if sample_obj else "[delta] sin objectives")

from fast_mcp.shared_kernel.cognitive_graph.models import tokenize  # noqa: E402
if sample_obj:
    tokens = tokenize(sample_obj)
    if tokens:
        res = b1.search("delta", {"text": tokens[0]})
        total = res["hits"]["total"] if isinstance(res.get("hits"), dict) else None
        print(f"[delta.fulltext] search({tokens[0]!r}) → {total} hits")
        assert total and total > 0, "fulltext delta sin hits — F1 roto post-migración"

# Instancia FRESCA para los checks light (M-07: el router no materializa)
b1._db.close()  # noqa: SLF001
g3 = build_cognitive_graph(chain_dir=DATA_DIR)
b3 = g3._backend  # noqa: SLF001

# 3) Structured DELTA — filters={'type': ...} resuelve por doc_type (F2 router)
top_type = next(
    (str(v["type"]) for v in delta_docs.values() if v.get("type")), None
)
if top_type:
    d3 = b3._indexes["delta"]  # noqa: SLF001
    expected_type = sum(1 for v in delta_docs.values() if str(v.get("type", "")) == top_type)
    assert d3._loaded is False, "delta materializado al arrancar — viola M-09"
    res = b3.search("delta", {"type": top_type})
    total = res["hits"]["total"] if isinstance(res.get("hits"), dict) else None
    print(f"[delta.structured] filters={{type: {top_type!r}}} → {total} hits "
          f"(esperado: {expected_type})")
    assert total == expected_type, (
        f"router structured delta: {total} vs esperado {expected_type}"
    )
    assert d3._loaded is False, "search structured materializó delta — viola M-07"
else:
    print("[delta.structured] SKIP — delta sin docs con tipo")

# 4) Structured TRACE — el schema v2 mapea event_type a SU columna
#    (fix 2026-11-16: antes todos los campos caían en 'keyword', que no es
#    columna → resolve_filters devolvía None y TODO filtro daba 0 hits, también
#    con datos presentes). Ahora el router resuelve por SQL sin materializar
#    (M-09) y discrimina entre valor presente y ausente.
t3 = b3._indexes["trace"]  # noqa: SLF001
assert t3._loaded is False
res = b3.search("trace", {"event_type": "ToolRegistered"})
total = res["hits"]["total"] if isinstance(res.get("hits"), dict) else None
print(f"[trace.structured] event_type=ToolRegistered → {total} hits (esperado 1)")
assert total == 1, (
    "trace structured no resuelve event_type por columna — regresión del "
    "mapeo a 'keyword' (fix 2026-11-16)"
)
res = b3.search("trace", {"event_type": "NoExiste"})
total_absent = res["hits"]["total"] if isinstance(res.get("hits"), dict) else None
print(f"[trace.structured] event_type=NoExiste → {total_absent} hits (esperado 0)")
assert total_absent == 0, "trace structured inventa hits para un valor inexistente"
assert t3._loaded is False, "filtro estructurado de trace materializó el índice"
b3._db.close()  # noqa: SLF001

print("\nSMOKE OK — idempotencia, fulltext delta y router structured verificados en v3.")