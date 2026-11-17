"""scripts/derive_knowledge_tags.py — barrido one-shot de tags derivados del CKG.

Problema medido (2026-11-16): `project_patterns` y `quality_gates` quedaban
vacíos en backend-microservices porque su conocimiento está tipado como
`decision`/`implementation_state`/`verification` y el collector consulta por
`pattern`/`rule`/`convention` (+ tags). Reclasificar el `type` sería destructivo
(un agente que filtre `type: decision` dejaría de ver los ADR), así que el
barrido es ADITIVO: agrega tags derivados por contenido y no toca el `type` ni
borra tags existentes.

Uso:
    python3 scripts/derive_knowledge_tags.py --project backend-microservices
    python3 scripts/derive_knowledge_tags.py --project backend-microservices --apply

Sin --apply solo reporta (dry-run). Con --apply escribe en el CKG del proyecto.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Reglas deterministas: tag -> fragmentos que lo disparan (case-insensitive).
# Se separan PREFIJO (el doc EMPIEZA con eso) de CONTENIDO: un ADR menciona
# "ADR-018" en todas partes, así que buscar la subcadena etiquetaba al 93% de
# los docs y el tag dejaba de discriminar.
PREFIX_RULES: dict[str, tuple[str, ...]] = {
    "architecture": ("adr-", "adr "),
    "quality": ("inv-", "regla", "convención", "convencion"),
    "pattern": ("patrón", "patron"),
}

CONTENT_RULES: dict[str, tuple[str, ...]] = {
    "architecture": ("arquitectura", "bounded context", "hexagonal",
                     "clean architecture", "capa de dominio"),
    "pattern": ("value object", "use case", "caso de uso", "puerto",
                "agregado", "aggregate", "repositorio"),
    "quality": ("invariante", "regla de negocio"),
}


def derive_tags(statement: str, rationale: str = "") -> set[str]:
    """Tags que corresponden al contenido (sin mirar el type actual)."""
    head = statement.strip().lower()
    text = f"{statement} {rationale}".lower()
    tags = {
        tag
        for tag, prefixes in PREFIX_RULES.items()
        if any(head.startswith(p) for p in prefixes)
    }
    tags |= {
        tag
        for tag, needles in CONTENT_RULES.items()
        if any(n in text for n in needles)
    }
    return tags


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="project_id registrado en walker")
    ap.add_argument("--apply", action="store_true", help="escribe (default: dry-run)")
    args = ap.parse_args()

    from fast_mcp.features.cognitive_knowledge import ckg_service
    from fast_mcp.shared_kernel.project_support import ProjectService

    registry = json.loads(
        (Path(__file__).resolve().parent.parent / "data/projects.json").read_text()
    )["projects"]
    entry = registry.get(args.project)
    if not entry:
        print(f"proyecto desconocido: {args.project}", file=sys.stderr)
        return 2

    paths = ProjectService.resolve_paths(entry["repo_path"])
    ckg = ckg_service(str(paths.data_dir))
    if ckg is None:
        print("CKG no disponible", file=sys.stderr)
        return 2

    docs = ckg._repo._cg.get_docs("ckg")  # noqa: SLF001 — script one-shot
    propuestos: dict[str, list[str]] = {}
    cambios: dict[str, dict] = {}
    for doc_id, doc in docs.items():
        tags = derive_tags(str(doc.get("statement", "")), str(doc.get("rationale", "")))
        nuevos = sorted(tags - set(doc.get("tags") or []))
        if not nuevos:
            continue
        for tag in nuevos:
            propuestos.setdefault(tag, []).append(doc_id)
        cambios[doc_id] = {**doc, "tags": sorted(set(doc.get("tags") or []) | tags)}

    print(f"proyecto: {args.project} · docs ckg: {len(docs)} · con tags nuevos: {len(cambios)}")
    for tag in sorted(propuestos):
        ids = propuestos[tag]
        print(f"  {tag:<14} +{len(ids):<3} docs  (ej: {', '.join(sorted(ids)[:3])})")

    if not args.apply:
        print("\n(dry-run) volvé a correr con --apply para escribir.")
        return 0
    if not cambios:
        print("nada que aplicar")
        return 0

    ckg._repo._cg.store_many("ckg", cambios)  # noqa: SLF001 — upsert por id
    print(f"\naplicado: {len(cambios)} docs actualizados (tags aditivos, type intacto)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
