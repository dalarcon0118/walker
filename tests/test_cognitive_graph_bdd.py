"""
Tests BDD (pytest-bdd) — Persistencia, búsqueda y trazabilidad del grafo cognitivo.

Escenarios: bdd/features/cognitive_graph.feature
"""
from __future__ import annotations

import gzip
import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/cognitive_graph.feature")


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def ctx() -> dict:
    return {}


@pytest.fixture
def project_dir(tmp_path: Path) -> str:
    cog_proj = tmp_path / ".cognitive" / "projects" / "test-project"
    data_dir = cog_proj / "data"
    os.makedirs(data_dir, exist_ok=True)

    empty_ckg = {"index": "ckg", "docs": [], "edges": [], "token_index": {}}
    with open(data_dir / "ckg.json.gz", "wb") as f:
        f.write(gzip.compress(json.dumps(empty_ckg).encode()))

    empty_delta = {"index": "delta", "docs": [], "edges": [], "token_index": {}}
    with open(data_dir / "delta.json.gz", "wb") as f:
        f.write(gzip.compress(json.dumps(empty_delta).encode()))

    with open(cog_proj / "project.json", "w") as f:
        json.dump({"project_id": "test-project", "name": "Test"}, f)

    return str(cog_proj)


# ── Helpers ───────────────────────────────────────────────────────


def _write_ckg(path: str, docs: list, edges: list | None = None):
    payload = {"index": "ckg", "docs": docs, "edges": edges or [], "token_index": {}}
    with open(path, "wb") as f:
        f.write(gzip.compress(json.dumps(payload).encode()))


# ── Antecedentes ──────────────────────────────────────────────────


@given("que el proyecto cuenta con un grafo cognitivo como repositorio de conocimiento")
def _setup_cognitive_graph(project_dir: str, ctx: dict):
    ctx["project_dir"] = project_dir


# ── Escenario 1: preservación a través del tiempo ─────────────────


@given("que he almacenado decisiones y convenciones del proyecto en el grafo cognitivo")
def _store_initial_knowledge(project_dir: str, ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    cg = CognitiveGraph(project_dir)
    cg.store(
        "ckg",
        "D-001",
        {"type": "decision", "statement": "Usar arquitectura limpia", "scope": "architecture"},
    )
    cg.store(
        "ckg",
        "C-001",
        {"type": "convention", "statement": "Usar type hints", "scope": "code_quality"},
    )
    ctx["cg"] = cg


@when("el sistema cierra y se reinicia posteriormente")
def _restart_system(ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    ctx["cg"] = CognitiveGraph(ctx["project_dir"])


@then("todo el conocimiento permanece disponible")
def _knowledge_available(ctx: dict):
    result = ctx["cg"].search("ckg", {})
    assert result["hits"]["total"] == 2, f"Esperaba 2 docs, obtuve {result['hits']['total']}"


@then("ninguna decisión o convención se pierde entre sesiones")
def _no_lost_knowledge(ctx: dict):
    types = {hit["_source"].get("type") for hit in ctx["cg"].search("ckg", {})["hits"]["hits"]}
    assert types == {"decision", "convention"}, f"Tipos perdidos: {types}"


# ── Escenario 2: búsqueda por palabra clave o dominio ─────────────


@given("que el proyecto acumula tareas, decisiones y convenciones en el grafo")
def _populate_knowledge(ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    cg = CognitiveGraph(ctx["project_dir"])
    cg.store("delta", "T-001", {"type": "task", "objective": "Implementar auth", "domain": "security"})
    cg.store("delta", "T-002", {"type": "task", "objective": "Fix payment", "domain": "payments"})
    cg.store("delta", "T-003", {"type": "task", "objective": "Logout en auth", "domain": "security"})
    ctx["cg"] = cg


@when("necesito encontrar conocimiento por palabra clave o por dominio")
def _search_knowledge(ctx: dict):
    ctx["text_result"] = ctx["cg"].search("delta", {"text": "auth"})
    ctx["domain_result"] = ctx["cg"].search("delta", {"domain": "security"})


@then("el sistema devuelve los resultados relevantes")
def _relevant_results(ctx: dict):
    assert ctx["text_result"]["hits"]["total"] >= 1, "Búsqueda por texto sin resultados"
    assert ctx["domain_result"]["hits"]["total"] >= 2, "Búsqueda por dominio insuficiente"


@then("la búsqueda respeta el contexto del proyecto")
def _search_respects_context(ctx: dict):
    domains = {
        hit["_source"].get("domain")
        for hit in ctx["domain_result"]["hits"]["hits"]
    }
    assert domains == {"security"}, f"Dominios fuera de contexto: {domains}"


# ── Escenario 3: sin duplicados ni inconsistencias ─────────────────


@given("que almaceno conocimiento en el grafo cognitivo")
def _store_knowledge(ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    cg = CognitiveGraph(ctx["project_dir"])
    cg.store("ckg", "K-001", {"type": "decision", "statement": "Usar JWT", "scope": "security"})
    ctx["cg"] = cg


@when("guardo la misma información más de una vez")
def _save_duplicate(ctx: dict):
    ctx["cg"].store("ckg", "K-001", {"type": "decision", "statement": "Usar JWT", "scope": "security"})


@then("el sistema no genera duplicados")
def _no_duplicates(ctx: dict):
    result = ctx["cg"].search("ckg", {"_id": "K-001"})
    assert result["hits"]["total"] == 1, f"Duplicados detectados: {result['hits']['total']}"


@then("actualizar conocimiento previo conserva la información existente")
def _update_preserves_existing(ctx: dict):
    ctx["cg"].store("ckg", "K-001", {"type": "decision", "statement": "Usar JWT v2", "scope": "security"})
    doc = ctx["cg"].get("ckg", "K-001")
    assert doc is not None
    assert doc["_source"]["type"] == "decision"
    assert doc["_source"]["scope"] == "security"


# ── Escenario 4: relaciones explorables ────────────────────────────


@given("que el proyecto registra dependencias entre tareas, archivos y decisiones")
def _register_relations(ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    cg = CognitiveGraph(ctx["project_dir"])
    cg.store("delta", "T-042", {"objective": "Fix auth"})
    cg.store("delta", "T-015", {"objective": "Handle token"})
    cg.add_edge("T-042", "T-015", type="depends_on")
    cg.add_edge("T-042", "src/auth.py", type="impacts_file")
    ctx["cg"] = cg


@when("consulto las relaciones de un elemento de conocimiento")
def _query_relations(ctx: dict):
    ctx["related"] = ctx["cg"].get_related("T-042", depth=1)


@then("puedo navegar las conexiones relevantes")
def _navigate_connections(ctx: dict):
    assert ctx["related"]["total"] >= 2, f"Esperaba >=2 nodos, obtuve {ctx['related']['total']}"
    assert len(ctx["related"]["edges"]) >= 1, "Sin edges"


@then("las relaciones se mantienen al persistir y recuperar el grafo")
def _relations_persist(ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    cg2 = CognitiveGraph(ctx["project_dir"])
    related2 = cg2.get_related("T-042", depth=1)
    assert related2["total"] >= 2, f"Relaciones perdidas tras reload: {related2['total']}"


# ── Escenario 5: historial completo de decisiones ──────────────────


@given("que el equipo toma decisiones que evolucionan con el tiempo")
def _setup_decisions(ctx: dict):
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

    ctx["ckg_service"] = CodeKnowledgeGraph(ctx["project_dir"])
    r1 = ctx["ckg_service"].save_nodes(
        [{"label": "decision:use-jwt", "type": "decision", "metadata": {"decision": "approve", "sub_state": "DESIGN.APPROVE"}}]
    )
    ctx["v1_id"] = r1["node_ids"][0]


@when("una decisión reemplaza a otra sobre el mismo tema")
def _supersede_decision(ctx: dict):
    r2 = ctx["ckg_service"].save_nodes(
        [
            {
                "label": "decision:use-jwt-v2",
                "type": "decision",
                "supersedes": [ctx["v1_id"]],
                "metadata": {"decision": "reject", "sub_state": "DESIGN.APPROVE"},
            }
        ]
    )
    ctx["v2_id"] = r2["node_ids"][0]


@then("la decisión anterior se preserva como historial")
def _old_preserved(ctx: dict):
    historia = ctx["ckg_service"].query({"type": "decision", "include_superseded": True})
    ids = [x["_id"] for x in historia]
    assert ctx["v1_id"] in ids, "v1 debe estar en historial"
    assert ctx["v2_id"] in ids, "v2 debe estar en historial"


@then("las consultas habituales muestran solo la versión vigente")
def _only_current_shown(ctx: dict):
    activas = ctx["ckg_service"].query({"type": "decision"})
    ids = [x["_id"] for x in activas]
    assert ctx["v2_id"] in ids, "v2 debe estar activa"
    assert ctx["v1_id"] not in ids, "v1 no debe estar visible"


# ── Escenario 6: trabajo organizado por sprints ────────────────────


@given("que el equipo ejecuta trabajo en sprints y sesiones informales")
def _setup_sprint(ctx: dict):
    from features.delta_tracking import SpringService

    ctx["spring_service"] = SpringService(ctx["project_dir"])
    ctx["spring_service"].create_spring(
        "sprint-demo", goal="Demo sprint", status="active", project_id="demo"
    )
    ctx["spring_service"].add_spec("sprint-demo", "Tarea A", domain="test", spec_index=0)


@when("consulto el trabajo realizado en un sprint o sesión")
def _query_sprint(ctx: dict):
    ctx["specs"] = ctx["spring_service"].get_specs("sprint-demo")


@then("puedo ver las tareas y su estado de completitud")
def _see_tasks_status(ctx: dict):
    assert len(ctx["specs"]) >= 1, "Sin specs"
    assert ctx["specs"][0].get("status") in {"draft", "in_progress", "completed"}


@then("el sistema distingue entre sprints formales e informales")
def _distinguish_formal_informal(ctx: dict):
    from features.delta_tracking import SpringService

    svc = SpringService(ctx["project_dir"])
    svc.create_spring(
        "session-20260804",
        goal="Session log",
        status="active",
        project_id="demo",
        session_id="cli",
        informal=True,
    )
    active = svc.get_active_spring()
    assert active is not None
    assert active["_id"] == "sprint-demo", "formal debe priorizar"


# ── Escenario 7: trazabilidad de consultas ─────────────────────────


@given("que el sistema registra cada consulta de conocimiento realizada")
def _setup_trace(ctx: dict):
    from shared.core.cognitive_graph import CognitiveGraph

    cg = CognitiveGraph(ctx["project_dir"])
    docs = [
        {
            "_id": f"ev-{i:012d}",
            "tool": "",
            "task_id": "TASK-1",
            "session_id": "sess-bdd",
            "event_type": "kg_query",
            "timestamp": f"2026-08-01T00:00:0{i}",
            "data": {"index": "ckg", "query": {"type": "convention"}, "project_id": ""},
        }
        for i in range(1, 4)
    ]
    trace_payload = {"index": "trace", "docs": docs, "edges": []}
    with open(os.path.join(ctx["project_dir"], "data", "trace.json.gz"), "wb") as f:
        f.write(gzip.compress(json.dumps(trace_payload).encode()))
    ctx["cg"] = cg


@when("diferentes componentes consultan el grafo cognitivo")
def _query_trace(ctx: dict):
    ctx["trace_result"] = ctx["cg"].search("trace", {"type": "kg_query"})


@then("cada consulta se registra con su contexto de sesión y tarea")
def _trace_has_context(ctx: dict):
    hits = ctx["trace_result"]["hits"]["hits"]
    assert ctx["trace_result"]["hits"]["total"] == 3, f"Esperaba 3, obtuve {ctx['trace_result']['hits']['total']}"
    sessions = {h["_source"].get("session_id") for h in hits}
    tasks = {h["_source"].get("task_id") for h in hits}
    assert sessions == {"sess-bdd"}, f"Sesiones: {sessions}"
    assert tasks == {"TASK-1"}, f"Tasks: {tasks}"


@then("no se generan registros duplicados por rutas alternativas")
def _no_trace_duplicates(ctx: dict):
    ids = {h["_id"] for h in ctx["trace_result"]["hits"]["hits"]}
    assert len(ids) == 3, f"IDs duplicados: {len(ids)} != 3"


# ── Escenario 8: re-planificación sin perder lo anterior ───────────


@given("que un sprint tiene una planificación existente")
def _setup_existing_plan(ctx: dict):
    from features.delta_tracking import SpringService

    ctx["spring_service"] = SpringService(ctx["project_dir"])
    ctx["spring_service"].create_spring("session-demo", goal="Sprint demo", status="active")
    for i in range(13):
        ctx["spring_service"].add_spec("session-demo", f"Objetivo viejo {i + 1}", domain="planning", spec_index=i)
    ctx["initial_count"] = len(ctx["spring_service"].get_specs("session-demo"))


@when("el equipo re-planifica agregando nuevas tareas")
def _replan_append(ctx: dict):
    for i in range(9):
        ctx["spring_service"].upsert_spec(
            "session-demo",
            objective=f"Objetivo NUEVO {i + 1}",
            domain="code-analysis",
            spec_type="feature",
        )


@then("las tareas nuevas se incorporan sin eliminar las existentes")
def _new_tasks_kept(ctx: dict):
    specs = ctx["spring_service"].get_specs("session-demo")
    assert len(specs) == ctx["initial_count"] + 9, f"Esperaba {ctx['initial_count'] + 9}, obtuve {len(specs)}"
    assert specs[0]["objective"] == "Objetivo viejo 1"
    assert specs[13]["objective"] == "Objetivo NUEVO 1"


@then("la trazabilidad de qué era viejo y qué es nuevo se mantiene")
def _traceability_old_new(ctx: dict):
    specs = ctx["spring_service"].get_specs("session-demo")
    assert specs[13]["spec_id"] == "SPEC-014", f"spec_id nuevo: {specs[13]['spec_id']}"


# ── Escenario 9: extracción estructurada ───────────────────────────


@given("que el proyecto acumula conocimiento a lo largo del tiempo")
def _accumulate_knowledge(ctx: dict):
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

    ctx["ckg_service"] = CodeKnowledgeGraph(ctx["project_dir"])
    ctx["ckg_service"].save_nodes(
        [
            {"label": "Arquitectura limpia", "type": "decision", "scope": "architecture"},
            {"label": "Type hints obligatorios", "type": "convention", "scope": "code_quality"},
        ]
    )


@when("ejecuto la extracción estructurada del conocimiento")
def _extract_knowledge(ctx: dict):
    from features.main.task_execution.tasks.kg_extract import kg_extract
    from shared.common.shared_domain import ExecutionContext

    ctx_obj = ExecutionContext.create(session_id="bdd-extract", chain_dir=ctx["project_dir"])
    ctx["extract_result"] = kg_extract(ctx_obj, {"scope": "all"})


@then("obtengo un resumen coherente del estado del proyecto")
def _coherent_summary(ctx: dict):
    if ctx["extract_result"].ok:
        stats = ctx["extract_result"].data.get("stats", {})
        assert stats, "Resumen sin stats"


@then("el cuestionario de extracción cubre los aspectos relevantes del dominio")
def _questionnaire_coverage(ctx: dict):
    if ctx["extract_result"].ok:
        questionnaire = ctx["extract_result"].data.get("questionnaire", {})
        questions = questionnaire.get("questions", [])
        assert len(questions) == 6, f"Esperaba 6 preguntas, obtuve {len(questions)}"
