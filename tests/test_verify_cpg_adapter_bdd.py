"""BDD: `verify_predicates` no muere cuando el contexto no trae `cpg_adapter`.

Medido 2026-11-18: la caminata en vivo murió en VERIFY.PREDICATE_CHECK con
`AttributeError: 'types.SimpleNamespace' object has no attribute 'cpg_adapter'`.
El handler documentaba "si ctx.cpg_adapter está disponible" (capability
opcional) pero accedía al atributo directo. El motor además nunca lo inyectaba:
el adapter vivía en el WorkflowContext y no llegaba al ExecutionContext de las
acciones.
"""
from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.tasks.handlers.verify import verify_predicates
from fast_mcp.features.workflow.application.bundle import EngineBundle


def _predicate(path):
    return {"type": "file_exists", "path": str(path)}


def test_verify_predicates_sin_cpg_adapter_no_rompe(tmp_path):
    target = tmp_path / "modulo.py"
    target.write_text("x = 1\n", encoding="utf-8")
    ctx = SimpleNamespace(chain_dir=str(tmp_path), task_id="T1")  # sin cpg_adapter

    result = verify_predicates(
        ctx,
        {"predicates": [_predicate(target)], "project_root": str(tmp_path)},
    )

    assert result.ok is True, result.error
    assert result.data["all_satisfied"] is True
    assert result.data["inspect_codebase"] if "inspect_codebase" in result.data else True


def test_verify_predicates_usa_el_adapter_si_existe(tmp_path):
    class FakeAdapter:
        def imports(self, file=""):
            return {"hallucinations": 0, "file": file}

    target = tmp_path / "modulo.py"
    target.write_text("x = 1\n", encoding="utf-8")
    ctx = SimpleNamespace(chain_dir=str(tmp_path), task_id="T1", cpg_adapter=FakeAdapter())

    result = verify_predicates(
        ctx,
        {
            # Sin predicados, el gate devuelve failure ANTES de llegar al
            # adapter (G-W6): el caso real siempre trae predicados de la spec.
            "predicates": [_predicate(target)],
            "project_root": str(tmp_path),
            "inspect_codebase_action": "imports",
            "file": "modulo.py",
        },
    )

    assert result.data["inspect_codebase"]["hallucinations"] == 0
    assert result.data["inspect_codebase"]["file"] == "modulo.py"


def test_bundle_lleva_el_cpg_adapter():
    adapter = object()
    bundle = EngineBundle(
        workflow_store=None,
        workflow_io=None,
        tracer=None,
        side_effects=None,
        chain_dir="/tmp/x",
        cpg_adapter=adapter,
    )

    assert bundle.cpg_adapter is adapter


def test_bundle_sin_adapter_queda_en_none():
    bundle = EngineBundle(
        workflow_store=None,
        workflow_io=None,
        tracer=None,
        side_effects=None,
        chain_dir="/tmp/x",
    )

    assert bundle.cpg_adapter is None
