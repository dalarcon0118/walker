"""cognitive_graph/schemas.py — PRODUCTION_INDEX_SCHEMAS: SSOT ÚNICO.

T-10 (M-6) del plan de remediación ADR-WALKER-075: los schemas de
producción (delta/trace/cpg/ckg) estaban duplicados en 3 sitios
(composition root, scripts/migrate_canonical_v3.py, scripts/m0_baseline.py)
incluido el quirk de trace ×3. Todos los sitios IMPORTAN de aquí — jamás
definen copias literales (refactor mecánico, cero cambio de comportamiento).

Canonicidad (FS-P4 resuelto): cada schema = la forma RUNTIME actual:
  - delta: composition root (`_build_delta_repo`)
  - trace: composition root (trace runner) — quirk pre-existente
    `event_type → 'keyword'` (tipo ES, no campo): definido UNA vez; la
    decisión del PO se reduce a editar 1 línea + drop/re-backfill.
  - cpg: `features/code_analysis/schema.py` (register_cpg_index) — la
    copia de migrate_canonical_v3 era STALE (fields node_id/kind); se
    descarta en favor de la forma runtime.
  - ckg: `features/cognitive_knowledge/.../cognitive_ckg_repo.py`
"""

from __future__ import annotations

PRODUCTION_INDEX_SCHEMAS: dict[str, dict] = {
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
    "trace": {
        "fields": {
            "type": "type",
            "event_type": "event_type",
            "source": "source",
            "task_id": "task_id",
            "sprint_id": "sprint_id",
            "node": "node",
            "tool": "tool",
        },
        "description": "TraceGraph: eventos de ejecución (consulta por task_id/event_type)",
    },
    "cpg": {
        "fields": {"function": "name", "file": "path", "type": "type"},
        "types": {"function": {}, "class": {}, "file": {}, "module": {}},
        "search": {
            "type": "fulltext",
            "fields": ["name", "path", "signature", "kind"],
        },
        "description": "CPG: Code Property Graph - estructura del codigo fuente",
    },
    "ckg": {
        "fields": {
            "type": "type",
            "scope": "scope",
            "label": "statement",
            "description": "rationale",
            "tag": "tags",
            "task": "metadata.task_id",
        },
        "types": {
            "decision": {}, "refactor": {}, "convention": {},
            "pattern": {}, "rule": {}, "component": {},
        },
        "search": {"type": "fulltext", "fields": ["statement", "rationale"]},
    },
}