"""test_gps_impact_bdd.py — el mapa que el GPS no estaba dibujando.

El workflow se presenta como control de vuelo: dónde estoy, a qué afecta el
cambio, cómo llego. Dos piezas prometían eso y devolvían vacío:

1. `CodeContextUseCase.for_file` filtraba los nodos del CPG por
   `hasattr(n, "file")`, pero los nodos exponen `path`. `file_nodes` salía
   vacío SIEMPRE y `relations` se devolvía en cero aunque el grafo tuviera
   16.796 edges (medido: 17 símbolos, 0 calls, 0 called_by en un archivo real).

2. `agent_context` decía "delta+ckg+cpg+trace con cross-references" y devolvía
   solo CKG. Es el tool que el agente más llama: la mitad del mapa nunca llegaba.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.code_analysis.application.use_cases.code_context import (
    CodeContextUseCase,
)


class _Node:
    def __init__(self, id, name, path, type_="function", cyclo=None, line=0):
        self.id = id
        self.name = name
        self.path = path
        self.type = type_
        self.cyclo = cyclo
        self.line = line


class _Edge:
    def __init__(self, from_id, to_id, type_="CALLS"):
        self.from_id = from_id
        self.to_id = to_id
        self.type = type_


class _Graph:
    def __init__(self, nodes, edges):
        self.nodes = {n.id: n for n in nodes}
        self.edges = edges


class _UseCase:
    def __init__(self, symbols):
        self._symbols = symbols

    def read(self, target_file):
        return {"symbols": self._symbols}


def _uc(graph):
    return CodeContextUseCase(_UseCase([{"name": "crear_promocion", "line": 10}]), graph)


def test_for_file_encuentra_los_nodos_por_path_no_por_file():
    """El bug: el CPG expone `path`; filtrar por `file` daba siempre vacío."""
    graph = _Graph(
        nodes=[
            _Node("a", "crear_promocion", "/repo/promotion/views.py"),
            _Node("b", "serializer", "/repo/promotion/serializers.py"),
        ],
        edges=[_Edge("b", "a")],
    )

    out = _uc(graph).for_file("promotion/views.py")

    assert out["relations"]["called_by"], "promotion/views.py es llamado desde serializers"
    assert out["relations"]["called_by"][0]["name"] == "serializer"


def test_for_file_lista_llamadas_salientes():
    graph = _Graph(
        nodes=[
            _Node("a", "crear_promocion", "/repo/promotion/views.py"),
            _Node("c", "validar", "/repo/promotion/rules.py"),
        ],
        edges=[_Edge("a", "c")],
    )

    out = _uc(graph).for_file("promotion/views.py")

    assert [x["name"] for x in out["relations"]["calls"]] == ["validar"]


def test_for_file_expone_cyclo_por_simbolo():
    """El baseline necesita el peso, no solo la lista de símbolos."""
    graph = _Graph(
        nodes=[_Node("a", "crear_promocion", "/repo/promotion/views.py", cyclo=12, line=40)],
        edges=[],
    )

    out = _uc(graph).for_file("promotion/views.py")

    sym = out["symbols"][0]
    assert sym["cyclo"] == 12
    assert sym["line"] == 40


def test_for_file_sin_nodos_sigue_devolviendo_vacio():
    out = _uc(_Graph(nodes=[], edges=[])).for_file("nada.py")

    assert out["relations"] == {"calls": [], "called_by": [], "imports": []}


# ── agent_context: tres capas, no una ────────────────────────────────────


@pytest.fixture()
def ctx(monkeypatch):
    calls = {}

    class _Ctx:
        chain_dir = "/tmp/proyecto"
        project_id = "proyecto"

        def code_context_service(self):
            calls["file"] = True
            return SimpleNamespace(
                execute=lambda target_file, query: {
                    "symbols": [{"name": "crear_promocion"}],
                    "relations": {
                        "calls": [{"name": f"c{i}"} for i in range(25)],
                        "called_by": [{"name": "x"}],
                        "imports": [],
                    },
                }
            )

    from fast_mcp.features.cognitive_knowledge.application.services import ckg_read
    from fast_mcp.features.event_tracing.application.services import trace_factory

    monkeypatch.setattr(
        ckg_read.CkgReadService, "gather_context",
        classmethod(lambda cls, chain_dir, query="": __import__("returns").result.Success([{"id": "d1"}])),
    )
    monkeypatch.setattr(
        trace_factory, "build_trace_service",
        lambda cd: SimpleNamespace(load_traces=lambda c: []),
    )
    return _Ctx()


def test_agent_context_devuelve_las_tres_capas(ctx):
    from fast_mcp.memories.handlers.knowledge import handle_agent_context

    out = handle_agent_context(ctx, file="promotion/views.py", text="promo")

    assert set(out) >= {"ckg", "cpg", "trace"}
    assert out["ckg"]["results"] == [{"id": "d1"}]
    assert out["cpg"]["symbols"][0]["name"] == "crear_promocion"


def test_agent_context_acota_relaciones_e_informa_el_total(ctx):
    from fast_mcp.memories.handlers.knowledge import handle_agent_context

    rel = handle_agent_context(ctx, file="promotion/views.py")["cpg"]["relations"]

    assert len(rel["calls"]) == 10
    assert rel["calls_total"] == 25, "el total no se pierde al recortar"
