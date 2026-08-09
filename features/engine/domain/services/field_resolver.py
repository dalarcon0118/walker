"""domain/services/field_resolver.py — Resolución de campos requeridos.

Deriva los campos requeridos de un sub-estado desde su JSON Schema.
Principio: el schema es la autoridad, no el campo legacy required_fields.
"""
from __future__ import annotations

from features.engine.domain.entities import SubState


class RequiredFieldResolver:
    """Deriva los campos requeridos de un sub-estado desde su JSON Schema.

    PRINCIPIO (schema como autoridad): el campo legacy `required_fields`
    de los node.json v3 esta VACIO en todos los sub-estados (GAP-3/GAP-4).
    La autoridad real de que campos se requieren es el JSON Schema:
      - input_schema.required  → campos que el agente debe PROVEER (submit)
      - output_schema.required → campos que el sub-estado debe PRODUCIR

    Este resolver deriva required_fields desde el schema, eliminando la
    dependencia del campo legacy. PURE: sin IO, no muta nada.

    Ejemplos:
      PLAN_APPROVAL.input_schema.required=['decision'] → ['decision']
      ACTIVE.input_schema.required=['intent']          → ['intent']
      KNOWLEDGE_CAPTURE.output_schema.required=['captured'] → ['captured']
    """

    @staticmethod
    def derive(sub_state: SubState) -> list[str]:
        """Deriva los campos requeridos del sub-estado.

        Prioridad:
          1. input_schema.required (campos a proveer en submit)
          2. output_schema.required (campos que produce)
          3. campo legacy required_fields (fallback si no hay schema)
        """
        if sub_state is None:
            return []
        # 1. input_schema.required (JSON Schema como autoridad)
        in_schema = sub_state.input_schema or {}
        in_required = in_schema.get("required", []) if isinstance(in_schema, dict) else []
        if in_required:
            return list(in_required)
        # 2. output_schema.required
        out_schema = sub_state.output_schema or {}
        out_required = (
            out_schema.get("required", []) if isinstance(out_schema, dict) else []
        )
        if out_required:
            return list(out_required)
        # 3. Fallback: campo legacy required_fields
        if sub_state.required_fields:
            return list(sub_state.required_fields)
        return []

    @staticmethod
    def resolve_required(sub_state: SubState) -> list[str]:
        """Alias explicito: deriva los required para validar un submit."""
        return RequiredFieldResolver.derive(sub_state)


__all__ = ["RequiredFieldResolver"]
