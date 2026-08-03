"""
Test: Sistema de prompts para USER GATES.

Valida:
  1. PromptManager.compose carga el template real (fix del bug de rendering)
  2. resolve_prompt inyecta extra_data en baggage para compose()
  3. ContextComposer._resolve_placeholders resuelve desde collect_all
  4. StateProvider/QueryManager exponen last_step_fields (QUÉ SUCEDIÓ)
  5. Motor de evidencia (features/prompts/evidence.py)
  6. Gate briefing estándar (features/prompts/gate_briefing.py)
  7. what_happened_from_state sintetiza Q&A del ANALYZE
"""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace


def _setup_project(tmpdir: str) -> tuple[str, str]:
    """Crea un proyecto walker válido y retorna (chain_dir, project_root).

    Estructura:
      tmpdir/proj/data/project.json            <- project root (data/project.json)
      tmpdir/proj/workflow.v3/prompts/         <- templates canónicos
      tmpdir/proj/data/spring/test-sprint/     <- chain_dir
    """
    project_root = os.path.join(tmpdir, "proj")
    chain_dir = os.path.join(project_root, "data", "spring", "test-sprint")
    os.makedirs(os.path.join(project_root, "data"), exist_ok=True)
    os.makedirs(chain_dir, exist_ok=True)
    # project.json mínimo para que resolve_project_root lo encuentre
    project_json = {
        "project": {
            "meta": {"project_id": "test-project", "version": "2.0"},
            "identity": {"name": "test-project"},
            "location": {"repo_path": project_root},
        }
    }
    with open(os.path.join(project_root, "data", "project.json"), "w") as f:
        json.dump(project_json, f, ensure_ascii=False)
    return chain_dir, project_root


def _write_template(project_root: str, node: str, prompt_name: str, template: str) -> str:
    """Escribe un template canónico en workflow.v3/prompts/<node>/<prompt>.prompt.json."""
    prompts_dir = os.path.join(project_root, "workflow.v3", "prompts", node)
    os.makedirs(prompts_dir, exist_ok=True)
    path = os.path.join(prompts_dir, f"{prompt_name}.prompt.json")
    with open(path, "w") as f:
        json.dump({"id": f"{node}/{prompt_name}", "type": "user_input", "template": template}, f, ensure_ascii=False)
    return path


def test_compose_carga_template_real():
    """PromptManager.compose con un ID debe cargar el template real."""
    from features.prompts.manager import PromptManager

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, project_root = _setup_project(tmpdir)
        pm = PromptManager(chain_dir, project_dir=project_root)

        _write_template(
            project_root, "testnode", "review",
            "📋 QUÉ SUCEDIÓ\n  {what_happened}\n\n➡️ OPCIÓN: {decision}\n",
        )

        ctx = SimpleNamespace(
            chain_dir=chain_dir,
            task_id="TASK-001",
            baggage={"task_id": "TASK-001", "what_happened": "Análisis completado", "decision": "approve"},
        )

        # node_config mínimo
        node_config = {
            "focus": {"role": "revisor"},
            "sources": [{"provider": "state", "config": {}}],
        }
        result = pm.compose(
            node_config=node_config,
            prompt_template="testnode/review",
            ctx=ctx,
            chain_dir=chain_dir,
            extra_data={"what_happened": "Análisis completado", "decision": "approve"},
        )
        assert "QUÉ SUCEDIÓ" in result, "Template no renderizado"
        assert "Análisis completado" in result, "what_happened no resuelto"
        assert "approve" in result, "decisión no resuelta"
        print("  ✅ test_compose_carga_template_real: template real renderizado con datos")


def test_resolve_prompt_inyecta_extra_data():
    """resolve_prompt con context_config debe usar extra_data (Q&A)."""
    from features.engine.application.orchestrator import OrchestrationService

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, project_root = _setup_project(tmpdir)
        svc = OrchestrationService()

        _write_template(
            project_root, "testnode", "review",
            "Q1: {q1}\nA1: {a1}\n",
        )

        context_config = {
            "focus": {"role": "analista"},
            "sources": [{"provider": "state", "config": {"query": {}}}],
        }

        ctx = SimpleNamespace(
            chain_dir=chain_dir,
            task_id="TASK-001",
            session_id="sess",
            baggage={"task_id": "TASK-001"},
        )

        result = svc.resolve_prompt(
            "testnode/review",
            chain_dir,
            "TASK-001",
            context_config=context_config,
            execution_ctx=ctx,
            extra_data={"q1": "¿Qué componentes?", "a1": "A, B, C"},
        )
        assert "¿Qué componentes?" in result, "q1 no resuelto"
        assert "A, B, C" in result, "a1 no resuelto"
        print("  ✅ test_resolve_prompt_inyecta_extra_data: Q&A resueltos")


def test_state_query_expone_last_fields():
    """QueryManager debe exponer last_step_fields del paso anterior."""
    from features.engine.infrastructure.repositories import WorkflowStateRepository, WorkflowState
    from features.engine.domain.models import SubStateData
    from features.engine.context.query_manager import QueryManager

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, _ = _setup_project(tmpdir)
        repo = WorkflowStateRepository()

        # Crear WorkflowState con sub_state_data del ANALYZE (Q&A del paso previo)
        state = WorkflowState(
            current_node="ANALYZE",
            current_sub_state="REVIEW",
            sub_state_data={
                "ANALYZE.ANALYZE": SubStateData(fields={"q1": "¿Qué?", "a1": "Respuesta"}, completed_at="x")
            },
        )
        repo.save(chain_dir, state)

        # Cargado de vuelta: los fields deben persistir
        loaded = repo.load(chain_dir)
        assert loaded.current_node == "ANALYZE", "WorkflowState no persiste current_node"
        entry = loaded.sub_state_data.get("ANALYZE.ANALYZE")
        fields = getattr(entry, "fields", {})
        assert fields.get("q1") == "¿Qué?", "sub_state_data no persiste fields"

        # _query_state (la lógica que alimenta QUÉ SUCEDIÓ) debe exponer last_fields
        qm = QueryManager.__new__(QueryManager)  # saltar init que requiere project_root
        qm._chain_dir = chain_dir
        result = qm._query_state({})
        assert result.get("ok") is True
        lf = result.get("last_fields", {})
        assert lf.get("q1") == "¿Qué?", f"last_fields.q1 = {lf.get('q1')!r}"
        assert result.get("node") == "ANALYZE.REVIEW", f"node = {result.get('node')}"
        print("  ✅ test_state_query_expone_last_fields: last_step_fields expuesto")


def test_evidence_motor():
    """El motor de evidencia genera el reporte Evidence + Meta-Instruction."""
    from features.prompts.evidence import build_evidence_report, format_detail_block

    detail = {
        "method": "shell",
        "command": "npm run compile",
        "exit_code": 1,
        "stdout": "235 errors in 91 files",
    }
    block = format_detail_block(detail)
    assert "npm run compile" in block
    assert "235 errors" in block

    checks = {
        "total": 2,
        "passed": 1,
        "passed_items": [
            {"predicate": "símbolo existe", "detail": {"method": "symbol_exists", "symbol": "X", "file": "a.ts"}}
        ],
        "failed": [
            {"predicate": "compila", "detail": {"method": "command_output", "command": "tsc", "found": False, "stdout": "errores"}, "type": "quality_gate"}
        ],
    }
    report = build_evidence_report(
        "TASK-001", checks,
        acceptance_criteria=[{"criterion": "Compila", "passed": False}],
        quality_gates=[{"gate": "tsc sin errores", "passed": False}],
    )
    assert "EVIDENCIA" in report
    assert "META-INSTRUCTION" in report
    assert "NO CUMPLE" in report
    print("  ✅ test_evidence_motor: reporte de evidencia generado")


def test_gate_briefing_estandar():
    """El briefing estándar renderiza las 3 secciones (QUÉ / POR QUÉ / SIGUIENTE)."""
    from features.prompts.gate_briefing import render_gate_briefing, what_happened_from_state

    wh = what_happened_from_state(
        {"q1": "Q?", "a1": "A1", "q2": "Q2?", "a2": "A2", "q3": "Q3?", "a3": "A3"}
    )
    assert "análisis 6→3" in wh
    assert "A1" in wh

    gates = [
        {"name": "analysis_approved", "target": "IMPLEMENT.USER_GATE", "description": "Aprobar"},
        {"name": "needs_refine", "target": "REFINE.DIAGNOSE", "description": "Refinar"},
    ]
    briefing = render_gate_briefing(
        gate_name="ANALYZE.REVIEW",
        task_id="SPEC-001",
        task_title="Probar módulo",
        what_happened=wh,
        evidence="tsc: 235 errores",
        gates=gates,
    )
    assert "QUÉ SUCEDIÓ" in briefing
    assert "POR QUÉ / EVIDENCIA" in briefing
    assert "TU SIGUIENTE PASO" in briefing
    assert "analysis_approved" in briefing
    assert "Se completó el análisis 6→3" in briefing
    print("  ✅ test_gate_briefing_estandar: briefing 3 secciones renderizado")


def test_what_happened_variants():
    """what_happened_from_state maneja decisión/intent/data."""
    from features.prompts.gate_briefing import what_happened_from_state

    assert "Decisión registrada: approve" in what_happened_from_state({"decision": "approve", "feedback": "ok"})
    assert "Intención del paso: start_task" in what_happened_from_state({"intent": "start_task"})
    assert "task_data:" in what_happened_from_state({"task_data": "contexto"})
    assert "(no hay resultado del paso anterior)" in what_happened_from_state({})
    print("  ✅ test_what_happened_variants: variantes sintetizadas")


def test_prompt_context_builder_what_happened():
    """PromptContextBuilder expone what_happened desde las Q&A del ANALYZE."""
    from features.engine.application.prompt_context_builder import PromptContextBuilder

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, _ = _setup_project(tmpdir)
        # Sin sprint en delta: no debe romper
        ctx = PromptContextBuilder().build(chain_dir, "TASK-001")
        assert "what_happened" in ctx or "task_table" in ctx
        print("  ✅ test_prompt_context_builder_what_happened: builder no rompe")


def test_health_analyzer_sugiere_submit_en_user_gate():
    """En un user gate, el health analyzer debe sugerir SUBMIT (no transition).

    Las gates del grafo (ej. analysis_approved) se disparan con el decision
    que el PO envía por submit; sugerir 'transition <gate>' confunde al PO.
    """
    from features.project.application.use_cases import HealthReportAnalyzer

    health = {
        "checks": {"ckg": "13 nodes", "cpg": "16891 nodes", "files": "up to date"},
        "warnings": [],
        "sprint": {
            "node": "ANALYZE",
            "sub_state": "REVIEW",
            "gates": [
                {"name": "analysis_approved", "target": "IMPLEMENT"},
                {"name": "needs_refine", "target": "REFINE"},
            ],
            "execution_type": "user_input",
            "prompt_id": "analyze/review",
        },
        "project": {"language": "ts"},
    }
    steps = HealthReportAnalyzer.analyze(health, "rnf", "", task_catalog=[])
    step4 = [s for s in steps if s["priority"] == 4]
    assert len(step4) == 1, steps
    s = step4[0]
    assert "submit" in s["command"], f"comando debe sugerir submit: {s['command']}"
    assert "transition <gate_name>" not in s["command"], "NO debe sugerir transition"
    assert '{"decision"' in s["reason"] or "decision" in s["reason"], s["reason"]
    print("  ✅ test_health_analyzer_sugiere_submit_en_user_gate: user gate -> submit")


def test_health_analyzer_mantiene_transition_en_gates_de_nodo():
    """Sin execution_type=user_input, las gates del nodo siguen usando transition."""
    from features.project.application.use_cases import HealthReportAnalyzer

    health = {
        "checks": {"ckg": "13 nodes", "cpg": "16891 nodes", "files": "up to date"},
        "warnings": [],
        "sprint": {
            "node": "VERIFY",
            "sub_state": "QA",
            "gates": [
                {"name": "task_complete", "target": "SPRINT"},
                {"name": "needs_refine", "target": "REFINE"},
            ],
            "execution_type": "llm_task",
            "prompt_id": "verify/qa",
        },
        "project": {"language": "ts"},
    }
    steps = HealthReportAnalyzer.analyze(health, "rnf", "", task_catalog=[])
    step4 = [s for s in steps if s["priority"] == 4]
    assert len(step4) == 1, steps
    assert "transition <gate_name>" in step4[0]["command"], step4[0]["command"]
    assert "submit" not in step4[0]["command"], "gate de nodo -> transition, no submit"
    print("  ✅ test_health_analyzer_mantiene_transition_en_gates_de_nodo: gates -> transition")
