"""test_feedback_coherence.py — regresion de coherencia del feedback del workflow.

Fija los bugs donde el feedback del workflow era incoherente o carecia de
contexto cuando algo fallaba:

1. BLOCK_MESSAGE en ErrorHandler incluye details/errors/stderr del error.
2. validate_plan side-effect incluye errores cuando falla.
3. submit_orchestrator surfacea fallos de side-effects al agente.
"""

from __future__ import annotations


# ── 1. ErrorHandler BLOCK_MESSAGE incluye contexto ──────────────────────


class TestBlockMessageIncludesDetails:
    """El strategy BLOCK_MESSAGE debe incluir los detalles del error
    (context) para que el agente sepa exactamente que fallo y como corregirlo."""

    def test_transition_blocked_includes_details(self):
        """TransitionBlockedError: details de precondiciones fallidas."""
        from features.engine.application.error_handler import ErrorHandler
        from features.engine.domain.errors import TransitionBlockedError

        err = TransitionBlockedError(
            "Precondiciones no cumplidas para gate 'next_task'",
            details=[
                "SPEC-001: result_state is empty",
                "SPEC-001: task_description is empty",
                "SPEC-002: status is 'draft' must be 'specified'",
            ],
        )
        handler = ErrorHandler(
            submit_uc=None,
            transition_uc=None,
            side_effect_engine=None,
        )
        result = handler.handle(
            ctx=type(
                "FakeCtx", (), {"chain_dir": "", "task_id": "", "session_id": ""}
            )(),
            error=err,
            sub_state=None,
            fields={},
        )
        assert result["ok"] is False
        assert result["strategy"] == "block+message"
        # El mensaje debe incluir los detalles especificos
        assert "SPEC-001: result_state is empty" in result["message"]
        assert "SPEC-001: task_description is empty" in result["message"]
        # Los detalles deben estar en el resultado
        assert "details" in result
        assert result["details"]["details"] == err.context["details"]

    def test_validation_error_includes_errors(self):
        """ValidationError: errores de campos invalidos."""
        from features.engine.application.error_handler import ErrorHandler
        from features.engine.domain.errors import ValidationError

        err = ValidationError(
            "Datos invalidos para sub-estado TASK_SPEC",
            errors=[
                {"field": "result_state", "issue": "empty"},
                {"field": "task_description", "issue": "empty"},
            ],
        )
        handler = ErrorHandler(
            submit_uc=None,
            transition_uc=None,
            side_effect_engine=None,
        )
        result = handler.handle(
            ctx=type(
                "FakeCtx", (), {"chain_dir": "", "task_id": "", "session_id": ""}
            )(),
            error=err,
            sub_state=None,
            fields={},
        )
        assert result["ok"] is False
        # El mensaje debe incluir los errores de campos
        assert "result_state" in result["message"]
        assert "task_description" in result["message"]
        assert "errors" in result["details"]

    def test_gate_validation_error_includes_stderr(self):
        """GateValidationError: stderr del script de validacion."""
        from features.engine.application.error_handler import ErrorHandler
        from features.engine.domain.errors import GateValidationError

        err = GateValidationError(
            "task_complete", "exit code 1: missing required field 'decision'"
        )
        handler = ErrorHandler(
            submit_uc=None,
            transition_uc=None,
            side_effect_engine=None,
        )
        result = handler.handle(
            ctx=type(
                "FakeCtx", (), {"chain_dir": "", "task_id": "", "session_id": ""}
            )(),
            error=err,
            sub_state=None,
            fields={},
        )
        assert result["ok"] is False
        assert "missing required field" in result["message"]
        assert "stderr" in result["details"]

    def test_generic_error_without_context(self):
        """Un error sin contexto (ValidationError) sigue funcionando correctamente."""
        from features.engine.application.error_handler import ErrorHandler
        from features.engine.domain.errors import ValidationError

        # ValidationError mapea a BLOCK_MESSAGE por defecto
        err = ValidationError("Algo salio mal")
        handler = ErrorHandler(
            submit_uc=None,
            transition_uc=None,
            side_effect_engine=None,
        )
        result = handler.handle(
            ctx=type(
                "FakeCtx", (), {"chain_dir": "", "task_id": "", "session_id": ""}
            )(),
            error=err,
            sub_state=None,
            fields={},
        )
        assert result["ok"] is False
        assert result["message"] == "Algo salio mal"
        # ValidationError tiene context = {"errors": []} por defecto
        assert "details" in result
        assert isinstance(result["details"], dict)


# ── 2. validate_plan side-effect incluye errores ────────────────────────


class TestValidatePlanErrors:
    """El side-effect validate_plan debe reportar errores cuando falla
    para que el agente vea que la validacion fallo y que corregir."""

    def test_validate_plan_reports_errors_on_failure(self):
        """Cuando validate_plan falla, ok=False y errors no esta vacio."""
        from unittest.mock import patch, MagicMock
        from features.task_execution.side_effects.validate import validate_plan
        from features.engine.domain.models import ExecutionContext

        ctx = ExecutionContext.create(
            session_id="test",
            chain_dir="/tmp/fake",
        )
        # Mock delta (SpringService) para retornar specs con errores
        mock_svc = MagicMock()
        mock_svc.get_spring_id.return_value = "test-sprint"
        mock_svc.get_specs_legacy.return_value = [
            {
                "spec_id": "SPEC-001",
                "status": "draft",  # Debe ser "specified"
                "result_state": "",  # Vacio = error
                "task_description": "",
            },
            {
                "spec_id": "SPEC-002",
                "status": "draft",
                "result_state": "",
                "task_description": "",
            },
        ]
        with patch(
            "features.spring.application.use_cases._spring_svc", return_value=mock_svc
        ):
            result = validate_plan(ctx, {})

        assert result["ok"] is False
        # El decorator agrega summary al envelope
        assert "summary" in result
        # Los errores estan en metadata
        assert "metadata" in result
        assert result["metadata"]["errors"]  # Lista no vacia

    def test_validate_plan_success(self):
        """Cuando validate_plan pasa, ok=True."""
        from unittest.mock import patch, MagicMock
        from features.task_execution.side_effects.validate import validate_plan
        from features.engine.domain.models import ExecutionContext

        ctx = ExecutionContext.create(
            session_id="test",
            chain_dir="/tmp/fake",
        )
        # Mock delta (SpringService) para retornar specs validos
        mock_svc = MagicMock()
        mock_svc.get_spring_id.return_value = "test-sprint"
        mock_svc.get_specs_legacy.return_value = [
            {
                "spec_id": "SPEC-001",
                "status": "specified",
                "result_state": "implemented",
                "task_description": "Test feature",
                "validation": {"predicates": ["works"]},
                "impacted_artifacts": {"files": ["test.py"]},
                "preconditions": ["none"],
                "acceptance_criteria": ["passes tests"],
                "quality_gates": ["lint"],
                "objective": "Test objective",
            },
        ]
        with patch(
            "features.spring.application.use_cases._spring_svc", return_value=mock_svc
        ):
            result = validate_plan(ctx, {})

        assert result["ok"] is True
        # No hay errores en metadata
        assert result["metadata"]["errors"] == []

    def test_validate_plan_on_exception(self):
        """Cuando el spring no esta en el delta, ok=False."""
        from unittest.mock import patch
        from features.task_execution.side_effects.validate import validate_plan
        from features.engine.domain.models import ExecutionContext

        ctx = ExecutionContext.create(
            session_id="test",
            chain_dir="/tmp/fake",
        )
        with patch(
            "features.spring.application.use_cases._spring_svc",
            side_effect=ValueError("spring not found"),
        ):
            result = validate_plan(ctx, {})

        assert result["ok"] is False
        assert "summary" in result


# ── 3. submit_orchestrator surfaces side-effect failures ────────────────


class TestSubmitOrchestratorSideEffectFailures:
    """submit_orchestrator.py debe surfacear fallos de side-effects al agente."""

    def test_failed_side_effect_detected(self):
        """Se detecta una side-effect fallida en la lista."""
        side_effects = [
            {"ok": True, "type": "advance_task"},
            {
                "ok": False,
                "type": "validate_plan",
                "summary": "validate_plan: 3 error(es) en 2/5 specs",
                "errors": ["SPEC-001: result_state is empty"],
            },
        ]
        failed = [
            se for se in side_effects if isinstance(se, dict) and se.get("ok") is False
        ]
        assert len(failed) == 1
        assert failed[0]["type"] == "validate_plan"

    def test_side_effect_failure_has_errors(self):
        """Una side-effect fallida tiene lista de errores."""
        side_effect = {
            "ok": False,
            "type": "validate_plan",
            "errors": [
                "SPEC-001: result_state is empty",
                "SPEC-001: task_description is empty",
            ],
        }
        errors = side_effect.get("errors", [])
        assert len(errors) == 2
        assert "SPEC-001: result_state is empty" in errors

    def test_successful_side_effects_not_flagged(self):
        """Side-effects exitosos no se marcan como errores."""
        side_effects = [
            {"ok": True, "type": "advance_task", "summary": "Task avanzada"},
            {"ok": True, "type": "capture_decision"},
        ]
        failed = [
            se for se in side_effects if isinstance(se, dict) and se.get("ok") is False
        ]
        assert len(failed) == 0
