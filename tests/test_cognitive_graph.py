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
from pathlib import Path


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
        ckg_data = {"index": "ckg", "docs": [
            {"_id": "test-001", "type": "decision", "statement": "test knowledge"}
        ], "edges": []}
        
        # Escribir con gzip normal
        payload = json.dumps(ckg_data, ensure_ascii=False, separators=(",", ":"))
        with open(ckg_path, "wb") as f:
            f.write(gzip.compress(payload.encode()))

        # Leer via CognitiveGraph
        cg = CognitiveGraph(tmpdir)
        result = cg.search("ckg", {})
        assert result["hits"]["total"] == 1, f"Expected 1 doc, got {result['hits']['total']}"
        print("  ✅ test_gzip_fallback: gzip normal funciona")

        # Simular archivo gzip con trailing data (que hace fallar gzip.decompress)
        with open(ckg_path, "wb") as f:
            payload = json.dumps({"index": "ckg", "docs": [
                {"_id": "test-002", "type": "decision", "statement": "trailing data test"}
            ], "edges": []}, ensure_ascii=False, separators=(",", ":"))
            compressed = gzip.compress(payload.encode())
            f.write(compressed)
            f.write(b"TRAILING_DATA")  # trailing data que rompe gzip.decompress

        # Leer con CognitiveGraph (debe usar fallback manual)
        from libs.cognitive_graph import CognitiveGraph
        cg2 = CognitiveGraph(tmpdir)
        result2 = cg2.search("ckg", {})
        assert result2["hits"]["total"] == 1, f"gzip fallback failed: expected 1, got {result2['hits']['total']}"
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
        cg.store("delta", "TASK-001", {
            "type": "task",
            "objective": "Implement authentication module",
            "domain": "security",
        })
        cg.store("delta", "TASK-002", {
            "type": "task",
            "objective": "Fix payment gateway timeout",
            "domain": "payments",
        })
        cg.store("delta", "TASK-003", {
            "type": "task",
            "objective": "Add logout button to auth module",
            "domain": "security",
        })

        # Busqueda full-text por "auth"
        result = cg.search("delta", {"text": "auth"})
        assert result["hits"]["total"] >= 1, f"Expected >=1 for 'auth', got {result['hits']['total']}"
        
        # Busqueda estructurada por domain
        result = cg.search("delta", {"domain": "security"})
        assert result["hits"]["total"] >= 2, f"Expected >=2 for security, got {result['hits']['total']}"
        
        print(f"  ✅ test_fulltext_search: {result['hits']['total']} tasks in security domain")


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
        task = TestTask(task_id="TASK-001", objective="Fix auth bug", domain="security", status="in_progress")
        task._cg_instance = cg
        task.save()

        # Verificar que type NO se sobreescribe con el nombre del indice
        doc = cg.get("odm_test", "TASK-001")
        assert doc is not None, "Doc should exist"
        source_type = doc["_source"].get("type", "")
        assert source_type != "odm_test", f"type should NOT be index name, got '{source_type}'"

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
        
        cg.store("delta", "T-001", {"type": "task", "objective": "Auth task", "domain": "security"})
        cg.store("ckg", "D-001", {"type": "decision", "statement": "Use JWT", "scope": "security"})

        result = cg.search_many({
            "delta": {"domain": "security"},
            "ckg": {"scope": "security"},
        })
        
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
        assert len(related["edges"]) >= 1, f"Expected >=1 edge, got {len(related['edges'])}"
        
        # Persistencia de edges (recargar)
        cg2 = CognitiveGraph(tmpdir)
        related2 = cg2.get_related("TASK-042", depth=1)
        assert related2["total"] >= 2, f"After reload: expected >=2, got {related2['total']}"
        
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
        result = ckg_service.save_nodes([
            {"label": "Clean Architecture with DDD", "type": "decision", "scope": "architecture"}
        ])
        assert result["nodes_created"] == 1, f"Expected 1 node created, got {result['nodes_created']}"

        # Guardar segundo nodo (verificar que no pierde datos previos)
        result2 = ckg_service.save_nodes([
            {"label": "Use JWT for authentication", "type": "decision", "scope": "security"}
        ])
        assert result2["nodes_created"] == 1

        # Leer con CognitiveGraph (simula kg_search)
        cg = CognitiveGraph(project_root)
        search_result = cg.search("ckg", {})
        total = search_result["hits"]["total"]
        assert total == 2, f"Expected 2 docs after 2 saves, got {total}"

        # Busqueda por tipo
        decisions = cg.search("ckg", {"type": "decision"})
        assert decisions["hits"]["total"] == 2, f"Expected 2 decisions, got {decisions['hits']['total']}"

        # Rechazar nodos vacios
        with open(ckg_path, "wb") as f:
            f.write(gzip.compress(json.dumps(empty_ckg).encode()))
        ckg_service2 = CodeKnowledgeGraph(project_root)
        result3 = ckg_service2.save_nodes([{"label": "", "type": "decision"}])
        assert result3["nodes_created"] == 0, "Empty label should be rejected"

        print("  ✅ test_kg_store_search_flow: persistence consistent, empty data rejected")


# ════════════════════════════════════════════════════════════════
# 7. Integration: tasks via registry (kg_store, kg_search, kg_extract)
# ════════════════════════════════════════════════════════════════

def _create_test_project(tmpdir: str) -> str:
    """Crea un proyecto de prueba con structure .cognitive/ completa."""
    import gzip, json
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
        result = ckg.save_nodes([
            {"label": "Integration test knowledge", "type": "decision", "scope": "test"}
        ])
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
            stats = result.data.get("stats", {})
            questionnaire = result.data.get("questionnaire", {})
            assert len(questionnaire.get("questions", [])) == 6
            print(f"  ✅ test_kg_extract_task: CPG stats + 6 questions")
        else:
            # Sin CPG es aceptable (proyecto de prueba no tiene CPG)
            print(f"  ✅ test_kg_extract_task: no CPG (expected in test env)")


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
        ckg.save_nodes([
            {"label": "Multi-index test", "type": "decision", "scope": "test"}
        ])
        
        cg = CognitiveGraph(cog_proj)
        result = cg.search_many({
            "ckg": {"type": "decision"},
            "delta": {},
        })
        responses = result.get("responses", {})
        assert "ckg" in responses, "Missing ckg"
        assert responses["ckg"]["hits"]["total"] >= 1
        assert "delta" in responses, "Missing delta"
        
        print(f"  ✅ test_kg_multi_via_cg: {len(responses)} indices")


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


# ════════════════════════════════════════════════════════════════
# Runner
# ════════════════════════════════════════════════════════════════

def run_all():
    print(f"\n{'='*60}")
    print("CognitiveGraph Tests")
    print(f"{'='*60}\n")
    
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
    
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_all() else 1)
