"""BDD: el prompt inyecta una señal, no el catálogo ni el mismo hecho tres veces.

Medido 2026-11-18: `VERIFY.QA` metía 1.059 chars de catálogo de skills (6) y el
agente cargaba una igual; y una misma decisión aparecía en `knowledge_global`,
`knowledge_relevant` y `project_patterns` del mismo prompt. Chroma mide que un
distractor cercano ya degrada el rendimiento, y el repetido es el peor caso.
"""
from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.workflow.application.prompts import collector
from fast_mcp.features.workflow.application.prompts.manager import (
    PromptManager,
    _dedupe_knowledge,
)


def _manager() -> PromptManager:
    return PromptManager(chain_dir="/tmp/proyecto", ckg_searcher=object())


def _skill(name: str, description: str = "hace algo"):
    return SimpleNamespace(name=name, description=description)


# ── skills: top-1 con escape ────────────────────────────────────────────

def test_skills_devuelve_solo_el_top_1(monkeypatch):
    monkeypatch.setattr(
        collector,
        "collect_skills",
        lambda chain_dir, intent, limit=6: [
            _skill("ponytail-review", "review de calidad"),
            _skill("code-review-checklist", "checklist"),
            _skill("frontend-design", "diseño"),
        ],
    )

    out = _manager()._skills_data({"skill_intent": {"needs": "review"}})

    catalog = out["skills_catalog"]
    assert catalog.count("- `") == 1
    assert "ponytail-review" in catalog
    assert "frontend-design" not in catalog
    # El escape tiene que estar: no listar todas no puede quitar la capacidad.
    assert "search_skills" in catalog
    assert out["skills_count"] == "3"


def test_skills_sin_match_lo_dice(monkeypatch):
    monkeypatch.setattr(collector, "collect_skills", lambda *a, **k: [])

    out = _manager()._skills_data({"skill_intent": {"needs": "x"}})

    assert "Sin skills" in out["skills_catalog"]
    assert out["skills_count"] == "0"


def test_skills_sin_intent_no_inyecta(monkeypatch):
    monkeypatch.setattr(collector, "collect_skills", lambda *a, **k: [_skill("x")])

    assert _manager()._skills_data({}) == {}


# ── conocimiento: un hecho, una sección ────────────────────────────────

def test_dedupe_quita_lineas_repetidas():
    sections = {
        "a": "- ADR-016: cli_interface\n- Regla: X",
        "b": "ADR-016:  CLI_INTERFACE\n- Otra: Y",
    }

    out = _dedupe_knowledge(sections)

    assert out["a"].count("ADR-016") == 1
    assert "ADR-016" not in out["b"]
    assert "Otra: Y" in out["b"]


def test_dedupe_conserva_el_orden_de_prioridad():
    sections = {
        "relevant": "- decisión A",
        "global": "- decisión B",
    }

    out = _dedupe_knowledge(sections)

    assert out["relevant"] == "- decisión A"
    assert out["global"] == "- decisión B"


def test_knowledge_no_repite_un_hecho_entre_secciones(monkeypatch):
    shared = "- ADR-WALKER-016: cli_interface"
    monkeypatch.setattr(
        collector,
        "collect_knowledge",
        lambda chain_dir, data, ckg_searcher=None: {
            "relevant": [{"statement": "ADR-WALKER-016: cli_interface"}],
            "global": [{"statement": "ADR-WALKER-016: cli_interface"}, {"statement": "Regla global"}],
            "architecture": [{"statement": "ADR-WALKER-016: cli_interface"}],
        },
    )
    monkeypatch.setattr(
        collector,
        "collect_project_info",
        lambda chain_dir, ckg_searcher=None: {
            "architecture": "ADR-WALKER-016: cli_interface",
            "patterns": ["ADR-WALKER-016: cli_interface", "Patrón B"],
            "quality_gates": [],
        },
    )

    out = _manager()._knowledge_data({"node_id": "DESIGN"})

    combined = "\n".join(out.values())
    assert combined.count("cli_interface") == 1
    assert "Regla global" in combined
    assert "Patrón B" in combined


def test_knowledge_cap_por_seccion(monkeypatch):
    many = [{"statement": f"hecho {i}"} for i in range(20)]
    monkeypatch.setattr(
        collector, "collect_knowledge", lambda *a, **k: {"relevant": many, "global": [], "architecture": []}
    )
    monkeypatch.setattr(
        collector,
        "collect_project_info",
        lambda *a, **k: {"architecture": "", "patterns": [], "quality_gates": []},
    )

    out = _manager()._knowledge_data({"node_id": "X"})

    assert len(out["knowledge_relevant"].splitlines()) <= 5


def test_sin_searcher_no_inyecta_conocimiento():
    manager = PromptManager(chain_dir="/tmp/proyecto", ckg_searcher=None)

    assert manager._knowledge_data({"node_id": "X"}) == {}
