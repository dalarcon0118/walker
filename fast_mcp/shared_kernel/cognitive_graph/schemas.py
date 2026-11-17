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
# ─── Proyección y ranking de respuestas (fix 2026-11-16) ────────────────────
# Antes nadie inyectaba estos valores: `build_cognitive_graph` no los aceptaba y
# `CognitiveGraph` los inicializaba vacíos, así que `_compact_result` no
# proyectaba campos (devolvía el doc completo) y el ranking por severidad era
# inerte (`self.severity_order.get(..., 9)` siempre devolvía 9).
#
# Son negocio del walker (qué campos importan en una respuesta compacta y qué
# severidad va primero), no del storage genérico. Se definen acá (SSOT de
# schemas) y el factory `build_cognitive_graph` los inyecta por defecto.

COMPACT_FIELDS: dict[str, list[str]] = {
    "ckg": ["statement", "type", "tags", "scope", "severity"],
    "delta": ["objective", "title", "type", "spec_id", "spring_id", "domain"],
    "trace": ["event_type", "tool", "node", "task_id", "timestamp"],
    "cpg": ["name", "path", "type", "kind", "line"],
}

# Menor número = mayor prioridad. `_compact_result` ordena ascendente, así que
# critical (0) va antes que low (8). Desconocido → 9 (al final).
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}
