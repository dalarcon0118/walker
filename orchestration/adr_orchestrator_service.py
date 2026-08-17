"""orchestration/adr_orchestrator_service.py — AdrOrchestratorService.

Cross-feature orchestrator para create_adr (ADR-051 §2.5 G10).

Orquesta 3 features: delta_tracking (spring/spec), cognitive_knowledge (CKG),
project (path resolution). El side-effect create_adr se reduce a un thin
adapter que delega en este servicio.

Regla ADR-051 §2.5: si una task/side-effect orquesta 2+ features, la
orquestación vive en orchestration/ (no es lógica de negocio de un solo
feature). Este servicio NO contiene lógica de negocio — solo coordina
los Application Services de los features.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AdrOrchestratorService:
    """Crea un ADR (nodo de decisión) en CKG desde una Spec aprobada.

    Orquesta:
      - SpringService (delta_tracking): obtiene la spec actual del spring
      - CkgApplicationService (cognitive_knowledge): guarda/consulta el ADR en CKG
      - ProjectService (project): resuelve paths

    El servicio es una thin orchestrator: recibe los servicios ya construidos
    y los coordina. No contiene lógica de negocio propiaria.
    """

    @staticmethod
    def create_adr_from_spec(
        chain_dir: str,
        approval_type: str = "standard",
        spring_svc: Any = None,
        ckg: Any = None,
        project_service: Any = None,
    ) -> dict[str, Any]:
        """Crea un ADR en CKG a partir de la spec actual del spring.

        Args:
            chain_dir: Directorio del sprint.
            approval_type: Tipo de aprobación (debe ser "decision" para crear ADR).
            spring_svc: SpringService ya construido (inyectado para testabilidad).
            ckg: CodeKnowledgeGraph ya construido (inyectado para testabilidad).
            project_service: ProjectService (clase con resolve_paths).

        Result pattern (convention-001): el core @safe nunca lanza.
        """
        if not chain_dir:
            return {"ok": False, "error": "chain_dir not available"}

        if not approval_type:
            approval_type = "standard"
        if approval_type != "decision":
            return {
                "ok": True,
                "skipped": True,
                "reason": f"approval_type={approval_type}, no es decision",
            }

        try:
            if spring_svc is None:
                from features.delta_tracking import spring_service as _spring_service
                spring_svc = _spring_service(chain_dir=chain_dir)
            if spring_svc is None:
                return {"ok": False, "error": "Spring service not available"}

            spring_id = spring_svc.get_spring_id(chain_dir)
            specs = spring_svc.get_specs_legacy(spring_id)
            if not specs:
                return {"ok": False, "error": "no current spec found"}

            current_spec = None
            if isinstance(specs, list):
                current_spec = specs[-1]
            elif isinstance(specs, dict):
                current_spec = next(iter(specs.values()))

            if not current_spec:
                return {"ok": False, "error": "Spec not found in spring"}

            spec_dict = current_spec if isinstance(current_spec, dict) else current_spec.to_dict()
            spec_id = spec_dict.get("spec_id", "")
            task_id = spec_dict.get("task_id", spec_id)
            title = spec_dict.get("task_description", spec_dict.get("result_state", task_id))[:120]

            if ckg is None:
                from features.project import ProjectService as _ProjectService
                ps = project_service or _ProjectService
                paths = ps.resolve_paths(chain_dir)
                project_root = str(paths.project_root)
                from features.cognitive_knowledge import ckg_service
                ckg = ckg_service(project_root)

            existing_adrs = ckg.query({"type": "decision", "metadata.spec_id": spec_id})
            if existing_adrs:
                adr_id = existing_adrs[0].get("_id", "")
                related = [adr_id]
                existing_related = spec_dict.get("related_decisions", [])
                if isinstance(existing_related, list):
                    related = existing_related + [adr_id] if adr_id not in existing_related else existing_related
                updates = {
                    "related_decisions": related,
                    "generated_from": f"SPEC:{spec_id}",
                }
                spring_svc.update_spec(spring_id, spec_id, updates)
                return {
                    "ok": True,
                    "type": "create_adr",
                    "spec_id": spec_id,
                    "adr_id": adr_id,
                    "related_decisions": related,
                    "generated_from": f"SPEC:{spec_id}",
                    "idempotent": True,
                }

            adr_id = ckg._next_id("ADR")
            adr_node = {
                "type": "decision",
                "label": f"ADR: {title}",
                "statement": f"ADR-{adr_id}: {title}",
                "description": (
                    f"Decision arquitectonica generada desde Spec {spec_id} "
                    f"(task {task_id}). Aprobada en TASK_REVIEW."
                ),
                "metadata": {
                    "task_id": task_id,
                    "spec_id": spec_id,
                    "generated_from": f"SPEC:{spec_id}",
                    "source": f"session:{getattr(spring_svc, '_repo', None)}",
                    "approval_type": "decision",
                },
                "supersedes": [],
            }

            save_result = ckg.save_nodes([adr_node])
            node_ids = save_result.get("node_ids", [])
            adr_id = node_ids[0] if node_ids else adr_id

            related = [adr_id]
            existing_related = spec_dict.get("related_decisions", [])
            if isinstance(existing_related, list):
                related = existing_related + [adr_id]

            generated_from = f"SPEC:{spec_id}"
            updates = {
                "related_decisions": related,
                "generated_from": generated_from,
            }
            spring_svc.update_spec(spring_id, spec_id, updates)

            return {
                "ok": True,
                "type": "create_adr",
                "spec_id": spec_id,
                "adr_id": adr_id,
                "related_decisions": related,
                "generated_from": generated_from,
            }
        except Exception as e:
            logger.warning(
                "adr_orchestrator: create_adr_from_spec fallo (%s: %s)",
                type(e).__name__, e,
            )
            return {"ok": False, "error": str(e)}
