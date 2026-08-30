"""import_ckg_v1_cli.py — Importa conocimiento CKG v1 (.json.gz) VÍA LA API PÚBLICA.

ADR-WALKER-063 (corte directo): sqlite (v2) es la única fuente de verdad del
runtime. Este script NO copia blobs ni filas: LEE los datos del archivo v1
(formato de índice histórico {docs, edges, metadata} o KnowledgeGraph v3.0
{version, nodes, edges}) y los AGREGA al CKG llamando a la API pública de
los application services del feature:

    CkgApplicationService.save_nodes(chain_dir, nodes)
      → SaveToCkgUseCase → CodeKnowledgeGraph.save_nodes
      → CognitiveCkgRepository.save_nodes (puerto → backend por config)

El mapeo datos → nodos canónicos (label = statement, description =
rationale, type validado) y la persistencia son 100% responsabilidad del
dominio; el script solo traduce el formato de entrada y delega.

Trazabilidad: cada nodo importado lleva `metadata["legacy_doc_id"]` = el id
del doc v1 (NUNCA se copian ids internos; el repo genera ids nuevos
type-NNN vía max_key). Idempotencia: en re-ejecuciones, los docs cuyo
legacy_doc_id ya existe se SKIP (contados), sin duplicar.

Filosofía (directiva): ejecutar de verdad, investigar la capa del fallo,
corregir y re-ejecutar. Cualquier error propaga con traceback completo
(nunca silencioso); el script sale con código != 0.

Uso:
    python -m features.cognitive_knowledge.application.import_ckg_v1_cli \
        --chain-dir <sprint o project_root>            \
        [--src <path al ckg.json.gz v1>]               \
        [--data-dir <data_dir del proyecto>]           \
        [--batch 25] [--dry-run]

Exit codes:
    0   import completado (o --dry-run sin errores de mapeo)
    2   error de argumentos / archivo de origen inexistente
    3   falló la persistencia vía API (investigar la capa del traceback)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("import_ckg_v1")

# Tipos canónicos del CKG (domain/models.py + CKG_INDEX_CONFIG).
VALID_TYPES = {"decision", "refactor", "convention", "pattern", "rule", "component"}

# Alias históricos → canónico. El blob legacy real usa español e incluso el
# typo 'decisione' (3.6k docs reales: 2410 'regla' + 1164 'decisione').
TYPE_ALIASES = {
    "decision": "decision", "decisión": "decision", "decisione": "decision",
    "refactor": "refactor", "refactorizacion": "refactor", "refactorización": "refactor",
    "convention": "convention", "convencion": "convention", "convención": "convention",
    "pattern": "pattern", "patron": "pattern", "patrón": "pattern",
    "rule": "rule", "regla": "rule",
    "component": "component", "componente": "component",
}

# Campos canónicos del nodo; el resto viaja en metadata (el repo normaliza).
_CANONICAL_FIELDS = {
    "label", "description", "rationale", "type", "tags", "metadata",
    "supersedes", "subject", "statement", "context", "query_hints",
    "location", "api", "status", "author", "source", "confidence",
    "_id", "id",
}


def _inspect_unmapped(docs: list[dict], api_nodes: list[dict], expected: int) -> None:
    """Diagnóstico: muestra la estructura de los docs que NO mapearon."""
    unmapped = []
    for doc in docs:
        statement = doc.get("statement") or doc.get("label") or doc.get("subject")
        has_stmt = bool(statement and str(statement).strip())
        if not has_stmt:
            unmapped.append(doc)
    logger.warning(
        "INSPECT: %d docs sin statement/label/subject (esperado %d)",
        len(unmapped), expected,
    )
    by_type: dict[str, int] = {}
    for doc in unmapped:
        t = str(doc.get("type", "") or "(sin type)")
        by_type[t] = by_type.get(t, 0) + 1
    logger.info("INSPECT: tipos de los no mapeados: %s", by_type)
    for i, doc in enumerate(unmapped[:3], start=1):
        keys = sorted(doc.keys())
        sample = {k: (str(doc[k])[:60] if doc[k] is not None else None) for k in keys[:15]}
        logger.info(
            "INSPECT muestra[%d] _id=%r type=%r -> %s",
            i, doc.get("_id"), doc.get("type"), sample,
        )


def _register_service_locator() -> None:
    """Composition root del proceso CLI (fase 5, ADR-045/058).

    El repo CKG resuelve 'cognitive_graph' por el service locator neutro
    (shared.core.engine.bootstrap.set_service_locator) — el MISMO contrato
    que registra features/main en producción. La factory devuelve la facade
    pública por puerto (CognitiveGraph(conn=conn), backend por config);
    NUNCA importa features/main ni reconstruye el dominio.
    """
    from shared.core.engine.bootstrap import set_service_locator

    def _resolve(name: str):
        if name == "cognitive_graph":
            from shared.libs.cognitive_graph import CognitiveGraph
            from app.memories.registration import apply_memory_indexes
            return lambda conn: CognitiveGraph(conn=conn, register_defaults=apply_memory_indexes)
        return None

    set_service_locator(_resolve)


def _read_v1_blob(src: str) -> dict:
    """Lee el archivo v1 y lo devuelve como dict (formato índice o v3.0)."""
    from shared.libs.cognitive_graph.file_io import read_blob

    text = read_blob(src)
    if text is None:
        logger.error("blob v1 ilegible o inexistente: %s", src)
        sys.exit(2)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error("blob v1 no es JSON válido (%s): %s", type(e).__name__, src)
        sys.exit(2)
    if not isinstance(payload, dict):
        logger.error("blob v1 sin estructura de objeto JSON: %s", src)
        sys.exit(2)
    return payload


def _docs_from_payload(payload: dict, src: str) -> list[dict]:
    """Normaliza DOCS desde el formato histórico del índice o v3.0."""
    docs = payload.get("docs")
    if docs is None and isinstance(payload.get("nodes"), list):
        # Formato KnowledgeGraph v3.0: {version, nodes:[{id,type,label,...}]}
        docs = []
        for n in payload["nodes"]:
            if not isinstance(n, dict):
                continue
            doc = dict(n)
            doc.setdefault("_id", n.get("id", n.get("_id", "")))
            docs.append(doc)
    if not isinstance(docs, list):
        logger.error(
            "blob v1 sin docs[]/nodes[] reconocibles (estructura inesperada): %s", src
        )
        sys.exit(2)
    out: list[dict] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        out.append(doc)
    return out


def _to_api_node(doc: dict, unknown_types: dict[str, int]) -> dict | None:
    """Mapea un doc v1 a un nodo canónico de la API pública del CKG.

    label = statement | label | subject (el statement ES el label del CKG).
    description = rationale | description. type canónico vía TYPE_ALIASES
    (default 'decision', como build_ckg_node); los tipos NO reconocidos se
    cuentan agrupados en unknown_types (un warning por tipo, no por doc).
    El resto de campos NO canónicos viaja en metadata (el repo los
    normaliza); legacy_doc_id preserva el id v1.
    """
    statement = doc.get("statement") or doc.get("label") or doc.get("subject")
    if not statement or not str(statement).strip():
        return None

    node_type = str(doc.get("type", "") or "decision").lower().strip()
    node_type = TYPE_ALIASES.get(node_type, node_type)
    if node_type not in VALID_TYPES:
        unknown_types[node_type] = unknown_types.get(node_type, 0) + 1
        node_type = "decision"

    meta: dict = {}
    for k, v in doc.items():
        if k in _CANONICAL_FIELDS or v is None:
            continue
        meta[k] = v
    meta.setdefault("legacy_doc_id", doc.get("_id") or doc.get("id") or "")

    return {
        "label": str(statement),
        "description": str(doc.get("rationale") or doc.get("description") or ""),
        "type": node_type,
        "tags": list(doc.get("tags", []) or []),
        "metadata": meta,
    }


def _existing_legacy_ids(chain_dir: str) -> set[str]:
    """Ids v1 ya importados, vía la API pública de lectura (query_all)."""
    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )

    existing: set[str] = set()
    t0 = time.monotonic()
    try:
        for node in CkgApplicationService.query_all(chain_dir):
            lid = (node.get("metadata", {}) or {}).get("legacy_doc_id")
            if lid:
                existing.add(str(lid))
        logger.info(
            "dedupe: %d legacy_doc_id ya en el CKG (%.1fs)",
            len(existing), time.monotonic() - t0,
        )
    except Exception as e:  # noqa: BLE001 — fail-open controlado (solo dedupe)
        logger.warning(
            "no se pudo consultar el CKG actual para dedupe (%s: %s); "
            "se importará sin skip", type(e).__name__, e,
        )
    return existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="import_ckg_v1",
        description="Importa conocimiento CKG v1 a sqlite v2 por la API pública.",
    )
    parser.add_argument(
        "--chain-dir", required=True,
        help="Dir del sprint o project_root (resuelve el data_dir del proyecto).",
    )
    parser.add_argument("--src", default="", help="Path al ckg.json.gz v1 (opcional).")
    parser.add_argument("--data-dir", default="", help="data_dir del proyecto (opcional).")
    parser.add_argument("--batch", type=int, default=25, help="Tamaño de lote por save_nodes.")
    parser.add_argument("--dry-run", action="store_true", help="Valida el mapeo sin persistir.")
    parser.add_argument(
        "--purge", action="store_true",
        help=(
            "Borra vía API pública los nodos de un import previo "
            "(metadata.task_id='migracion-v1') ANTES de importar "
            "(re-rollo tras corregir el mapeo)."
        ),
    )
    parser.add_argument(
        "--inspect", action="store_true",
        help="Diagnóstico: muestra la estructura de los docs NO mapeados.",
    )
    args = parser.parse_args(argv)

    _register_service_locator()

    from features.project import ProjectService

    paths = ProjectService.resolve_paths(args.chain_dir)
    project_root = str(paths.project_root) if getattr(paths, "project_root", None) else ""
    data_dir = args.data_dir or (str(paths.data_dir) if getattr(paths, "data_dir", None) else "")
    if not project_root and not data_dir:
        logger.error("no se pudo resolver project_root para --chain-dir=%r", args.chain_dir)
        return 2

    base_dir = data_dir or f"{project_root}/data"
    src = args.src or os.path.join(base_dir, "ckg.json.gz")
    if not os.path.exists(src):
        # Fallback a .json plano (fixtures/pruebas) y peer legacy histórico.
        for candidate in (
            os.path.join(base_dir, "ckg.json"),
            os.path.join(project_root, "data", "cog", "data", "ckg.json.gz"),
        ):
            if os.path.exists(candidate):
                src = candidate
                break
    if not os.path.exists(src):
        logger.error("blob v1 no encontrado (--src o ckg.json.gz/json en %s): %s", base_dir, src)
        return 2

    logger.info("origen: %s", src)
    logger.info("project_root: %s | data_dir: %s", project_root, data_dir)

    payload = _read_v1_blob(src)
    docs = _docs_from_payload(payload, src)
    logger.info("docs v1 en el blob: %d", len(docs))

    api_nodes: list[dict] = []
    skipped: list[str] = []
    unknown_types: dict[str, int] = {}
    no_statement: list[dict] = []
    for doc in docs:
        node = _to_api_node(doc, unknown_types)
        if node is None:
            stmt = doc.get("statement") or doc.get("label") or doc.get("subject")
            if not (stmt and str(stmt).strip()) and len(no_statement) < 6:
                no_statement.append(
                    {"_id": doc.get("_id"), "keys": sorted(doc.keys())[:12]}
                )
            continue
        api_nodes.append(node)

    for t, count in sorted(unknown_types.items()):
        logger.warning(
            "type %r NO reconocido (%d docs) → mapeado a 'decision'", t, count,
        )
    if no_statement:
        logger.warning(
            "%d docs sin statement/label/subject — no mapeables (muestras: %s)",
            len(docs) - len(api_nodes), no_statement,
        )
    logger.info("nodos mapeados a la API: %d (de %d docs)", len(api_nodes), len(docs))
    if args.dry_run:
        for i, node in enumerate(api_nodes[:5], start=1):
            logger.info(
                "dry-run[%d] type=%s label=%r (legacy_doc_id=%s, keys=%s)",
                i, node["type"], node["label"][:60],
                node["metadata"].get("legacy_doc_id"),
                sorted(node["metadata"].keys()),
            )
        logger.info("DRY-RUN: mapeo válido, no se persistió nada.")
        return 0

    if args.inspect and not args.dry_run:
        _inspect_unmapped(docs, api_nodes, len(docs) - len(api_nodes))
        return 0

    from returns.pipeline import is_successful

    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )

    if args.purge:
        # El purge DEBE correr ANTES del dedupe: si el dedupe mira la BD
        # sucia (imports previos con los mismos legacy_doc_id), los marca
        # "ya importados" y el re-import solo repone lo que faltaba (bug
        # real: 3598 esperados → 12 importados).
        t0 = time.monotonic()
        # Campo 'task' del schema CKG → metadata.task_id (docs de migracion-v1).
        purge = CkgApplicationService.delete_by_metadata(
            args.chain_dir, "task", "migracion-v1"
        )
        removed = int(purge.get("nodes_removed", 0) or purge.get("deleted", 0) or 0)
        logger.info(
            "PURGE: %d nodos de migracion-v1 borrados vía API (%.1fs)",
            removed, time.monotonic() - t0,
        )

    existing = _existing_legacy_ids(args.chain_dir)
    to_save: list[dict] = []
    for node in api_nodes:
        lid = node["metadata"].get("legacy_doc_id")
        if lid and lid in existing:
            skipped.append(str(lid))
            continue
        to_save.append(node)

    # Algunos docs v1 no tienen _id (formato v3.0 sin id): no dedupe, se guardan.
    logger.info("a guardar: %d | ya importados (skip): %d", len(to_save), len(skipped))
    if not to_save:
        logger.info("nada que importar (todo ya presente o vacío).")
        return 0

    total_saved = 0
    total_degraded: list[str] = []
    t_start = time.monotonic()
    for start in range(0, len(to_save), args.batch):
        batch = to_save[start : start + args.batch]
        t_batch = time.monotonic()
        logger.info(
            "save_nodes batch %d..%d (%d nodos)...",
            start + 1, start + len(batch), len(batch),
        )
        result = CkgApplicationService.save_nodes(
            args.chain_dir,
            batch,
            task_id="migracion-v1",
            session_id="legacy-import",
        )
        if not is_successful(result):
            error = result.failure()
            logger.error(
                "FALLO en la API (SaveToCkgUseCase) — capa: %s: %s",
                type(error).__name__, error,
            )
            return 3
        saved = result.value_or({})
        ok = bool(saved.get("ok", False))
        if not ok:
            logger.error("save_nodes respondió ok=False: %s", saved)
            return 3
        total_saved += int(saved.get("nodes_saved", 0) or 0)
        total_degraded.extend(saved.get("degraded", []) or [])
        inner = saved.get("result", {}) or {}
        logger.info(
            "  batch ok: nodes_created=%s node_ids=%s (%.1fs)",
            saved.get("nodes_saved"), inner.get("node_ids", [])[:3],
            time.monotonic() - t_batch,
        )

    logger.info(
        "IMPORT COMPLETO: %d nodos guardados vía API | %d degradados | %d skip | %.1fs total",
        total_saved, len(total_degraded), len(skipped), time.monotonic() - t_start,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI
    sys.exit(main())