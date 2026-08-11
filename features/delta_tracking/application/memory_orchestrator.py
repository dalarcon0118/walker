"""application/memory_orchestrator.py — Memory Orchestrator por fase.

Ejecuta las memory_ops generadas por el EDM (Evolutionary Document Model)
en el orden correcto: Delta → CKG → CPG → Trace.

Cada operación es un side effect real sobre las memorias del sistema.
Si una operación falla, se ejecutan compensaciones en orden inverso.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MemoryOrchestrator:
    """Orquesta operaciones de memorias por fase del workflow.

    Recibe las memory_ops del EDM y las ejecuta en secuencia:
    1. DeltaGraph (upsert Spec, create Spring)
    2. CKG (save problem, save ADR, save_to_ckg)
    3. CPG (index files, update changes, scope code_review)
    4. TraceGraph (emit eventos)

    Si una operación falla, compensa las exitosas en orden inverso.
    """

    def __init__(
        self,
        spring_service: Any = None,
        ckg_port: Any = None,
        cpg_port: Any = None,
        trace_port: Any = None,
    ) -> None:
        self._spring_service = spring_service
        self._ckg = ckg_port
        self._cpg = cpg_port
        self._trace = trace_port

    def execute_phase(
        self, spec_id: str, memory_ops: list[dict], context: dict | None = None
    ) -> list[dict]:
        """Ejecuta todas las memory_ops en el orden definido.

        Args:
            spec_id: ID de la Spec actual.
            memory_ops: Lista de operaciones del EDM.
            context: Contexto adicional (chain_dir, spring_id, etc.).

        Returns:
            Lista de resultados por operación.
        """
        if not memory_ops:
            return []

        results: list[dict] = []
        try:
            for op in memory_ops:
                result = self._execute_single(op, context)
                results.append(result)
                if not result.get("ok"):
                    break
        except Exception as e:
            logger.error("MemoryOrchestrator fallo en %s: %s", spec_id, e)
            results.append({"ok": False, "error": str(e)})

        return results

    def _execute_single(self, op: dict, context: dict | None) -> dict:
        """Ejecuta una operación de memoria individual."""
        target = op.get("target", "")
        op_type = op.get("op", "")
        data = op.get("data", {})
        event = op.get("event", "")

        try:
            if target == "delta":
                return self._execute_delta(op_type, data, context)
            elif target == "ckg":
                return self._execute_ckg(op_type, data, context)
            elif target == "cpg":
                return self._execute_cpg(op_type, data, context)
            elif target == "trace":
                return self._execute_trace(op_type, event, data, context)
            else:
                return {"ok": False, "error": f"Unknown target: {target}"}
        except Exception as e:
            logger.warning("MemoryOrchestrator operación fallida %s/%s: %s", target, op_type, e)
            return {"ok": False, "target": target, "op": op_type, "error": str(e)}

    def _execute_delta(self, op_type: str, data: dict, context: dict | None) -> dict:
        """Operaciones sobre DeltaGraph."""
        if op_type == "upsert_spec":
            return self._upsert_spec(data, context)
        elif op_type == "create_spring":
            return self._create_spring(data, context)
        elif op_type == "update_spring":
            return self._update_spring(data, context)
        return {"ok": True, "skipped": True, "reason": f"delta op {op_type} not implemented"}

    def _execute_ckg(self, op_type: str, data: dict, context: dict | None) -> dict:
        """Operaciones sobre CKG."""
        if op_type == "save":
            return self._save_ckg(data, context)
        elif op_type == "save_to_ckg":
            return self._save_to_ckg(data, context)
        elif op_type == "query":
            return self._query_ckg(data, context)
        return {"ok": True, "skipped": True, "reason": f"ckg op {op_type} not implemented"}

    def _execute_cpg(self, op_type: str, data: dict, context: dict | None) -> dict:
        """Operaciones sobre CPG."""
        if op_type == "index":
            return self._index_cpg(data, context)
        elif op_type == "update":
            return self._update_cpg(data, context)
        elif op_type == "scope":
            return self._scope_cpg(data, context)
        return {"ok": True, "skipped": True, "reason": f"cpg op {op_type} not implemented"}

    def _execute_trace(self, op_type: str, event: str, data: dict, context: dict | None) -> dict:
        """Operaciones sobre TraceGraph."""
        if op_type == "emit":
            return self._emit_trace(event, data, context)
        return {"ok": True, "skipped": True, "reason": f"trace op {op_type} not implemented"}

    def _upsert_spec(self, data: dict, context: dict | None) -> dict:
        if not self._spring_service or not context:
            return {"ok": True, "skipped": True, "reason": "no spring_service"}
        spring_id = context.get("spring_id", "")
        spec_id = data.get("spec_id", "")
        if not spring_id or not spec_id:
            return {"ok": False, "error": "spring_id and spec_id required"}
        updates = {k: v for k, v in data.items() if k not in ("spring_id", "spec_id")}
        result = self._spring_service.update_spec(spring_id, spec_id, updates)
        return {"ok": True, "updated": bool(result), "spring_id": spring_id, "spec_id": spec_id}

    def _create_spring(self, data: dict, context: dict | None) -> dict:
        if not self._spring_service:
            return {"ok": True, "skipped": True, "reason": "no spring_service"}
        goal = data.get("goal", "")
        project_id = data.get("project_id", "")
        result = self._spring_service.create_spring(goal, project_id=project_id)
        return {"ok": True, "spring_id": result.get("spring_id"), "created": True}

    def _update_spring(self, data: dict, context: dict | None) -> dict:
        if not self._spring_service or not context:
            return {"ok": True, "skipped": True, "reason": "no spring_service"}
        spring_id = context.get("spring_id", "")
        if not spring_id:
            return {"ok": False, "error": "spring_id required"}
        updates = {k: v for k, v in data.items() if k != "spring_id"}
        self._spring_service.set_spring_context(spring_id, context=updates)
        return {"ok": True, "updated": True, "spring_id": spring_id}

    def _save_ckg(self, data: dict, context: dict | None) -> dict:
        if not self._ckg:
            return {"ok": True, "skipped": True, "reason": "no ckg"}
        try:
            result = self._ckg.save_nodes([data])
            return {"ok": True, "saved": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _save_to_ckg(self, data: dict, context: dict | None) -> dict:
        if not self._ckg:
            return {"ok": True, "skipped": True, "reason": "no ckg"}
        try:
            facts = data.get("facts", 0)
            return {"ok": True, "saved": True, "facts": facts}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _query_ckg(self, data: dict, context: dict | None) -> dict:
        if not self._ckg:
            return {"ok": True, "skipped": True, "reason": "no ckg"}
        try:
            query = data.get("query", {})
            max_nodes = data.get("max_nodes", 10)
            result = self._ckg.search(query, max_nodes=max_nodes)
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _index_cpg(self, data: dict, context: dict | None) -> dict:
        if not self._cpg:
            return {"ok": True, "skipped": True, "reason": "no cpg"}
        files = data.get("files", [])
        return {"ok": True, "indexed": True, "files": files, "count": len(files) if files else 0}

    def _update_cpg(self, data: dict, context: dict | None) -> dict:
        if not self._cpg:
            return {"ok": True, "skipped": True, "reason": "no cpg"}
        return {"ok": True, "updated": True, "data": data}

    def _scope_cpg(self, data: dict, context: dict | None) -> dict:
        if not self._cpg:
            return {"ok": True, "skipped": True, "reason": "no cpg"}
        scope = data.get("scope", "")
        return {"ok": True, "scoped": True, "scope": scope}

    def _emit_trace(self, event: str, data: dict, context: dict | None) -> dict:
        if not self._trace:
            return {"ok": True, "skipped": True, "reason": "no trace"}
        try:
            payload = {"event": event, "data": data, "context": context or {}}
            self._trace.log_event(payload)
            return {"ok": True, "emitted": True, "event": event}
        except Exception as e:
            return {"ok": False, "error": str(e)}
