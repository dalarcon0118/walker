"""
Test: Reflexion-by-context (REFINE.DIAGNOSE).

Valida:
  1. build_reflection_context deriva source_node/attempt desde el TraceGraph
     (eventos TRANSITION hacia REFINE) — el motor no inyecta nada.
  2. error_context se compone desde claves genéricas del estado
     (last_error / last_error_strategy / last_reason).
  3. El ReflectionProvider produce una ContextPiece con la capa 'reflection'.
  4. El registry descubre el provider via bootstrap (sin tocar el engine).

PRINCIPIO: el engine es un mecanismo puro; la reflexión es lógica de la
feature event_tracing. Este test verifica que ninguna pieza requiera
conocer REFINE desde el motor.
"""

import json
import os
import tempfile


def _setup_project(tmpdir: str) -> tuple[str, str]:
    """Crea un proyecto walker válido y retorna (chain_dir, project_root)."""
    project_root = os.path.join(tmpdir, "proj")
    chain_dir = os.path.join(project_root, "data", "spring", "test-sprint")
    os.makedirs(os.path.join(project_root, "data"), exist_ok=True)
    os.makedirs(chain_dir, exist_ok=True)
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


def _write_trace_ndjson(chain_dir: str, events: list[dict]) -> None:
    """Escribe traces NDJSON (formato legacy data/traces/*.json.gz).

    load_traces() lee estos archivos como fallback al CognitiveGraph,
    por lo que sirve para testear el builder sin infra real.
    """
    import gzip

    trace_dir = os.path.join(chain_dir, "data", "traces")
    os.makedirs(trace_dir, exist_ok=True)
    path = os.path.join(trace_dir, "sprint.json.gz")
    with gzip.open(path, "wt") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_reflection_deriva_source_node_y_attempt_desde_trace():
    """source_node y attempt se derivan de TRANSITION -> REFINE, no del motor."""
    from features.event_tracing.application.reflection import (
        build_reflection_context,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, _ = _setup_project(tmpdir)
        _write_trace_ndjson(
            chain_dir,
            [
                {
                    "event_type": "TRANSITION",
                    "task_id": "TASK-001",
                    "timestamp": "2026-08-02T10:00:00+00:00",
                    "data": {"gate": "start_task", "from": "SPRINT", "to": "DESIGN"},
                },
                {
                    "event_type": "TRANSITION",
                    "task_id": "TASK-001",
                    "timestamp": "2026-08-02T11:00:00+00:00",
                    "data": {
                        "gate": "needs_refine",
                        "from": "IMPLEMENT",
                        "to": "REFINE",
                    },
                },
                {
                    "event_type": "ERROR",
                    "task_id": "TASK-001",
                    "timestamp": "2026-08-02T11:00:01+00:00",
                    "data": {
                        "error_type": "quality_gate",
                        "message": "tsc: 235 errores",
                    },
                },
            ],
        )

        pkg = build_reflection_context(
            chain_dir=chain_dir,
            task_id="TASK-001",
            last_error="quality_gate",
            last_error_strategy="retry_failed",
            last_reason="Calidad insuficiente en el code review",
            failed_gates="tsc sin errores",
        )

        assert pkg["source_node"] == "IMPLEMENT", (
            f"source_node derivado incorrecto: {pkg['source_node']}"
        )
        assert pkg["attempt"] == 1, f"attempt debería ser 1: {pkg['attempt']}"
        assert pkg["max_attempts"] == 3
        assert "quality_gate" in pkg["error_context"], pkg["error_context"]
        assert "tsc sin errores" in pkg["failed_gates"]
        assert pkg["hint"], "hint no debería estar vacío"
        assert any(p["type"] == "ERROR" for p in pkg["past_attempts"]), (
            "debería haber ERRORs"
        )
        print("  ✅ test_reflection_deriva_source_node_y_attempt_desde_trace")


def test_reflection_attempt_incrementa_con_segundo_refine():
    """Dos transiciones a REFINE -> attempt == 2."""
    from features.event_tracing.application.reflection import (
        build_reflection_context,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, _ = _setup_project(tmpdir)
        _write_trace_ndjson(
            chain_dir,
            [
                {
                    "event_type": "TRANSITION",
                    "task_id": "TASK-002",
                    "timestamp": "2026-08-02T09:00:00+00:00",
                    "data": {"gate": "needs_refine", "from": "ANALYZE", "to": "REFINE"},
                },
                {
                    "event_type": "TRANSITION",
                    "task_id": "TASK-002",
                    "timestamp": "2026-08-02T10:00:00+00:00",
                    "data": {
                        "gate": "needs_refine",
                        "from": "IMPLEMENT",
                        "to": "REFINE",
                    },
                },
            ],
        )
        pkg = build_reflection_context(chain_dir=chain_dir, task_id="TASK-002")
        assert pkg["attempt"] == 2, f"attempt debería ser 2: {pkg['attempt']}"
        # El source_node es el del último refine
        assert pkg["source_node"] == "IMPLEMENT"
        print("  ✅ test_reflection_attempt_incrementa_con_segundo_refine")


def test_reflection_provider_produce_capa():
    """ReflectionProvider.collect devuelve una ContextPiece 'reflection'."""
    from types import SimpleNamespace

    from features.event_tracing.context.providers import ReflectionProvider

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, _ = _setup_project(tmpdir)
        _write_trace_ndjson(
            chain_dir,
            [
                {
                    "event_type": "TRANSITION",
                    "task_id": "TASK-003",
                    "timestamp": "2026-08-02T10:00:00+00:00",
                    "data": {"gate": "needs_refine", "from": "VERIFY", "to": "REFINE"},
                },
            ],
        )

        ctx = SimpleNamespace(
            chain_dir=chain_dir,
            task_id="TASK-003",
            baggage={
                "task_id": "TASK-003",
                "last_error": "qa_reject",
                "last_error_strategy": "none",
                "last_reason": "Test de integración falló",
            },
        )
        provider = ReflectionProvider()
        piece = provider.collect({}, ctx)

        assert piece is not None
        assert piece.provider == "reflection"
        assert "source_node: VERIFY" in piece.content
        assert "attempt: 1/3" in piece.content
        assert "qa_reject" in piece.content
        assert piece.tokens > 0
        print("  ✅ test_reflection_provider_produce_capa")


def test_registry_descubre_reflection_sin_tocar_engine():
    """bootstrap_providers registra 'reflection' en el registry completo."""
    from features.engine.bootstrap import build_context_registry

    registry = build_context_registry()
    assert "reflection" in registry.available(), (
        f"provider 'reflection' no registrado. Disponibles: {registry.available()}"
    )
    print("  ✅ test_registry_descubre_reflection_sin_tocar_engine")


def test_prompt_context_builder_inyecta_reflexion():
    """PromptContextBuilder expone los placeholders de reflexión (SKILL.md 210).

    El engine (componente de contexto) debe inyectar source_node, attempt,
    error_context y hint derivados de la feature event_tracing, para que los
    prompts de REFINE los resuelvan con datos reales y no dependan de que el
    LLM "recuerde" una capa.
    """
    from features.engine.application.prompt_context_builder import (
        PromptContextBuilder,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, _ = _setup_project(tmpdir)
        _write_trace_ndjson(
            chain_dir,
            [
                {
                    "event_type": "TRANSITION",
                    "task_id": "TASK-004",
                    "timestamp": "2026-08-02T12:00:00+00:00",
                    "data": {
                        "gate": "needs_refine",
                        "from": "IMPLEMENT",
                        "to": "REFINE",
                    },
                },
                {
                    "event_type": "ERROR",
                    "task_id": "TASK-004",
                    "timestamp": "2026-08-02T12:00:01+00:00",
                    "data": {
                        "error_type": "quality_gate",
                        "message": "tsc: 235 errores",
                    },
                },
            ],
        )

        ctx = PromptContextBuilder().build(chain_dir, "TASK-004")

        # El builder debe exponer las claves de reflexión planas para templates
        assert "source_node" in ctx, f"falta source_node en ctx: {sorted(ctx)}"
        assert "attempt" in ctx, "falta attempt"
        assert "max_attempts" in ctx
        assert "error_context" in ctx
        assert "hint" in ctx, "falta hint (hint determinista)"
        assert "reflection_past_attempts" in ctx

        assert ctx["source_node"] == "IMPLEMENT", ctx["source_node"]
        assert ctx["attempt"] == 1
        assert ctx["max_attempts"] == 3
        assert "tsc" in ctx["hint"], f"hint debería citar el error: {ctx['hint']}"
        print(
            "  ✅ test_prompt_context_builder_inyecta_reflexion: builder inyecta reflexión"
        )


def test_prompt_refine_resuelve_placeholders_reflexion():
    """resolve_prompt resuelve {source_node}/{hint} en el template real de REFINE.

    Verifica el contrato SKILL.md 210 de punta a punta: el engine inyecta
    source_node/attempt/error_context y el prompt de DIAGNOSE los muestra.
    """
    from types import SimpleNamespace

    from features.engine.application.orchestrator import OrchestrationService

    with tempfile.TemporaryDirectory() as tmpdir:
        chain_dir, project_root = _setup_project(tmpdir)
        _write_trace_ndjson(
            chain_dir,
            [
                {
                    "event_type": "TRANSITION",
                    "task_id": "TASK-005",
                    "timestamp": "2026-08-02T12:00:00+00:00",
                    "data": {"gate": "needs_refine", "from": "VERIFY", "to": "REFINE"},
                },
            ],
        )

        # Template minimalista en workflow.v3/prompts/refine (ruta canónica)
        prompts_dir = os.path.join(project_root, "workflow.v3", "prompts", "refine")
        os.makedirs(prompts_dir, exist_ok=True)
        template = (
            "source_node={source_node} attempt={attempt}/{max_attempts} "
            "error={error_context} hint={hint}"
        )
        with open(os.path.join(prompts_dir, "diagnose.prompt.json"), "w") as f:
            json.dump(
                {"id": "refine/diagnose", "type": "llm_task", "template": template}, f
            )

        svc = OrchestrationService()
        ctx_obj = SimpleNamespace(
            chain_dir=chain_dir,
            task_id="TASK-005",
            session_id="s",
            baggage={"task_id": "TASK-005"},
        )
        sprint_ctx = svc._build_prompt_context(chain_dir, "TASK-005")
        prompt = svc.resolve_prompt(
            "refine/diagnose",
            chain_dir,
            "TASK-005",
            context_config={"sources": [], "focus": {"role": "x"}},
            execution_ctx=ctx_obj,
            extra_data=sprint_ctx,
        )

        assert "{source_node}" not in prompt, "source_node sin resolver"
        assert "{hint}" not in prompt, "hint sin resolver"
        assert "source_node=VERIFY" in prompt, f"source_node no inyectado: {prompt}"
        assert "attempt=1/3" in prompt, f"attempt no inyectado: {prompt}"
        print(
            "  ✅ test_prompt_refine_resuelve_placeholders_reflexion: prompt resuelto"
        )
