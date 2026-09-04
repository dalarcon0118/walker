"""tests/benchmarks/cpg_fixture.py — Fixture determinista 16K docs (shape cpg real).

ADR-WALKER-075 §8 `benchmark_fixture` / §9 Fase M0. El shape es el SSOT de
`fast_mcp/features/code_analysis/schema.py`:

    docs:   {name, type, path, line, signature, kind}
    types:  function | class | file | module
    fulltext (search.fields): name, path, signature, kind

Propiedades:
  - DETERMINISTA: `random.Random(SEED)` + ids/names derivados del índice —
    dos corridas producen el fixture byte-idéntico (baseline reproducible).
  - NACE LIMPIO (H-3): todo edge `(from, to)` referencia docs del MISMO
    índice — el fixture jamás contamina un baseline con suciedad propia.
  - 16000 docs (12K functions + 3K classes + 700 modules + 300 files) y
    24000 edges (~1.5 calls/function + contains) — la "forma" del CPG
    mediano histórico (16K docs + 24K edges, ver SqliteIndex._do_load).
"""

from __future__ import annotations

import random

SEED = 42

N_FUNCTION = 12000
N_CLASS = 3000
N_MODULE = 700
N_FILE = 300
N_CALL_EDGES = 18000
N_CONTAINS_EDGES = 6000  # 2K module→class + 3K class→function + 1K file→function

# SSOT del schema (fast_mcp/features/code_analysis/schema.py — register_cpg_index)
CPG_SCHEMA_CONFIG: dict = {
    "fields": {"function": "name", "file": "path", "type": "type"},
    "types": {"function": {}, "class": {}, "file": {}, "module": {}},
    "search": {
        "type": "fulltext",
        "fields": ["name", "path", "signature", "kind"],
    },
    "description": "CPG: Code Property Graph - estructura del codigo fuente "
    "(fixture determinista M0 — ADR-WALKER-075 §9)",
}


def build_cpg_fixture() -> tuple[dict[str, dict], list[dict]]:
    """Genera (docs, edges) deterministas con shape cpg real.

    Returns:
        docs: {doc_id: {name, type, path, line, signature, kind}}
        edges: [{"from": doc_id, "to": doc_id, "rel": "calls"|"contains"}]
    """
    rng = random.Random(SEED)
    docs: dict[str, dict] = {}

    file_ids: list[str] = []
    for i in range(N_FILE):
        doc_id = f"cpg:file:f{i:04d}"
        docs[doc_id] = {
            "name": f"file_{i}.py",
            "type": "file",
            "path": f"src/pkg{i // 30}/file_{i}.py",
            "line": 1,
            "signature": "",
            "kind": "file",
        }
        file_ids.append(doc_id)

    module_ids: list[str] = []
    for i in range(N_MODULE):
        doc_id = f"cpg:module:m{i:04d}"
        docs[doc_id] = {
            "name": f"module_{i}",
            "type": "module",
            "path": f"src/pkg{i // 10}/module_{i}.py",
            "line": 1,
            "signature": "",
            "kind": "module",
        }
        module_ids.append(doc_id)

    class_ids: list[str] = []
    for i in range(N_CLASS):
        doc_id = f"cpg:class:c{i:05d}"
        docs[doc_id] = {
            "name": f"Class_{i}",
            "type": "class",
            "path": f"src/pkg{i % 250}/cls_{i}.py",
            "line": 10 + (i % 900),
            "signature": f"class Class_{i}(Base_{i % 97}):",
            "kind": "class",
        }
        class_ids.append(doc_id)

    function_ids: list[str] = []
    for i in range(N_FUNCTION):
        doc_id = f"cpg:function:fn{i:05d}"
        docs[doc_id] = {
            "name": f"fn_{i}",
            "type": "function",
            "path": f"src/pkg{i % 250}/fn_{i}.py",
            "line": 20 + (i % 700),
            "signature": f"def fn_{i}(arg_a: int, arg_b: str = 'x{i % 13}') -> bool:",
            "kind": "def",
        }
        function_ids.append(doc_id)

    edges: list[dict] = []
    for _ in range(N_CALL_EDGES):
        edges.append(
            {
                "from": function_ids[rng.randrange(N_FUNCTION)],
                "to": function_ids[rng.randrange(N_FUNCTION)],
                "rel": "calls",
            }
        )
    # contains: module→class (2K), class→function (3K), file→function (1K)
    for i in range(2000):
        edges.append(
            {
                "from": module_ids[rng.randrange(N_MODULE)],
                "to": class_ids[rng.randrange(N_CLASS)],
                "rel": "contains",
            }
        )
    for i in range(3000):
        edges.append(
            {
                "from": class_ids[rng.randrange(N_CLASS)],
                "to": function_ids[rng.randrange(N_FUNCTION)],
                "rel": "contains",
            }
        )
    for i in range(1000):
        edges.append(
            {
                "from": file_ids[rng.randrange(N_FILE)],
                "to": function_ids[rng.randrange(N_FUNCTION)],
                "rel": "contains",
            }
        )

    # Sanidad H-3: nace sin huérfanos (jamás un benchmark sucio de fábrica).
    for e in edges:
        assert e["from"] in docs and e["to"] in docs, f"edge huérfano: {e}"

    return docs, edges


def sample_search_terms(docs: dict[str, dict], n: int = 100) -> list[str]:
    """Términos que SÍ tienen hits (nombres reales del fixture, deterministas)."""
    rng = random.Random(SEED)
    names = [d["name"] for d in docs.values() if d["type"] == "function"]
    return [rng.choice(names) for _ in range(n)]
