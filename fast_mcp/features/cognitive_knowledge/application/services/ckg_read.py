"""application/services/ckg_read.py — CkgReadService.

All CKG read operations: queries, search, schema, health checks.

SRP: this service handles read operations only. Write operations are in
ckg_write.py, extraction in ckg_extract.py.
"""
from __future__ import annotations

from typing import Any

from returns.pipeline import is_successful

from fast_mcp.features.cognitive_knowledge.application.services.ckg_common import (
    _resolve_ckg,
)

_SINGLE_QUERY_KEYS = (
    "text", "type", "file", "function", "scope", "status",
    "severity", "tag", "label", "task", "description",
    "signature", "kind", "domain", "include_superseded",
    "tool", "session", "agent", "sprint", "node",
    "sub_state", "source",
)


def _is_test_seed(node: dict) -> bool:
    """Nodo de prueba del harness (seed), no conocimiento del proyecto."""
    tags = node.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    meta = node.get("metadata") or {}
    return bool(
        "test-seed" in tags
        or "origin:test" in tags
        or str(meta.get("origin", "")) == "test"
    )


def _is_session_node(node: dict) -> bool:
    """Nodo capturado por una sesión (memoria de trabajo), no una decisión.

    Medido en el golden set (2026-11-20): al preguntar por el engine puro
    (`adr-walker-067`) el top-1 era un path de OTRO proyecto capturado en una
    sesión, y la pregunta por tareas/workflow devolvía `session:roadmap-…`.
    Estos nodos traen `metadata.session_id` y `metadata.source = "session:<sid>"`
    (el `scope` es el del proyecto, así que no sirve como señal). Se recuperan
    por `trace`/`delta`, no por el índice de decisiones.
    """
    meta = node.get("metadata") or {}
    if str(meta.get("source") or "").startswith("session:"):
        return True
    if str(meta.get("session_id") or "").strip():
        return True
    if str(node.get("type") or meta.get("type") or "").lower() == "session":
        return True
    return str(node.get("_id") or node.get("id") or "").startswith("session-")


def _with_compact_defaults(queries: dict) -> dict:
    """Add default compact search parameters."""
    out = dict(queries or {})
    if isinstance(out, dict):
        out.setdefault("_top_k", 10)
        out.setdefault("_max_chars", 400)
        out.setdefault("_compact", True)
    return out


def _is_authority(node: dict) -> bool:
    """Nodo con autoridad de proyecto: ADR — o índice derivado de `docs/`.

    La memoria de trabajo de una sesión (`source: session:*`) es evidencia de
    lo que pasó, no una decisión del proyecto. Desplazaba ADRs en el top-5 del
    golden set (`decision-001` con statement "proceed" rankeaba 1º).
    """
    meta = node.get("metadata") or {}
    if meta.get("adr"):
        return True
    if str(meta.get("source") or "").startswith("docs/"):
        return True
    return str(node.get("id") or node.get("_id") or "").startswith("adr-")


#: Tope del `rationale` que viaja al agente. El cuerpo completo se sigue
#: indexando para búsqueda; la salida no paga 6 KB por resultado × 20.
_MAX_RATIONALE = 600


def _normalize_node(node: dict) -> dict:
    """Homogeneiza la lectura: `_id` → `id` y `label` con contenido.

    El storage devuelve `_id` y no devuelve `label` (es la clave con la que se
    guardó). Cualquier consumidor que mire `node["id"]` recibía None — pasó con
    `session_context` y con la verificación de escrituras.

    Desde F2 el `rationale` se indexa completo (cuerpo del ADR) pero se
    devuelve recortado: el payload de 20 decisiones no puede crecer con el
    corpus. `rationale_chars` dice el tamaño real para no confundir recorte
    con documento corto.
    """
    if not isinstance(node, dict):
        return node
    node = dict(node)
    if not node.get("id"):
        node["id"] = node.get("_id") or ""
    if not node.get("label"):
        node["label"] = node.get("statement") or node.get("subject") or node.get("id") or ""
    rationale = node.get("rationale")
    if isinstance(rationale, str) and len(rationale) > _MAX_RATIONALE:
        node["rationale"] = rationale[:_MAX_RATIONALE].rstrip() + "…"
        node["rationale_chars"] = len(rationale)
    return node


class CkgReadService:
    """Read operations for the CKG."""

    # ── Direct queries ────────────────────────────────────────────────

    @staticmethod
    def query_active(chain_dir: str, type_name: str = "", size: int = 0) -> list[dict]:
        """Search active (non-superseded) nodes in the CKG.

        `size` > 0 pide una página explícita (la retención necesita ver todos
        los `session` activos, no los primeros 50 del default).
        """
        ckg, _ = _resolve_ckg(chain_dir)
        if not ckg:
            return []
        query: dict = {"include_superseded": False}
        if type_name:
            query["type"] = type_name
        if size > 0:
            query["size"] = int(size)
        return [_normalize_node(n) for n in (ckg.query(query) or [])]

    @staticmethod
    def query_by_files(chain_dir: str, files: list[str]) -> list[dict]:
        """Search nodes in the CKG by files."""
        ckg, _ = _resolve_ckg(chain_dir)
        if not ckg:
            return []
        results = []
        for f in files:
            nodes = ckg.query({"file": f}) or []
            results.extend(_normalize_node(n) for n in nodes)
        return results

    @staticmethod
    def query_all(chain_dir: str) -> list[dict]:
        """Search all nodes in the CKG (no filters)."""
        ckg, _ = _resolve_ckg(chain_dir)
        if not ckg:
            return []
        return [_normalize_node(n) for n in (ckg.query({"size": 100000}) or [])]

    @staticmethod
    def gather_context(
        chain_dir: str,
        query: str = "type:convention OR type:decision",
        task_id: str = "",
    ):
        """Gather CKG context for a task."""
        from returns.pipeline import is_successful
        from returns.result import safe, Success

        @safe
        def _gather() -> list:
            ckg, _ = _resolve_ckg(chain_dir)
            q = f"task_id:{task_id}" if task_id else query
            rows = ckg.search(q, max_results=50) if ckg else []
            # P1.2: los nodos de prueba del harness no son decisiones del
            # proyecto. Citarlos (p. ej. `adr-999`) le da autoridad falsa a un
            # seed; se excluyen por tag/origen, no por lista negra de ids.
            # P1.5: un nodo superseded tampoco es autoridad vigente (el
            # contrato del puerto dice que las queries devuelven solo activos;
            # `search` por texto no lo aplica, así que se filtra acá).
            # F2: los nodos de sesión (`session-*`) son memoria de trabajo; se
            # recuperan por trace/delta y desplazaban decisiones en el top-5.
            # Si al filtrarlos no queda nada, se devuelve la lista sin filtrar:
            # "no hay decisión" no debe convertirse en "no hay nada" (medido:
            # la pregunta por tasks/workflow quedaba en 0 resultados).
            decisions = [
                n for n in rows
                if not _is_test_seed(n)
                and not _is_session_node(n)
                and str((n.get("metadata") or {}).get("status", "") or "")
                not in ("superseded", "deprecated")
            ]
            if decisions:
                # F2: autoridad primero. `sorted` es estable, así que dentro de
                # cada tier se conserva el orden bm25 del índice. Un nodo de
                # sesión ("proceed", "el CKG deriva label") no puede rankear
                # por encima de un ADR en una pregunta de arquitectura.
                decisions.sort(key=lambda n: 0 if _is_authority(n) else 1)
                return [_normalize_node(n) for n in decisions[:20]]
            # Sin decisiones: devolver lo recuperado (menos seeds) antes que
            # convertir "no hay decisión" en "no hay nada".
            return [
                _normalize_node(n) for n in rows
                if not _is_test_seed(n)
                and str((n.get("metadata") or {}).get("status", "") or "")
                not in ("superseded", "deprecated")
            ][:20]

        result = _gather()
        return result if is_successful(result) else Success([])

    @staticmethod
    def get_nodes_for_task(chain_dir: str, task_id: str, task_files: list[str]):
        """Get CKG nodes relevant to a task."""
        from returns.result import safe

        @safe
        def _get() -> list:
            ckg, _ = _resolve_ckg(chain_dir)
            if not ckg:
                return []
            by_task = ckg.search(f"task_id:{task_id}") if task_id else []
            by_files = []
            for f in task_files:
                nodes = ckg.search(f"file:{f}")
                by_files.extend(nodes)
            seen = set()
            result = []
            for node in by_task + by_files:
                nid = node.get("_id") or node.get("id", "")
                if nid and nid not in seen:
                    seen.add(nid)
                    result.append(node)
            return result[:50]

        return _get()

    @staticmethod
    def check_health(projects_data_dir: str):
        """Check CKG health: node count and status."""
        from returns.result import safe

        @safe
        def _read_ckg() -> dict:
            from fast_mcp.features.cognitive_knowledge.infrastructure.ckg_reader import (
                read_ckg_via_port,
            )

            data = read_ckg_via_port(projects_data_dir)
            total = len(data.get("nodes", []))
            return {"status": f"{total} nodes", "node_count": total}

        return _read_ckg()

    # ── Search / Schema ───────────────────────────────────────────────

    @staticmethod
    def schema(searcher: Any, index: str = "") -> dict:
        """Get schema info for an index or list all indexes."""
        if index:
            try:
                return searcher.schema(index)
            except KeyError:
                return {
                    "type": "error",
                    "message": f"Index '{index}' not found. Available: {searcher.list_indexes()}",
                }
        return {"type": "schema", "indexes": searcher.list_indexes()}

    @staticmethod
    def search(searcher: Any, payload: dict, project_id: str = "") -> dict:
        """Single index search with superseded fallback."""
        if set(payload.keys()) == {"_index"} or (
            len(payload) == 1 and "_index" in payload
        ):
            index = payload.get("_index", "ckg")
            try:
                return searcher.search(index, {})
            except KeyError:
                return {
                    "type": "error",
                    "message": f"Index '{index}' not found. Available: {searcher.list_indexes()}",
                }

        has_single_query_keys = any(k in payload for k in _SINGLE_QUERY_KEYS)
        if not has_single_query_keys and len(payload) > 0:
            return searcher.search_many(_with_compact_defaults(payload))

        index = payload.get("_index", "ckg")
        query = {k: v for k, v in payload.items() if k != "_index"}
        include_superseded = query.pop("include_superseded", False)
        if index == "ckg" and include_superseded:
            superseded = CkgReadService._search_superseded(project_id, query)
            if superseded:
                return superseded
        try:
            return searcher.search(index, query)
        except KeyError:
            return {
                "type": "error",
                "message": f"Index '{index}' not found. Available: {searcher.list_indexes()}",
            }

    @staticmethod
    def search_many(searcher: Any, payload: dict) -> dict:
        """Federated search across multiple indexes."""
        return searcher.search_many(_with_compact_defaults(payload))

    @staticmethod
    def _search_superseded(project_id: str, query: dict) -> dict | None:
        """Search with include_superseded=True (fallback)."""
        try:
            ckg, _ = _resolve_ckg(project_id)
            if not ckg:
                return None
            nodes = ckg.query({**query, "include_superseded": True})
            return {"type": "search", "index": "ckg", "hits": len(nodes), "results": nodes}
        except Exception:
            return None
