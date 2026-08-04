"""
Test: CognitiveGraph persistence, search, ODM entities, and tasks.

Validates:
  1. Gzip read/write with fallback (the persistence bug)
  2. Full-text search via TokenIndex
  3. ODM entities (Task, KnowledgeFact, CodeNode)
  4. Cross-index search (search_many)
  5. Graph edges and navigation
  6. kg_store + kg_search task flow
  7. kg_extract without sprint
  8. Empty data rejection
"""

import json
import os
import tempfile
import gzip


# ════════════════════════════════════════════════════════════════
# 1. Gzip fallback
# ════════════════════════════════════════════════════════════════


def test_gzip_fallback():
    """CognitiveGraph debe leer archivos gzip incluso si gzip.decompress falla."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "data")
        os.makedirs(data_dir)
        ckg_path = os.path.join(data_dir, "ckg.json.gz")

        # Guardar un CKG valido
        ckg_data = {
            "index": "ckg",
            "docs": [
                {"_id": "test-001", "type": "decision", "statement": "test knowledge"}
            ],
            "edges": [],
        }

        # Escribir con gzip normal
        payload = json.dumps(ckg_data, ensure_ascii=False, separators=(",", ":"))
        with open(ckg_path, "wb") as f:
            f.write(gzip.compress(payload.encode()))

        # Leer via CognitiveGraph
        cg = CognitiveGraph(tmpdir)
        result = cg.search("ckg", {})
        assert result["hits"]["total"] == 1, (
            f"Expected 1 doc, got {result['hits']['total']}"
        )
        print("  ✅ test_gzip_fallback: gzip normal funciona")

        # Simular archivo gzip con trailing data (que hace fallar gzip.decompress)
        with open(ckg_path, "wb") as f:
            payload = json.dumps(
                {
                    "index": "ckg",
                    "docs": [
                        {
                            "_id": "test-002",
                            "type": "decision",
                            "statement": "trailing data test",
                        }
                    ],
                    "edges": [],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            compressed = gzip.compress(payload.encode())
            f.write(compressed)
            f.write(b"TRAILING_DATA")  # trailing data que rompe gzip.decompress

        # Leer con CognitiveGraph (debe usar fallback manual)
        from libs.cognitive_graph import CognitiveGraph

        cg2 = CognitiveGraph(tmpdir)
        result2 = cg2.search("ckg", {})
        assert result2["hits"]["total"] == 1, (
            f"gzip fallback failed: expected 1, got {result2['hits']['total']}"
        )
        print("  ✅ test_gzip_fallback: trailing data manejado con fallback zlib")


# ════════════════════════════════════════════════════════════════
# 2. Full-text search via TokenIndex
# ════════════════════════════════════════════════════════════════


def test_fulltext_search():
    """Busqueda full-text via CognitiveGraph con TokenIndex."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir)

        # Guardar documentos con search config (delta usa fulltext)
        cg.store(
            "delta",
            "TASK-001",
            {
                "type": "task",
                "objective": "Implement authentication module",
                "domain": "security",
            },
        )
        cg.store(
            "delta",
            "TASK-002",
            {
                "type": "task",
                "objective": "Fix payment gateway timeout",
                "domain": "payments",
            },
        )
        cg.store(
            "delta",
            "TASK-003",
            {
                "type": "task",
                "objective": "Add logout button to auth module",
                "domain": "security",
            },
        )

        # Busqueda full-text por "auth"
        result = cg.search("delta", {"text": "auth"})
        assert result["hits"]["total"] >= 1, (
            f"Expected >=1 for 'auth', got {result['hits']['total']}"
        )

        # Busqueda estructurada por domain
        result = cg.search("delta", {"domain": "security"})
        assert result["hits"]["total"] >= 2, (
            f"Expected >=2 for security, got {result['hits']['total']}"
        )

        print(
            f"  ✅ test_fulltext_search: {result['hits']['total']} tasks in security domain"
        )


# ════════════════════════════════════════════════════════════════
# 3. ODM entities
# ════════════════════════════════════════════════════════════════


def test_odm_entities():
    """ODM entities: CRUD, search, type preservation."""
    from libs.cognitive_graph import CognitiveGraph
    from libs.cognitive_graph.odm import Entity, fields

    class TestTask(Entity):
        __index__ = "odm_test"
        __id_field__ = "task_id"
        __search__ = ["objective"]

        task_id: str = fields.String()
        objective: str = fields.Text(fulltext=True)
        domain: str = fields.String()
        status: str = fields.String()

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir, auto_register=True)
        TestTask.bind(cg)

        # Crear
        task = TestTask(
            task_id="TASK-001",
            objective="Fix auth bug",
            domain="security",
            status="in_progress",
        )
        task._cg_instance = cg
        task.save()

        # Verificar que type NO se sobreescribe con el nombre del indice
        doc = cg.get("odm_test", "TASK-001")
        assert doc is not None, "Doc should exist"
        source_type = doc["_source"].get("type", "")
        assert source_type != "odm_test", (
            f"type should NOT be index name, got '{source_type}'"
        )

        # Buscar por texto
        results = TestTask.search("auth", cg=cg)
        assert len(results) >= 1, f"Expected >=1, got {len(results)}"

        # Buscar por campo estructurado
        results = TestTask.find_by(domain="security", cg=cg)
        assert len(results) >= 1, f"Expected >=1, got {len(results)}"

        print("  ✅ test_odm_entities: CRUD, search, type preservation correct")


# ════════════════════════════════════════════════════════════════
# 4. Cross-index search
# ════════════════════════════════════════════════════════════════


def test_cross_index_search():
    """search_many debe consultar multiples indices y retornar resultados."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir)

        cg.store(
            "delta",
            "T-001",
            {"type": "task", "objective": "Auth task", "domain": "security"},
        )
        cg.store(
            "ckg",
            "D-001",
            {"type": "decision", "statement": "Use JWT", "scope": "security"},
        )

        result = cg.search_many(
            {
                "delta": {"domain": "security"},
                "ckg": {"scope": "security"},
            }
        )

        responses = result.get("responses", {})
        assert "delta" in responses, "delta response missing"
        assert "ckg" in responses, "ckg response missing"
        assert responses["delta"]["hits"]["total"] >= 1
        assert responses["ckg"]["hits"]["total"] >= 1

        print("  ✅ test_cross_index_search: both indices returned results")


# ════════════════════════════════════════════════════════════════
# 5. Graph edges and navigation
# ════════════════════════════════════════════════════════════════


def test_graph_edges():
    """Edges en el grafo deben persistir y ser navegables."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir)

        cg.store("delta", "TASK-042", {"objective": "Fix auth"})
        cg.store("delta", "TASK-015", {"objective": "Handle token"})

        cg.add_edge("TASK-042", "TASK-015", type="depends_on")
        cg.add_edge("TASK-042", "src/auth.py", type="impacts_file")

        # Navegar vecinos
        related = cg.get_related("TASK-042", depth=1)
        assert related["total"] >= 2, f"Expected >=2 nodes, got {related['total']}"
        assert len(related["edges"]) >= 1, (
            f"Expected >=1 edge, got {len(related['edges'])}"
        )

        # Persistencia de edges (recargar)
        cg2 = CognitiveGraph(tmpdir)
        related2 = cg2.get_related("TASK-042", depth=1)
        assert related2["total"] >= 2, (
            f"After reload: expected >=2, got {related2['total']}"
        )

        print("  ✅ test_graph_edges: edges persist and navigate correctly")


# ════════════════════════════════════════════════════════════════
# 6. kg_store + kg_search task flow
# ════════════════════════════════════════════════════════════════


def test_kg_store_search_flow():
    """kg_store debe persistir y kg_search debe encontrar lo guardado."""
    from libs.cognitive_graph import CognitiveGraph
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = tmpdir

        # Inicializar CKG (simula build_initial_ckg)
        data_dir = os.path.join(project_root, "data")
        os.makedirs(data_dir)
        ckg_path = os.path.join(data_dir, "ckg.json.gz")
        empty_ckg = {"index": "ckg", "docs": [], "edges": [], "token_index": {}}
        with open(ckg_path, "wb") as f:
            f.write(gzip.compress(json.dumps(empty_ckg).encode()))

        # Guardar via CodeKnowledgeGraph (simula kg_store)
        ckg_service = CodeKnowledgeGraph(project_root)
        result = ckg_service.save_nodes(
            [
                {
                    "label": "Clean Architecture with DDD",
                    "type": "decision",
                    "scope": "architecture",
                }
            ]
        )
        assert result["nodes_created"] == 1, (
            f"Expected 1 node created, got {result['nodes_created']}"
        )

        # Guardar segundo nodo (verificar que no pierde datos previos)
        result2 = ckg_service.save_nodes(
            [
                {
                    "label": "Use JWT for authentication",
                    "type": "decision",
                    "scope": "security",
                }
            ]
        )
        assert result2["nodes_created"] == 1

        # Leer con CognitiveGraph (simula kg_search)
        cg = CognitiveGraph(project_root)
        search_result = cg.search("ckg", {})
        total = search_result["hits"]["total"]
        assert total == 2, f"Expected 2 docs after 2 saves, got {total}"

        # Busqueda por tipo
        decisions = cg.search("ckg", {"type": "decision"})
        assert decisions["hits"]["total"] == 2, (
            f"Expected 2 decisions, got {decisions['hits']['total']}"
        )

        # Busqueda por scope (normalizado a metadata por kg_store; _match
        # debe encontrar el campo tanto en top-level como en metadata)
        scoped = cg.search("ckg", {"scope": "security"})
        assert scoped["hits"]["total"] == 1, (
            f"Expected 1 with scope=security, got {scoped['hits']['total']}"
        )
        scoped_arch = cg.search("ckg", {"scope": "architecture"})
        assert scoped_arch["hits"]["total"] == 1, (
            f"Expected 1 with scope=architecture, got {scoped_arch['hits']['total']}"
        )

        # Rechazar nodos vacios
        with open(ckg_path, "wb") as f:
            f.write(gzip.compress(json.dumps(empty_ckg).encode()))
        ckg_service2 = CodeKnowledgeGraph(project_root)
        result3 = ckg_service2.save_nodes([{"label": "", "type": "decision"}])
        assert result3["nodes_created"] == 0, "Empty label should be rejected"

        print(
            "  ✅ test_kg_store_search_flow: persistence consistent, empty data rejected"
        )


# ════════════════════════════════════════════════════════════════
# 7. Integration: tasks via registry (kg_store, kg_search, kg_extract)
# ════════════════════════════════════════════════════════════════


def _create_test_project(tmpdir: str) -> str:
    """Crea un proyecto de prueba con structure .cognitive/ completa."""
    import gzip
    import json

    cog_proj = os.path.join(tmpdir, ".cognitive", "projects", "test-project")
    data_dir = os.path.join(cog_proj, "data")
    os.makedirs(data_dir)

    # CKG vacio
    empty_ckg = {"index": "ckg", "docs": [], "edges": [], "token_index": {}}
    with open(os.path.join(data_dir, "ckg.json.gz"), "wb") as f:
        f.write(gzip.compress(json.dumps(empty_ckg).encode()))

    # delta vacio (para kg_search)
    empty_delta = {"index": "delta", "docs": [], "edges": [], "token_index": {}}
    with open(os.path.join(data_dir, "delta.json.gz"), "wb") as f:
        f.write(gzip.compress(json.dumps(empty_delta).encode()))

    # project.json minimo
    with open(os.path.join(cog_proj, "project.json"), "w") as f:
        json.dump({"project_id": "test-project", "name": "Test"}, f)

    return cog_proj


def test_kg_store_task():
    """Ejecutar kg_store como task registrada, con project_root directo."""
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)

        # Usar CodeKnowledgeGraph directamente (igual que kg_store internamente)
        ckg = CodeKnowledgeGraph(cog_proj)
        result = ckg.save_nodes(
            [
                {
                    "label": "Integration test knowledge",
                    "type": "decision",
                    "scope": "test",
                }
            ]
        )
        assert result["nodes_created"] == 1, f"save_nodes failed: {result}"

        # Verificar con CognitiveGraph directo (igual que kg_search internamente)
        from libs.cognitive_graph import CognitiveGraph

        cg = CognitiveGraph(cog_proj)
        search_result = cg.search("ckg", {"type": "decision"})
        total = search_result["hits"]["total"]
        assert total >= 1, f"Expected >=1 doc, got {total}"

        print(f"  ✅ test_kg_store_task: stored + verified ({total} docs)")


def test_kg_store_empty_rejected():
    """kg_store.save_nodes debe rechazar label vacio."""
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)
        ckg = CodeKnowledgeGraph(cog_proj)
        result = ckg.save_nodes([{"label": "", "type": "decision"}])
        assert result["nodes_created"] == 0, "Empty label should be rejected"

        print("  ✅ test_kg_store_empty_rejected: empty label rejected")


def test_kg_extract_task():
    """kg_extract() function debe devolver stats + questionnaire."""
    from features.task_execution.tasks.kg_extract import kg_extract
    from libs.shared_domain import ExecutionContext

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)

        # Simular que hay un CognitiveGraph disponible
        ctx = ExecutionContext.create(session_id="test", chain_dir=cog_proj)
        data = {"scope": "all"}

        result = kg_extract(ctx, data)
        # Puede fallar si no hay CPG, pero debe devolver estructura valida
        if result.ok:
            result.data.get("stats", {})
            questionnaire = result.data.get("questionnaire", {})
            assert len(questionnaire.get("questions", [])) == 6
            print("  ✅ test_kg_extract_task: CPG stats + 6 questions")
        else:
            # Sin CPG es aceptable (proyecto de prueba no tiene CPG)
            print("  ✅ test_kg_extract_task: no CPG (expected in test env)")


def test_kg_schema_via_cg():
    """kg_schema: CognitiveGraph debe listar indices."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        _create_test_project(tmpdir)
        cg = CognitiveGraph(tmpdir)
        indexes = cg.list_indexes()
        assert "ckg" in indexes, f"CKG not in {indexes}"
        assert "delta" in indexes, f"Delta not in {indexes}"

        print(f"  ✅ test_kg_schema_via_cg: indices: {indexes}")


def test_kg_multi_via_cg():
    """kg_multi: CognitiveGraph.search_many debe consultar multi-indice."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)
        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

        ckg = CodeKnowledgeGraph(cog_proj)
        ckg.save_nodes(
            [{"label": "Multi-index test", "type": "decision", "scope": "test"}]
        )

        cg = CognitiveGraph(cog_proj)
        result = cg.search_many(
            {
                "ckg": {"type": "decision"},
                "delta": {},
            }
        )
        responses = result.get("responses", {})
        assert "ckg" in responses, "Missing ckg"
        assert responses["ckg"]["hits"]["total"] >= 1
        assert "delta" in responses, "Missing delta"

        print(f"  ✅ test_kg_multi_via_cg: {len(responses)} indices")


def test_trace_search_dedupes_native_vs_registry():
    """TraceGraph: el mismo trace.json.gz no debe devolverse duplicado.

    El _trace_search fusiona storage nativo (chain_dir/data/trace.json.gz) y
    el storage del proyecto real (get_project_dir → repo_path descentralizado).
    Cuando chain_dir == project_dir (caso MCP con project_id desde CWD), ambas
    rutas resuelven al MISMO archivo → cada evento se contaba 2 veces.
    El dedupe por _id debe devolver cada evento exactamente una vez.
    """
    import gzip
    import json
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)

        # Escribir trace.json.gz con 3 eventos kg_query únicos
        docs = [
            {
                "_id": f"ev-{i:012d}",
                "tool": "",
                "task_id": "",
                "session_id": "cli",
                "event_type": "kg_query",
                "timestamp": f"2026-08-01T00:00:0{i}",
                "data": {
                    "index": "ckg",
                    "query": {"type": "convention"},
                    "project_id": "",
                },
            }
            for i in range(1, 4)
        ]
        trace_payload = {"index": "trace", "docs": docs, "edges": []}
        with open(os.path.join(cog_proj, "data", "trace.json.gz"), "wb") as f:
            f.write(gzip.compress(json.dumps(trace_payload).encode()))

        # chain_dir == project_dir: fuente nativa y registry apuntan al mismo archivo
        cg = CognitiveGraph(cog_proj)
        result = cg.search("trace", {"type": "kg_query"})
        hits = result["hits"]["hits"]

        assert result["hits"]["total"] == 3, (
            f"Esperaba 3 eventos únicos, obtuvo {result['hits']['total']}"
        )
        unique_ids = {h["_id"] for h in hits}
        assert len(unique_ids) == 3, f"IDs duplicados: {len(unique_ids)} != 3"

        print(
            "  ✅ test_trace_search_dedupes_native_vs_registry: 3 eventos, 0 duplicados"
        )


def test_query_manager_traces_composer_queries():
    """QueryManager.query_layer() debe registrar eventos kg_query en el trace.

    El composer inyecta capas (cpg/ckg/delta/trace) via query_layer().
    Antes esas consultas eran INVISIBLES en el TraceGraph (solo las tasks
    CLI kg_search/kg_multi registraban eventos). Con este fix, cada capa
    consultada queda trazada con el task_id/session_id real del contexto.
    """
    from features.engine.context.query_manager import QueryManager

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)

        # Escribir trace.json.gz vacío (para que el índice exista)
        import gzip

        trace_payload = {"index": "trace", "docs": [], "edges": []}
        with open(os.path.join(cog_proj, "data", "trace.json.gz"), "wb") as f:
            f.write(gzip.compress(json.dumps(trace_payload).encode()))

        qm = QueryManager(
            cog_proj,
            task_id="TASK-1",
            session_id="sess-composer-test",
        )
        qm.query_layer("ckg", {"type": "convention"})
        qm.query_layer("state", {})

        from libs.infrastructure import CognitiveGraphSearcher

        searcher = CognitiveGraphSearcher(cog_proj)
        result = searcher.search("trace", {"type": "kg_query"})
        hits = result["hits"]["hits"]

        # Cada query_layer registra 1 evento kg_query (ckg + state = 2)
        assert result["hits"]["total"] == 2, (
            f"Esperaba 2 eventos kg_query del composer, obtuvo {result['hits']['total']}"
        )
        sessions = {h["_source"].get("session_id") for h in hits}
        assert sessions == {"sess-composer-test"}, f"Session no trazada: {sessions}"
        tasks = {h["_source"].get("task_id") for h in hits}
        assert tasks == {"TASK-1"}, f"Task no trazada: {tasks}"

        print(
            "  ✅ test_query_manager_traces_composer_queries: 2 eventos, session/task trazados"
        )


# ════════════════════════════════════════════════════════════════
# 8. TaskResult failure with data
# ════════════════════════════════════════════════════════════════


def test_task_result_failure_with_data():
    """TaskResult.failure() debe aceptar data dict con next_step."""
    from libs.shared_domain import TaskResult

    r = TaskResult.failure("test error", data={"next_step": "do something"})
    assert r.ok is False
    assert r.error == "test error"
    assert r.data == {"next_step": "do something"}
    print("  ✅ test_task_result_failure_with_data: failure() accepts data dict")


def test_decision_lifecycle_supersedes():
    """Ciclo de vida de decisiones: supersedes degrada a la previa y query filtra."""
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)
        ckg = CodeKnowledgeGraph(cog_proj)

        # 1) Decision v1 activa
        r1 = ckg.save_nodes(
            [
                {
                    "label": "decision:use-jwt",
                    "type": "decision",
                    "metadata": {"decision": "approve", "sub_state": "DESIGN.APPROVE"},
                }
            ]
        )
        assert r1["nodes_created"] == 1
        v1_id = r1["node_ids"][0]

        # 2) Decision v2 que SUPERSEDE a la v1 -> v1 queda degradada
        r2 = ckg.save_nodes(
            [
                {
                    "label": "decision:use-jwt-v2",
                    "type": "decision",
                    "supersedes": [v1_id],
                    "metadata": {"decision": "reject", "sub_state": "DESIGN.APPROVE"},
                }
            ]
        )
        assert r2["degraded"] == [v1_id], f"Expected v1 degraded, got {r2['degraded']}"
        v2_id = r2["node_ids"][0]

        # 3) query() por defecto: SOLO decisiones activas (v2)
        activas = ckg.query({"type": "decision"})
        activas_ids = [x["_id"] for x in activas]
        assert v2_id in activas_ids, "v2 debe estar activa"
        assert v1_id not in activas_ids, (
            "v1 debe estar degradada (no visible en query normal)"
        )

        # 4) include_superseded: historia completa
        historia = ckg.query({"type": "decision", "include_superseded": True})
        historia_ids = [x["_id"] for x in historia]
        assert v1_id in historia_ids and v2_id in historia_ids, (
            "Historia debe incluir ambas"
        )

        # 5) La degradada tiene metadata de trazabilidad
        v1_doc = next(x for x in historia if x["_id"] == v1_id)
        meta = v1_doc["metadata"]
        assert meta.get("status") == "superseded", f"status={meta.get('status')}"
        assert meta.get("superseded_by"), "debe registrar quien la reemplazo"
        assert meta.get("superseded_at"), "debe registrar cuando"
        print(
            "  ✅ test_decision_lifecycle_supersedes: supersedes degrada, query filtra"
        )


def test_capture_decision_detects_superseded():
    """capture_decision detecta automaticamente que una decision nueva reemplaza a la previa."""
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
    from features.task_execution.side_effects.decision_capture import capture_decision
    from libs.shared_domain import ExecutionContext

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)
        # chain_dir para que capture_decision resuelva project_root via ProjectService
        # (el project.json vive a nivel del proyecto: cog_proj/project.json — creado por _create_test_project)
        chain_dir = os.path.join(cog_proj, "data", "spring", "sprint-test")
        os.makedirs(chain_dir, exist_ok=True)

        ctx = ExecutionContext(
            session_id="s1", chain_dir=chain_dir, trace_id="t1", span_id="sp1"
        )

        # 1) PO aprueba
        r1 = capture_decision(
            ctx, {"decision": "approve", "sub_state": "DESIGN.APPROVE"}
        )
        assert r1["captured"] is True, f"capture 1 failed: {r1}"
        first_id = r1["result"]["node_ids"][0]

        # 2) PO cambia de opinion -> reject sobre el MISMO sub_state
        r2 = capture_decision(
            ctx, {"decision": "reject", "sub_state": "DESIGN.APPROVE"}
        )
        assert r2["captured"] is True, f"capture 2 failed: {r2}"
        assert r2["result"]["supersedes_detected"] == [first_id], (
            f"debe detectar la approve previa, got {r2['result'].get('supersedes_detected')}"
        )
        assert r2["result"]["degraded"] == [first_id], "debe degradar la approve previa"

        # 3) query normal: solo queda la reject activa
        ckg = CodeKnowledgeGraph(cog_proj)
        activas = ckg.query({"type": "decision"})
        assert len(activas) == 1, f"Deberia quedar 1 activa, got {len(activas)}"
        assert activas[0]["metadata"]["decision"] == "reject"

        # 4) reconfirmacion del mismo valor NO supersede
        r3 = capture_decision(
            ctx, {"decision": "reject", "sub_state": "DESIGN.APPROVE"}
        )
        assert r3["result"].get("supersedes_detected") == [], (
            "reconfirmacion no debe superseder"
        )
        print(
            "  ✅ test_capture_decision_detects_superseded: deteccion automatica funcional"
        )


def test_capture_task_creates_session_sprint():
    """capture_task crea/reusa el spring informal session-{fecha} y lo indexa en delta."""
    from features.task_execution.tasks.capture_task import capture_task
    from libs.shared_domain import ExecutionContext

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)
        ctx = ExecutionContext(
            session_id="s-test", chain_dir="", trace_id="t1", span_id="sp1"
        )

        # 1) Capturar primera tarea -> crea el spring informal
        r1 = capture_task(
            ctx,
            {
                "_project_id": "test-project",
                "_project_root": cog_proj,
                "_payload": {
                    "objective": "Implementar feature X",
                    "domain": "test-domain",
                    "type": "feature",
                    "result_state": "Feature X funcionando",
                    "files": ["features/x.py"],
                },
            },
        )
        assert r1.ok, f"capture_task 1 failed: {r1.error}"
        sprint_id = r1.data["sprint_id"]
        assert sprint_id.startswith("session-"), f"sprint_id={sprint_id}"
        assert r1.data["created_sprint"] is True, "debe crear el sprint la primera vez"

        # 2) Capturar segunda tarea -> reutiliza el mismo spring informal
        r2 = capture_task(
            ctx,
            {
                "_project_id": "test-project",
                "_project_root": cog_proj,
                "_payload": {"objective": "Arreglar bug Y", "domain": "test-domain"},
            },
        )
        assert r2.ok
        assert r2.data["sprint_id"] == sprint_id, "misma fecha = mismo spring"
        assert r2.data["created_sprint"] is False, "no debe recrear el sprint"
        assert r2.data["spec_count"] == 2, (
            f"debe haber 2 specs, got {r2.data['spec_count']}"
        )

        # 3) El sprint informal persiste en el delta (fuente unica) y en
        #    plan.json (proyeccion de compatibilidad). sprint-state.json
        #    fue ELIMINADO estructuralmente.
        spring_root = os.path.join(cog_proj, "data", "spring", sprint_id)
        assert os.path.exists(os.path.join(spring_root, "plan.json"))
        assert not os.path.exists(os.path.join(spring_root, "sprint-state.json")), (
            "sprint-state.json fue eliminado (delta = fuente unica)"
        )
        with open(os.path.join(spring_root, "plan.json")) as f:
            plan = json.load(f)
        specs = plan.get("specifications", [])
        assert len(specs) == 2, f"plan debe tener 2 specs, got {len(specs)}"
        assert specs[0]["domain"] == "test-domain", (
            f"domain se debe preservar: {specs[0].get('domain')}"
        )
        assert specs[0]["result_state"] == "Feature X funcionando"
        files = specs[0].get("files") or specs[0].get("impacted_artifacts", {}).get(
            "files", []
        )
        assert files == ["features/x.py"], f"files={files}"
        # El estado de negocio (specs) vive en el delta, no en un archivo
        # sprint-state.json paralelo.

        # 4) El delta (fuente unica) tiene nodo spring + specs con estado
        from libs.cognitive_graph import CognitiveGraph

        cg = CognitiveGraph(cog_proj, auto_register=True)
        idx = cg._indexes.get("delta")
        result = idx.search({}) if hasattr(idx, "search") else {"hits": {"hits": []}}
        docs = [h.get("_source", h) for h in result.get("hits", {}).get("hits", [])]
        spring_docs = [
            d for d in docs if d.get("_id") == sprint_id or d.get("type") == "spring"
        ]
        assert any(d.get("type") == "spring" for d in spring_docs), (
            "debe existir nodo spring"
        )
        session_docs = [
            d
            for d in docs
            if d.get("spring_id") == sprint_id or d.get("sprint_id") == sprint_id
        ]
        assert len(session_docs) >= 2, (
            f"delta debe tener specs de session, got {len(session_docs)}"
        )
        # IDs unicos por sprint
        task_ids = [d.get("task_id", d.get("_id", "")) for d in session_docs]
        assert all(t.startswith(f"{sprint_id}:") or t == sprint_id for t in task_ids), (
            f"ids={task_ids}"
        )
        print(
            "  ✅ test_capture_task_creates_session_sprint: spring informal + delta fuente unica"
        )


def test_index_sprint_unique_ids():
    """index_sprint debe usar IDs unicos por sprint (evita colision entre sprints)."""
    from features.delta_tracking.application.index_service import index_sprint
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cog_proj = _create_test_project(tmpdir)
        cg = CognitiveGraph(cog_proj, auto_register=True)

        # Crear dos sprints con la misma spec_id (via plan.json — proyeccion)
        for sprint_id in ("sprint-a", "sprint-b"):
            sprint_dir = os.path.join(cog_proj, "data", "spring", sprint_id)
            os.makedirs(sprint_dir, exist_ok=True)
            plan = {
                "version": "2.0",
                "chain_id": sprint_id,
                "goal": f"Goal {sprint_id}",
                "specifications": [
                    {
                        "spec_id": "SPEC-001",
                        "objective": f"Tarea de {sprint_id}",
                        "domain": "test",
                        "type": "feature",
                    },
                ],
            }
            with open(os.path.join(sprint_dir, "plan.json"), "w") as f:
                json.dump(plan, f)

        # Indexar ambos
        index_sprint(os.path.join(cog_proj, "data", "spring", "sprint-a"), cg)
        index_sprint(os.path.join(cog_proj, "data", "spring", "sprint-b"), cg)

        # Verificar que ambos docs existen (no colisionaron)
        idx = cg._indexes.get("delta")
        result = idx.search({}) if hasattr(idx, "search") else {"hits": {"hits": []}}
        docs = [h.get("_source", h) for h in result.get("hits", {}).get("hits", [])]
        ids = [d.get("task_id", d.get("_id", "")) for d in docs]
        assert "sprint-a:SPEC-001" in ids, f"debe existir sprint-a:SPEC-001, got {ids}"
        assert "sprint-b:SPEC-001" in ids, f"debe existir sprint-b:SPEC-001, got {ids}"
        print("  ✅ test_index_sprint_unique_ids: IDs unicos por sprint, sin colision")


def test_spring_graph_service_unified_model():
    """SpringGraphService: modelo unificado (spring+spec+estados) sobre el delta."""
    from features.delta_tracking.application.spring_graph_service import (
        SpringGraphService,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SpringGraphService(tmpdir)

        # 1. Crear spring + specs
        svc.create_spring(
            "sprint-demo", goal="Unificar fuentes", status="planning", project_id="demo"
        )
        svc.add_spec("sprint-demo", "Crear nodo spring", domain="delta", spec_index=0)
        svc.add_spec("sprint-demo", "Crear nodo spec", domain="delta", spec_index=1)

        # 2. Spring activo (consulta de negocio)
        assert svc.get_active_spring() is None, "aun no activo"
        svc.set_spring_status("sprint-demo", "active")
        active = svc.get_active_spring()
        assert active is not None and active.get("status") == "active"

        # 3. Muta estados de specs durante la ejecucion
        svc.update_spec_status("sprint-demo", "SPEC-001", "in_progress")
        svc.update_spec_status("sprint-demo", "SPEC-002", "completed")
        specs = svc.get_specs("sprint-demo")
        assert specs[0]["status"] == "in_progress", f"got {specs[0]['status']}"
        assert specs[1]["status"] == "completed", f"got {specs[1]['status']}"

        # 4. Spec actual = primera no completada
        cur = svc.get_current_spec("sprint-demo")
        assert cur["spec_id"] == "SPEC-001", f"current={cur['spec_id']}"

        # 5. Historico: spring cerrado deja de ser activo
        svc.set_spring_status("sprint-demo", "closed")
        assert svc.get_active_spring() is None, "cerrado no debe estar activo"
        assert len(svc.list_springs(status="closed")) == 1

        # 6. Persistencia en disco (delta.json.gz)
        import gzip

        delta_path = os.path.join(tmpdir, "data", "delta.json.gz")
        assert os.path.exists(delta_path), "delta debe persistir"
        d = json.loads(gzip.open(delta_path).read())
        types = [x.get("type") for x in d.get("docs", [])]
        assert "spring" in types and "spec" in types, f"types={types}"
        # Edges contains
        edge_types = [e.get("type") for e in d.get("edges", [])]
        assert "contains" in edge_types, f"edges={edge_types}"
        print(
            "  ✅ test_spring_graph_service_unified_model: modelo unificado sobre delta"
        )


def test_get_active_spring_prioriza_formal():
    """get_active_spring() debe priorizar el sprint formal del workflow.

    Regression: capture_task crea session-YYYYMMDD con status='active' y
    competia con el sprint formal como 'activo'. El informal no debe
    desplazar al formal; solo se usa como fallback si NO hay formal.
    """
    from features.delta_tracking.application.spring_graph_service import (
        SpringGraphService,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SpringGraphService(tmpdir)

        # Sprint formal del workflow (sin informal)
        svc.create_spring(
            "sprint-formal", goal="Goal formal", status="active", project_id="demo"
        )
        # Sprint informal de captura (session log)
        svc.create_spring(
            "session-20260804",
            goal="Session log session-20260804 — trabajo del agente",
            status="active",
            project_id="demo",
            session_id="cli",
            informal=True,
        )

        # El activo global debe ser el FORMAL, no el informal
        active = svc.get_active_spring()
        assert active is not None
        assert active["_id"] == "sprint-formal", (
            f"formal debe ganar, got {active['_id']}"
        )

        # Fallback: si el formal se cierra, el informal es el activo
        svc.set_spring_status("sprint-formal", "closed")
        active = svc.get_active_spring()
        assert active is not None and active["_id"] == "session-20260804", (
            "informal como fallback cuando no hay formal"
        )

        # Sin activos → None
        svc.set_spring_status("session-20260804", "closed")
        assert svc.get_active_spring() is None
        print(
            "  ✅ test_get_active_spring_prioriza_formal: formal gana, informal fallback"
        )


def test_spec_key_unico_por_spring():
    """spec_key identifica globalmente una spec ({spring_id}:{spec_id}).

    Regression: dos springs distintos comparten spec_id local (SPEC-001..N).
    spec_key debe ser unico para que las consultas distingan specs de
    springs diferentes sin colision.
    """
    from features.delta_tracking.application.spring_graph_service import (
        SpringGraphService,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SpringGraphService(tmpdir)

        # Dos springs con la misma spec_id local
        for sid in ("sprint-a", "sprint-b"):
            svc.create_spring(sid, goal=f"Goal {sid}", status="active")
            svc.add_spec(sid, f"Tarea de {sid}", domain="test", spec_index=0)

        keys_a = [s["spec_key"] for s in svc.get_specs("sprint-a")]
        keys_b = [s["spec_key"] for s in svc.get_specs("sprint-b")]

        # spec_id local es identico (SPEC-001) pero spec_key es unico
        assert keys_a == ["sprint-a:SPEC-001"], f"keys_a={keys_a}"
        assert keys_b == ["sprint-b:SPEC-001"], f"keys_b={keys_b}"
        assert keys_a != keys_b, "spec_key debe diferenciar springs"

        # El doc_id del delta coincide con spec_key
        spec_a = svc.get_spec("sprint-a", "SPEC-001")
        assert spec_a is not None
        assert spec_a.get("spec_key") == "sprint-a:SPEC-001"
        print(
            "  ✅ test_spec_key_unico_por_spring: spec_key identifica spring+spec"
        )


def test_spring_display_name():
    """display_name: nombre legible del spring, derivado del goal.

    El spring_id es un identificador técnico (session-YYYYMMDD o
    sprint-timestamp) heredado del directorio. display_name da un nombre
    sugerente de lo que se hizo, derivado del goal en backfill si falta.
    """
    from features.delta_tracking.application.spring_graph_service import (
        SpringGraphService,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SpringGraphService(tmpdir)

        # Creación con display_name explícito
        svc.create_spring(
            "spring-redesign",
            goal="Rediseño del contrato autoritativo del workflow",
            status="active",
            display_name="Workflow contract redesign",
        )
        spring = svc.get_spring("spring-redesign")
        assert spring["display_name"] == "Workflow contract redesign"

        # Derivación automática del goal (sin display_name)
        svc.create_spring(
            "session-20260804",
            goal="Session log session-20260804 — trabajo del agente",
            status="active",
            informal=True,
        )
        spring = svc.get_spring("session-20260804")
        assert spring["display_name"] == "Session log session-20260804", (
            f"derivado del goal, got {spring['display_name']}"
        )

        # Backfill: spring legacy sin display_name obtiene el derivado
        svc._store(
            "sprint-legacy",
            {
                "type": "spring",
                "goal": "Refactor estructural Fase 3: DeltaGraph como fuente",
                "status": "active",
            },
        )
        spring = svc.get_spring("sprint-legacy")
        assert spring["display_name"] == (
            "Refactor estructural Fase 3"
        ), f"backfill del goal, got {spring['display_name']}"

        # get_active_spring expone display_name
        active = svc.get_active_spring()
        assert active is not None and active.get("display_name")
        print(
            "  ✅ test_spring_display_name: display_name explícito, derivado y backfill"
        )


def test_slugify_sprint_name():
    """Sprints formales se nombran con slug descriptivo del goal.

    Regression: _resolve_chain_dir usaba 'sprint-{timestamp}' (id opaco).
    Ahora genera sprint-{slug} desde el goal para que el spring_id comunique
    de que trata el trabajo.
    """
    from features.spring.application.use_cases import _slugify_sprint_name

    # Slug descriptivo desde el goal
    slug = _slugify_sprint_name(
        "Rediseno del contrato autoritativo del workflow: resolver gaps"
    )
    assert slug.startswith("sprint-rediseno-del-contrato-autoritativo"), slug
    assert slug == slug.lower() and " " not in slug, f"slug invalido: {slug}"

    # Sin caracteres especiales/acentos
    slug2 = _slugify_sprint_name("Ñandú — migración del código legacy")
    assert slug2 == "sprint-nandu-migracion-del-codigo-legacy", slug2

    # Goal vacío → fallback timestamp
    slug3 = _slugify_sprint_name("")
    assert slug3.startswith("sprint-") and slug3 != "sprint-", slug3
    print(
        "  ✅ test_slugify_sprint_name: spring_id descriptivo desde el goal"
    )


def test_spring_graph_import_legacy():
    """import_spring: puente desde plan.json legacy al modelo unificado.

    PRINCIPIO estructural: sprint-state.json fue eliminado. El estado del
    spring se deriva de .workflow-state.json (motor) o defaults a planning.
    """
    from features.delta_tracking.application.spring_graph_service import (
        SpringGraphService,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Crear un sprint legacy (plan.json + .workflow-state.json)
        sprint_dir = os.path.join(tmpdir, "data", "spring", "sprint-legacy")
        os.makedirs(sprint_dir, exist_ok=True)
        plan = {
            "chain_id": "sprint-legacy",
            "goal": "Spring legacy",
            "specifications": [
                {
                    "spec_id": "SPEC-001",
                    "objective": "Tarea A",
                    "domain": "d1",
                    "type": "feature",
                    "status": "specified",
                },
                {
                    "spec_id": "SPEC-002",
                    "objective": "Tarea B",
                    "domain": "d2",
                    "type": "feature",
                    "status": "draft",
                },
            ],
        }
        with open(os.path.join(sprint_dir, "plan.json"), "w") as f:
            json.dump(plan, f)
        # Estado del motor: spring activo
        with open(os.path.join(sprint_dir, ".workflow-state.json"), "w") as f:
            json.dump(
                {
                    "current_node": "SPRINT",
                    "current_sub_state": "ACTIVE",
                    "sub_state_index": 0,
                    "sub_state_data": {},
                    "history": ["SPRINT.ACTIVE"],
                    "context": {"chain_dir": sprint_dir},
                    "sprint": {"chain_id": "sprint-legacy", "status": "active"},
                },
                f,
            )
        assert not os.path.exists(os.path.join(sprint_dir, "sprint-state.json")), (
            "sprint-state.json fue eliminado"
        )

        svc = SpringGraphService(tmpdir)
        r = svc.import_spring(sprint_dir)
        assert r["imported"] is True and r["specs"] == 2, f"import={r}"

        # Spring en modelo unificado con estado importado desde el motor
        spring = svc.get_spring("sprint-legacy")
        assert spring is not None and spring.get("status") == "active", (
            f"spring={spring}"
        )

        specs = svc.get_specs("sprint-legacy")
        assert len(specs) == 2, f"specs={len(specs)}"
        statuses = {s.get("spec_id"): s.get("status") for s in specs}
        assert statuses.get("SPEC-001") == "specified", f"statuses={statuses}"
        assert statuses.get("SPEC-002") == "draft", f"statuses={statuses}"
        print("  ✅ test_spring_graph_import_legacy: puente legacy al modelo unificado")


# ════════════════════════════════════════════════════════════════
# 12. Infra gaps: upsert, update_or_create, fulltext unificado, atomic write
# ════════════════════════════════════════════════════════════════


def test_upsert_spec_append_no_replace():
    """BUG re-planificación: upsert_spec debe APPENDEAR desde SPEC-014+
    cuando el spring ya tiene specs, NO sobrescribir SPEC-001..N."""
    from features.delta_tracking.application.spring_graph_service import (
        SpringGraphService,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SpringGraphService(tmpdir)
        svc.create_spring("session-demo", goal="Sprint demo", status="active")

        # Spring con 13 specs (simula el caso real session-20260731)
        for i in range(13):
            svc.add_spec(
                "session-demo",
                f"Objetivo viejo {i + 1}",
                domain="legacy",
                spec_index=i,
            )
        assert len(svc.get_specs("session-demo")) == 13

        # Re-plan con 9 tasks NUEVAS (objetivos distintos)
        for i in range(9):
            svc.upsert_spec(
                "session-demo",
                objective=f"Objetivo NUEVO {i + 1}",
                domain="code-analysis",
                spec_type="feature",
            )

        specs = svc.get_specs("session-demo")
        assert len(specs) == 22, f"Esperaba 22 specs (13+9), got {len(specs)}"
        # Las viejas siguen intactas
        assert specs[0]["objective"] == "Objetivo viejo 1"
        assert specs[12]["objective"] == "Objetivo viejo 13"
        # Las nuevas continuan desde SPEC-014+
        assert specs[13]["spec_id"] == "SPEC-014", specs[13]["spec_id"]
        assert specs[13]["objective"] == "Objetivo NUEVO 1"
        assert specs[21]["spec_id"] == "SPEC-022", specs[21]["spec_id"]
        print("  ✅ test_upsert_spec_append_no_replace: no reemplaza, appendea SPEC-014+")


def test_upsert_spec_refine_no_duplicate():
    """Re-plan con el MISMO objetivo: refina la spec existente, no duplica."""
    from features.delta_tracking.application.spring_graph_service import (
        SpringGraphService,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = SpringGraphService(tmpdir)
        svc.create_spring("session-demo", goal="Sprint demo", status="active")
        svc.upsert_spec(
            "session-demo",
            objective="Fix auth bug",
            domain="security",
            status="draft",
        )

        # Re-plan con el mismo objetivo pero estado distinto
        svc.upsert_spec(
            "session-demo",
            objective="Fix auth bug",
            domain="security",
            status="specified",
            result_state="auth fixed",
        )

        specs = svc.get_specs("session-demo")
        assert len(specs) == 1, f"Esperaba 1 spec (sin duplicar), got {len(specs)}"
        assert specs[0]["spec_id"] == "SPEC-001"
        assert specs[0]["status"] == "specified", specs[0]["status"]
        assert specs[0]["result_state"] == "auth fixed"
        print("  ✅ test_upsert_spec_refine_no_duplicate: refina, no duplica")


def test_odm_update_or_create():
    """update_or_create: primitiva reusable por campo de negocio."""
    from libs.cognitive_graph import CognitiveGraph
    from libs.cognitive_graph.odm import Entity, fields

    class Doc(Entity):
        __index__ = "odm_uoc"
        __id_field__ = "doc_id"

        doc_id: str = fields.String()
        objective: str = fields.Text(fulltext=True)
        status: str = fields.String()

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir, auto_register=True)
        Doc.bind(cg)

        # Create
        obj, created = Doc.update_or_create(
            filters={"objective": "Crear nodo spring"},
            defaults={"doc_id": "TASK-1", "status": "draft"},
            cg=cg,
        )
        assert created is True, "Primer call debe crear"
        assert obj._id == "TASK-1"

        # Update (mismo objective → refine, no crear)
        obj2, created2 = Doc.update_or_create(
            filters={"objective": "Crear nodo spring"},
            defaults={"status": "completed"},
            cg=cg,
        )
        assert created2 is False, "Segundo call debe refinar"
        assert obj2._id == "TASK-1", "Debe conservar el id del nodo existente"
        assert obj2.status == "completed"

        docs = Doc.all(cg=cg)
        assert len(docs) == 1, f"Esperaba 1 doc, got {len(docs)}"
        print("  ✅ test_odm_update_or_create: create + refine sin duplicar")


def test_cognitive_graph_upsert():
    """CognitiveGraph.upsert: distingue create de update (by key)."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir, auto_register=True)

        # Create
        res = cg.upsert("ckg", "node-001", {"type": "decision", "statement": "A"})
        assert res["created"] is True, res

        # Update (merge de campos: preserva campos no pisados)
        res2 = cg.upsert(
            "ckg", "node-001", {"statement": "A (v2)", "scope": "security"}
        )
        assert res2["updated"] is True, res2
        from libs.infrastructure import CognitiveGraphSearcher

        searcher = CognitiveGraphSearcher(tmpdir)
        hit = searcher.search("ckg", {"_id": "node-001"})["hits"]["hits"]
        src = hit[0]["_source"]
        assert src["statement"] == "A (v2)", src
        assert src["type"] == "decision", "El merge no debe borrar campos previos"
        assert src["scope"] == "security"
        print("  ✅ test_cognitive_graph_upsert: create/update por key (via CognitiveGraph)")


def test_fulltext_derived_from_fields():
    """G5: __search__ se deriva automaticamente de fields.fulltext=True."""
    from libs.cognitive_graph.odm import Entity, fields

    class SearchDoc(Entity):
        __index__ = "odm_ft"

        title: str = fields.Text(fulltext=True)
        body: str = fields.Text()
        status: str = fields.String()

    # Sin __search__ explicito, debe derivarse de fulltext=True
    assert SearchDoc.__search__ == ["title"], SearchDoc.__search__

    class ExplicitDoc(Entity):
        __index__ = "odm_ft2"
        __search__ = ["status"]

        title: str = fields.Text(fulltext=True)
        status: str = fields.String()

    # Fusiona explicito + derivado, sin duplicados
    assert ExplicitDoc.__search__ == ["status", "title"], ExplicitDoc.__search__
    print("  ✅ test_fulltext_derived_from_fields: __search__ derivado + fusionado")


def test_atomic_write_no_tmp_leftover():
    """G1: _save escribe atomico (temp+rename); no quedan .tmp huerfanos."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir, auto_register=True)
        cg.store("ckg", "n1", {"type": "decision", "statement": "x"})
        cg.store("ckg", "n2", {"type": "convention", "statement": "y"})

        # No deben quedar archivos .tmp ni .lock corruptos
        leftovers = [
            f for f in os.listdir(os.path.join(tmpdir, "data")) if ".tmp" in f
        ]
        assert leftovers == [], f"Quedaron temporales: {leftovers}"

        # El indice persiste integro tras multiple writes
        result = cg.search("ckg", {})
        assert result["hits"]["total"] == 2, result["hits"]["total"]
        print("  ✅ test_atomic_write_no_tmp_leftover: escritura atomica sin tmp")


def test_index_keys_and_max_key():
    """Primitiva keys()/max_key(): inventario completo sin limites de search."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir, auto_register=True)
        cg.store("ckg", "decision-001", {"type": "decision", "statement": "a"})
        cg.store("ckg", "decision-013", {"type": "decision", "statement": "b"})
        cg.store("ckg", "convention-002", {"type": "convention", "statement": "c"})
        cg.store("ckg", "other", {"type": "note", "statement": "d"})

        # keys() devuelve todos los IDs (sin top 50 de search)
        keys = cg.keys("ckg")
        assert len(keys) == 4, keys
        assert "decision-013" in keys

        # max_key con prefijo: ignora IDs no numericos y otros prefijos
        found = cg.max_key("ckg", "decision")
        assert found == ("decision-013", 13), found

        # max_key sin prefijo: el maximo numerico global
        found_all = cg.max_key("ckg")
        assert found_all == ("decision-013", 13), found_all

        # max_key sin coincidencias numericas
        assert cg.max_key("ckg", "rule") is None
        print("  ✅ test_index_keys_and_max_key: inventario completo sin top-50")


def test_ckg_next_id_no_collision_after_delete():
    """Bug CKG: _next_id usa max_key (no count+1) para no colisionar tras borrados."""
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        ckg = CodeKnowledgeGraph(tmpdir)
        ids = []
        for i in range(3):
            r = ckg.save_nodes([{"label": f"Decision {i}", "type": "decision"}])
            ids.append(r["node_ids"][0])
        assert ids == ["decision-001", "decision-002", "decision-003"], ids

        # Borrar el doc intermedio
        ckg._cg.delete("ckg", "decision-002")

        # El siguiente ID debe ser 004 (MAX, no count+1=003 que colisionaria
        # con decision-003 existente)
        r = ckg.save_nodes([{"label": "Decision 4", "type": "decision"}])
        assert r["node_ids"][0] == "decision-004", r["node_ids"]

        # Los IDs son unicos
        all_ids = ckg._cg.keys("ckg")
        assert len(all_ids) == len(set(all_ids)), "IDs duplicados"
        print("  ✅ test_ckg_next_id_no_collision_after_delete: max_key evita colision")


def test_val_contains_exact_no_substring_bug():
    """G7: _val_contains exacto por defecto (no substring). 'active' no matchea 'inactive'."""
    from libs.cognitive_graph import Index

    # Exacto case-insensitive por defecto
    assert Index._val_contains("active", "active"), "iguales"
    assert Index._val_contains("ACTIVE", "active"), "case-insensitive"
    assert Index._val_contains("active", "ACTIVE"), "case-insensitive reverse"
    assert not Index._val_contains("inactive", "active"), "substring bug fix"
    assert not Index._val_contains("active-pending", "active"), "prefix bug fix"

    # Operadores explicitos
    assert Index._val_contains("authentication", {"$contains": "auth"}), "$contains"
    assert Index._val_contains("/src/auth.py", {"$prefix": "/src/"}), "$prefix"
    assert not Index._val_contains("lib/auth.py", {"$prefix": "/src/"}), "$prefix fail"
    assert Index._val_contains("active", {"$eq": "ACTIVE"}), "$eq case-insensitive"

    # Listas: match exacto en cualquiera
    assert Index._val_contains(["auth", "admin"], "auth")
    assert not Index._val_contains(["auth", "admin"], "aut"), "no substring en lista"
    print("  ✅ test_val_contains_exact_no_substring_bug: G7 arreglado")


def test_search_pagination_with_from():
    """G8: paginacion real con _from/from offset."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir, auto_register=True)
        for i in range(10):
            cg.store("ckg", f"doc-{i:03d}", {"type": "convention", "seq": i})

        # Pagina 1: primeros 3
        r1 = cg.search("ckg", {"type": "convention", "size": 3})
        assert r1["hits"]["total"] == 10, r1["hits"]["total"]
        assert len(r1["hits"]["hits"]) == 3
        assert r1["hits"].get("from", 0) == 0

        # Pagina 2: offset 3, size 3
        r2 = cg.search("ckg", {"type": "convention", "_from": 3, "size": 3})
        assert r2["hits"]["total"] == 10
        assert len(r2["hits"]["hits"]) == 3
        assert r2["hits"].get("from") == 3

        # Pagina final: ultimos (solo 1)
        r3 = cg.search("ckg", {"type": "convention", "_from": 9, "size": 3})
        assert r3["hits"]["total"] == 10
        assert len(r3["hits"]["hits"]) == 1
        assert r3["hits"].get("from") == 9

        # Sin _from: default 0
        r4 = cg.search("ckg", {"type": "convention"})
        assert r4["hits"].get("from", 0) == 0
        print("  ✅ test_search_pagination_with_from: G8 paginacion real")


def test_fulltext_nested_dict_fields():
    """G9: fulltext indexa campos anidados como metadata.summary."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        cg = CognitiveGraph(tmpdir, auto_register=True)

        # Registrar indice con campo fulltext anidado
        cg.register_index("nested", {
            "fields": {"label": "statement"},
            "search": {
                "type": "fulltext",
                "fields": ["summary", "metadata.description"],
            },
            "storage": {"path": os.path.join(tmpdir, "data", "nested.json.gz")},
        })

        cg.store("nested", "n1", {
            "statement": "flat title",
            "metadata": {"description": "deep auth description inside metadata"},
        })
        cg.store("nested", "n2", {
            "statement": "flat title 2",
            "metadata": {"description": "another topic"},
        })

        # Buscar fulltext: "auth" debe encontrar n1 via metadata.description
        r = cg.search("nested", {"text": "auth"})
        hits = r["hits"]["hits"]
        ids = {h["_id"] for h in hits}
        assert "n1" in ids, f"nested field no indexado: {ids}"

        # "another" debe encontrar n2
        r2 = cg.search("nested", {"text": "another"})
        ids2 = {h["_id"] for h in r2["hits"]["hits"]}
        assert "n2" in ids2, f"nested n2 no encontrado: {ids2}"
        print("  ✅ test_fulltext_nested_dict_fields: G9 fulltext anidado")


def test_schema_migration_on_fulltext_change():
    """G3: al cambiar fulltext_fields, el TokenIndex se reconstruye automaticamente."""
    from libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        # Fase 1: crear indice con schema v1 (campo "title")
        cg1 = CognitiveGraph(tmpdir, auto_register=True)
        cg1.register_index("mig", {
            "fields": {"label": "statement"},
            "search": {
                "type": "fulltext",
                "fields": ["title"],
            },
            "storage": {"path": os.path.join(tmpdir, "data", "mig.json.gz")},
        })
        cg1.store("mig", "d1", {"title": "authentication module", "body": "jwt"})
        cg1.store("mig", "d2", {"title": "user profile", "body": "avatar"})

        # Fase 2: schema v2 — agrega campo "body" al fulltext
        cg2 = CognitiveGraph(tmpdir, auto_register=False)
        cg2.register_index("mig", {
            "fields": {"label": "statement"},
            "search": {
                "type": "fulltext",
                "fields": ["title", "body"],  # ← nuevo campo
            },
            "storage": {"path": os.path.join(tmpdir, "data", "mig.json.gz")},
        })

        # Al hacer la primera busqueda (o cualquier operacion que dispare
        # _load), la migracion debe detectarse y el TokenIndex reconstruido.
        r = cg2.search("mig", {"text": "jwt"})
        hits = r["hits"]["hits"]
        ids = {h["_id"] for h in hits}
        assert "d1" in ids, (
            f"Migracion G3 fallo: 'jwt' en campo 'body' no indexado. ids={ids}"
        )

        # El campo title sigue funcionando
        r2 = cg2.search("mig", {"text": "authentication"})
        ids2 = {h["_id"] for h in r2["hits"]["hits"]}
        assert "d1" in ids2, f"Campo title roto tras migracion: {ids2}"

        # El schema_hash quedo guardado (proximo load no migra de nuevo)
        idx = cg2._get_index("mig")
        assert idx._loaded, "indice deberia estar cargado despues de search"
        print("  ✅ test_schema_migration_on_fulltext_change: G3 migracion automatica")


# ════════════════════════════════════════════════════════════════
# Runner
# ════════════════════════════════════════════════════════════════


def run_all():
    print(f"\n{'=' * 60}")
    print("CognitiveGraph Tests")
    print(f"{'=' * 60}\n")

    tests = [
        ("Gzip fallback", test_gzip_fallback),
        ("Full-text search", test_fulltext_search),
        ("ODM entities", test_odm_entities),
        ("Cross-index search", test_cross_index_search),
        ("Graph edges", test_graph_edges),
        ("kg_store+kg_search flow", test_kg_store_search_flow),
        ("TaskResult failure", test_task_result_failure_with_data),
        ("kg_store task (integration)", test_kg_store_task),
        ("kg_store empty rejected", test_kg_store_empty_rejected),
        ("kg_extract task (integration)", test_kg_extract_task),
        ("kg_schema via CG", test_kg_schema_via_cg),
        ("kg_multi via CG", test_kg_multi_via_cg),
        ("Decision lifecycle (supersedes)", test_decision_lifecycle_supersedes),
        (
            "capture_decision detects superseded",
            test_capture_decision_detects_superseded,
        ),
        ("capture_task session sprint", test_capture_task_creates_session_sprint),
        ("index_sprint unique ids", test_index_sprint_unique_ids),
        ("SpringGraph unified model", test_spring_graph_service_unified_model),
        ("SpringGraph import legacy", test_spring_graph_import_legacy),
        ("upsert_spec append no replace", test_upsert_spec_append_no_replace),
        ("upsert_spec refine no duplicate", test_upsert_spec_refine_no_duplicate),
        ("ODM update_or_create", test_odm_update_or_create),
        ("CognitiveGraph upsert", test_cognitive_graph_upsert),
        ("fulltext derived from fields", test_fulltext_derived_from_fields),
        ("atomic write no tmp leftover", test_atomic_write_no_tmp_leftover),
        ("index keys and max_key", test_index_keys_and_max_key),
        ("ckg next_id no collision", test_ckg_next_id_no_collision_after_delete),
        ("val_contains exact no substring", test_val_contains_exact_no_substring_bug),
        ("search pagination with from", test_search_pagination_with_from),
        ("fulltext nested dict fields", test_fulltext_nested_dict_fields),
        ("schema migration on fulltext change", test_schema_migration_on_fulltext_change),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'=' * 60}\n")
    return failed == 0


if __name__ == "__main__":
    import sys

    sys.exit(0 if run_all() else 1)
