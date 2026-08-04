"""test_required_field_resolver.py — regresion de RequiredFieldResolver (SPEC-002).

Fija GAP-3/GAP-4: los required_fields se derivan del JSON Schema
(input_schema.required / output_schema.required) en vez del campo legacy
required_fields vacio de los node.json v3.
"""
from __future__ import annotations

from features.engine.domain.services import RequiredFieldResolver


def _sub_state(input_schema=None, output_schema=None, legacy=None):
    """Construye un SubState minimo para testear el resolver."""
    from features.engine.domain.models import SubState

    return SubState(
        key="TEST",
        input_schema=input_schema or {},
        output_schema=output_schema or {},
        required_fields=legacy or [],
    )


def test_derive_priority_input_schema():
    """input_schema.required tiene prioridad."""
    ss = _sub_state(
        input_schema={"type": "object", "required": ["decision"]},
        output_schema={"required": ["result_state"]},
        legacy=["goal"],
    )
    assert RequiredFieldResolver.derive(ss) == ["decision"]


def test_derive_fallback_output_schema():
    """Sin input_schema.required, usa output_schema.required."""
    ss = _sub_state(
        input_schema={"type": "object"},
        output_schema={"required": ["captured"]},
    )
    assert RequiredFieldResolver.derive(ss) == ["captured"]


def test_derive_fallback_legacy():
    """Sin schemas, usa el campo legacy required_fields."""
    ss = _sub_state(legacy=["goal", "tasks"])
    assert RequiredFieldResolver.derive(ss) == ["goal", "tasks"]


def test_derive_empty():
    """Sin schemas ni legacy, retorna vacio."""
    assert RequiredFieldResolver.derive(_sub_state()) == []


def test_derive_none_sub_state():
    """None retorna vacio (no lanza)."""
    assert RequiredFieldResolver.derive(None) == []


def test_planning_derives_goal_tasks():
    """SPRINT.PLANNING requiere goal y tasks (desde input_schema)."""
    ss = _sub_state(
        input_schema={
            "type": "object",
            "required": ["goal", "tasks"],
            "properties": {"goal": {"type": "string"}, "tasks": {"type": "array"}},
        }
    )
    assert set(RequiredFieldResolver.derive(ss)) == {"goal", "tasks"}


def test_analyze_derives_6to3():
    """ANALYZE.ANALYZE requiere q1/a1/q2/a2/q3/a3."""
    ss = _sub_state(
        input_schema={
            "type": "object",
            "required": ["q1", "a1", "q2", "a2", "q3", "a3"],
        }
    )
    assert set(RequiredFieldResolver.derive(ss)) == {
        "q1", "a1", "q2", "a2", "q3", "a3",
    }


def test_resolve_required_alias():
    """resolve_required es alias de derive."""
    ss = _sub_state(input_schema={"required": ["decision"]})
    assert RequiredFieldResolver.resolve_required(ss) == ["decision"]


def run_all():
    tests = [
        test_derive_priority_input_schema,
        test_derive_fallback_output_schema,
        test_derive_fallback_legacy,
        test_derive_empty,
        test_derive_none_sub_state,
        test_planning_derives_goal_tasks,
        test_analyze_derives_6to3,
        test_resolve_required_alias,
    ]
    for t in tests:
        t()
        print(f"  OK {t.__name__}")


if __name__ == "__main__":
    run_all()
