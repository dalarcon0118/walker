"""application/evolutionary_document_model.py — Evolutionary Document Model (EDM).

Orquestador del ciclo de vida de las Specs en el workflow.
No es un orquestador nuevo; se integra con SideEffectEngine existente.

Responsabilidades:
  - Validar transiciones de estado (draft → specified → approved → ...)
  - Aplicar transformaciones de campos por nodo
  - Ejecutar memory_ops (Delta, CKG, CPG, Trace)
  - Registrar historial de transformaciones
  - Ejecutar compensaciones en orden inverso si falla
"""
from __future__ import annotations

import logging
from typing import Any

from features.delta_tracking.application.memory_orchestrator import MemoryOrchestrator
from features.delta_tracking.domain.entities import Spec
from features.delta_tracking.domain.services.spec_service import (
    SPEC_STATUS_FLOW,
    SpecService,
    SpecStatusUpdater,
)

logger = logging.getLogger(__name__)


class EvolutionaryDocumentModel:
    """Modelo de documento evolutivo para Specs.

    Recibe la Spec actual, aplica transformaciones según el nodo,
    y persiste el documento completo en DeltaGraph.
    """

    def __init__(self, chain_dir: str) -> None:
        self.chain_dir = chain_dir

    def transform(
        self,
        spec: Spec,
        node_key: str,
        fields: dict,
        memory_orchestrator: MemoryOrchestrator | None = None,
        context: dict | None = None,
    ) -> tuple[Spec, list[dict]]:
        """Aplica transformación de Spec para el nodo actual.

        Args:
            spec: Spec actual.
            node_key: Nodo del workflow (ej: SPRINT_PLANNING.TASK_SPEC).
            fields: Campos a aplicar en este nodo.

        Returns:
            Tupla (spec_transformada, resultados_de_memory_ops).
        """
        new_status = self._status_for_node(node_key)
        if not new_status:
            return spec, []

        if not self._can_transition(spec.status, new_status):
            logger.warning(
                "EDM: transición inválida %s -> %s en %s",
                spec.status, new_status, node_key,
            )
            return spec, []

        merged = SpecService.merge(spec, {"status": new_status, **fields})
        memory_ops = self._memory_ops_for_status(new_status, merged)

        if memory_orchestrator:
            results = memory_orchestrator.execute_phase(spec.spec_id, memory_ops, context)
            return merged, results

        return merged, memory_ops

    def get_transformation_map(self, spec: Spec) -> dict:
        """Retorna el mapa de transformación de la Spec."""
        return spec.to_dict().get("transformation_map", {})

    def _status_for_node(self, node_key: str) -> str | None:
        mapping = {
            "SPRINT_PLANNING.PLANNING": "draft",
            "SPRINT_PLANNING.PLAN_APPROVAL": "draft",
            "SPRINT_PLANNING.TASK_SPEC": "specified",
            "SPRINT_PLANNING.TASK_REVIEW": "approved",
            "SPRINT_PLANNING.DESIGN_GATHER": "in_progress",
            "SPRINT_PLANNING.ANALYZE_GATHER": "in_progress",
            "SPRINT_PLANNING.IMPLEMENT_USER_GATE": "in_progress",
            "SPRINT_PLANNING.IMPLEMENT_WORK": "in_progress",
            "SPRINT_PLANNING.CODE_REVIEW_SCAN": "in_review",
            "SPRINT_PLANNING.CODE_REVIEW_RESULT": "in_review",
            "SPRINT_PLANNING.REPORT_GATE": "completed",
            "SPRINT_PLANNING.VERIFY_USER_GATE": "completed",
            "SPRINT_PLANNING.QA_RUN": "completed",
            "SPRINT_PLANNING.PO_ACCEPT": "completed",
            "DESIGN.GATHER": "in_progress",
            "DESIGN.CKG_SAVE": "in_progress",
            "ANALYZE.GATHER": "in_progress",
            "ANALYZE.ANALYZE": "in_progress",
            "ANALYZE.KNOWLEDGE_CAPTURE": "in_progress",
            "IMPLEMENT.USER_GATE": "in_progress",
            "IMPLEMENT.WORK": "in_progress",
            "IMPLEMENT.CODE_REVIEW_SCAN": "in_review",
            "IMPLEMENT.CODE_REVIEW_RESULT": "in_review",
            "IMPLEMENT.KNOWLEDGE_UPDATE": "in_progress",
            "IMPLEMENT.REPORT_GATE": "completed",
            "VERIFY.USER_GATE": "completed",
            "VERIFY.QA_RUN": "completed",
            "VERIFY.PO_ACCEPT": "completed",
        }
        if node_key in mapping:
            return mapping[node_key]
        parts = node_key.split(".", 1)
        if len(parts) == 2:
            return mapping.get(parts[1])
        return None

    def _can_transition(self, current: str, target: str) -> bool:
        try:
            current_idx = SPEC_STATUS_FLOW.index(current)
            target_idx = SPEC_STATUS_FLOW.index(target)
            return target_idx >= current_idx
        except ValueError:
            return target == "draft"

    def _memory_ops_for_status(self, status: str, spec: Spec) -> list[dict]:
        ops = [{"target": "delta", "op": "upsert_spec", "data": {"status": status}}]
        if status == "specified":
            ops.append({"target": "ckg", "op": "save", "data": {"type": "problem", "id": spec.spec_id}})
            ops.append({"target": "cpg", "op": "index", "data": {"files": spec.files}})
            ops.append({"target": "trace", "op": "emit", "event": "spec_specified"})
        elif status == "approved":
            ops.append({"target": "ckg", "op": "save", "data": {"type": "decision", "id": spec.spec_id}})
            ops.append({"target": "trace", "op": "emit", "event": "decision_approved"})
        elif status == "in_progress":
            ops.append({"target": "trace", "op": "emit", "event": "impl_started"})
        elif status == "in_review":
            ops.append({"target": "cpg", "op": "scope", "data": {"scope": "code_review"}})
            ops.append({"target": "trace", "op": "emit", "event": "code_review_scanned"})
        elif status == "completed":
            ops.append({"target": "ckg", "op": "save_to_ckg"})
            ops.append({"target": "trace", "op": "emit", "event": "spec_completed"})
        return ops
