from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ckg import (
    CodeKnowledgeGraph,
    Node,
    NodeType,
    Edge,
    QltyScanner,
    SymbolScanner,
    JsonRepository,
)
from ckg.application.detection import detect_issues as detect_issues_detection
from ckg.application.migration import migrate_from_facts as migrate_from_facts_migration
from ckg.application.context import build_context
from ckg.infrastructure.scanners import ImportScanner as IS


EXPECTED_FILES = [
    "domain/models.py",
    "domain/errors.py",
    "domain/graph.py",
    "application/ckg.py",
    "application/context.py",
    "application/detection.py",
    "application/migration.py",
    "infrastructure/repository.py",
    "infrastructure/scanners.py",
    "infrastructure/schema.py",
    "infrastructure/qlty.py",
]


def test_module_files_exist():
    base = ROOT / "ckg"
    for rel in EXPECTED_FILES:
        assert (base / rel).exists(), f" Falta {rel}"


def test_import_no_error():
    from ckg.application.ckg import CodeKnowledgeGraph as CKGCore
    from ckg.domain.models import Node as NodeModel
    from ckg.domain.graph import KnowledgeGraph as KGProtocol

    assert CKGCore is not None
    assert NodeModel is not None
    assert KGProtocol is not None


def test_save_load_roundtrip(tmp_path):
    g = CodeKnowledgeGraph(str(tmp_path))
    g.add_node(Node(id="n1", type=NodeType.FILE, label="a.py"))
    g.add_node(Node(id="n2", type=NodeType.FILE, label="b.py"))
    g.add_edge(Edge(source="n1", target="n2", type="depends_on"))

    out = tmp_path / "ckg.json"
    g.save(str(out))
    assert out.exists()

    g2 = CodeKnowledgeGraph.load(str(out))
    assert len(g2.nodes) == len(g.nodes)
    assert len(g2.edges) == len(g.edges)


def test_context_for_files_returns_legible_string():
    g = CodeKnowledgeGraph(".")
    g.add_node(Node(id="n1", type=NodeType.FILE, label="file.py"))
    result = build_context(g, ["n1"], depth=2)
    assert isinstance(result, str)
    assert "file.py" in result


def test_detect_issues_finds_cycle():
    g = CodeKnowledgeGraph(".")
    g.add_node(Node(id="a", type=NodeType.FILE, label="a.py"))
    g.add_node(Node(id="b", type=NodeType.FILE, label="b.py"))
    g.add_edge(Edge(source="a", target="b", type="depends_on"))
    g.add_edge(Edge(source="b", target="a", type="depends_on"))
    issues = detect_issues_detection(g)
    assert any(issue.get("type") == "cycle" for issue in issues)


def test_migrate_from_facts_transforms_facts():
    g = CodeKnowledgeGraph(".")
    facts = [
        {"id": "f1", "type": "file", "label": "main.py", "depends_on": ["lib"]},
    ]
    migrate_from_facts_migration(g, facts)
    assert any(n.id == "f1" for n in g.nodes.values())


def test_import_scanner_generates_depends_on_edges():
    scanner = IS(str(ROOT / "ckg"))
    edges = scanner.scan()
    assert any(e.type == "DEPENDS_ON" for e in edges)


def test_no_node_without_valid_nodetype():
    g = CodeKnowledgeGraph(".")
    g.add_node(Node(id="x", type=NodeType.FILE, label="bad.py"))
    assert g.nodes["x"].type is NodeType.FILE


def test_qlty_scanner_runs():
    scanner = QltyScanner(str(ROOT))
    result = scanner.run()
    assert isinstance(result, dict)


def test_symbol_scanner_finds_symbols():
    scanner = SymbolScanner(str(ROOT))
    symbols = scanner.scan()
    assert isinstance(symbols, dict)


def test_json_repository_read_write(tmp_path):
    repo = JsonRepository(str(tmp_path / "ckg.json"))
    repo.write({"nodes": [], "edges": []})
    data = repo.read()
    assert data == {"nodes": [], "edges": []}
