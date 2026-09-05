# Baseline storage — M0 re-ejecutado en v3 (ADR-WALKER-075 §9, cierra G5)

**Regla §9:** ningún umbral de este ADR (M-03, M-05, FS-5, `compaction_trigger`, p99)
se ACTIVA sin baseline medido antes — umbral sin baseline = opinión.
Recalibración: `umbral = max(baseline_p99 × 2, valor propuesto)`; si el baseline
ya cumple el umbral propuesto, el umbral pasa a ser de no-regresión.
Esta corrida es la RE-EJECUCIÓN sancionada por §9 ("M0 se re-ejecuta solo si
cambia el formato de payload (v3)") — post F1+F2+F3; el baseline v2 histórico
vive en el commit 6c40c28.

- Fecha: 2026-09-05T01:49:25Z
- Entorno: Python 3.12.2 / sqlite 3.45.1 / arm64
- Canónica: `/Users/davidmartinez/develop/Active-projects/walker/.cognitive/projects/walker/data/cognitive.sqlite3` (376,832 bytes)
- Fixture: 16,000 docs + 24,000 edges deterministas
  (seed 42, shape cpg real — `tests/benchmarks/cpg_fixture.py`),
  build 736.9 ms.

## Precondición H0 (§10.0 — gate verde, commit 54f019e)

| Check | Evidencia |
|---|---|
| H-1 integrity | `ok` |
| H-3 huérfanos | 0 |
| H-6 derivables | cpg=0, ckg=0 (dropeados — rebuild post-migración) |
| SSOT delta/trace | delta=8 docs, trace=872 docs — intactos |

## Métricas (protocolo §9)

| Métrica | p50 | p95 | p99 | n | Detalle |
|---|---|---|---|---|---|
| `save()` 1 doc nuevo | 113.88 ms | 124.241 ms | 133.639 ms | 100 | timer alrededor de `_save` |
| `save()` 1 edge nueva | 114.597 ms | 122.358 ms | 126.455 ms | 100 | idem |
| `save()` 0 cambios | 115.553 ms | 122.868 ms | 124.425 ms | 50 | diff-scan completo (costo O(docs+edges)) |
| `load` light (boot, post-F3) | — | — | 94.9 ms (min de 3) | 3 | `_ensure_light` (sin parsear docs): corridas [121.7, 94.9, 106.7] |
| `load` materialize (on-demand) | — | — | 70.5 ms (min de 3) | 3 | `_load` completo: corridas [70.5, 82.2, 82.2] |
| fulltext recall `{"text": ...}` | 14.872 ms | 16.211 ms | 19.995 ms | 100 | hits 100/100 — post 6e96f58, pre-FTS5 |
| query selectiva `{"type": "function"}` | 3.983 ms | 4.843 ms | 6.133 ms | 100 | índice materializado, 12100 hits |
| WAL growth tras 1 build | — | — | — | — | 12,442,432 bytes (replace_blob 16K+24K) |

Load de la COPIA REAL (SSOT post-H0): ckg: 0.0 ms / 0 docs, cpg: 0.0 ms / 0 docs, delta: 0.2 ms / 8 docs, trace: 4.7 ms / 872 docs.

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
