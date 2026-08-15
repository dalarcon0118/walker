"""tests/test_workflow_action_completeness_bdd.py — Checklist bidireccional de completitud de workflow actions.

SPEC-014 Fase 0: valida que no haya names huérfanos en el workflow ni
handlers sin uso en el catálogo.

Checklist BIDIRECCIONAL:
- {action} ∪ {side_effect.type} ∪ {compensation.type} ⊆ ActionCatalog
- ActionCatalog ⊆ nodos + compensaciones internas (0 fantasmas)
"""
from __future__ import annotations

import glob
import json
import os

import pytest

from features.main.container import AppContainer


@pytest.fixture(scope="module")
def catalog():
    c = AppContainer.build()
    se = c.side_effect_engine
    task_registry = c.task_registry
    return {
        "side_effects": set(getattr(se, "_handlers", {}).keys()),
        "tasks": set(task_registry.list_tasks()) if hasattr(task_registry, "list_tasks") else set(),
    }


def _collect_node_names() -> tuple[set[str], set[str]]:
    """Recolecta action, side_effect.type y compensation.type de todos los nodos."""
    actions: set[str] = set()
    side_effects: set[str] = set()
    compensations: set[str] = set()

    for path in glob.glob("core/engine/workflow/nodes/*.json"):
        with open(path) as f:
            node = json.load(f)

        # action del sub_state actual
        action = node.get("action", "")
        if action:
            actions.add(action)

        # side_effects y compensations de cada sub_state
        for ss in node.get("sub_states", []):
            se_list = ss.get("side_effects", []) or []
            for se in se_list:
                if isinstance(se, dict):
                    se_type = se.get("type", "")
                    if se_type:
                        side_effects.add(se_type)
                    comp = se.get("compensation")
                    if isinstance(comp, dict):
                        comp_type = comp.get("type", "")
                        if comp_type:
                            compensations.add(comp_type)
                    elif isinstance(comp, str) and comp:
                        compensations.add(comp)
                elif isinstance(se, str) and se:
                    side_effects.add(se)

    return actions, side_effects, compensations


def test_no_orphan_action_names_in_workflow(catalog):
    """Todo action declarado en los nodos existe en el TaskRegistry."""
    actions, _, _ = _collect_node_names()
    missing = actions - catalog["tasks"]
    assert not missing, f"Actions sin handler en TaskRegistry: {sorted(missing)}"


def test_no_orphan_side_effect_names_in_workflow(catalog):
    """Todo side_effect.type declarado en los nodos existe en el SideEffectEngine."""
    _, side_effects, _ = _collect_node_names()
    missing = side_effects - catalog["side_effects"]
    assert not missing, f"Side effects sin handler: {sorted(missing)}"


def test_no_orphan_compensation_names_in_workflow(catalog):
    """Todo compensation.type declarado en los nodos existe en el SideEffectEngine."""
    _, _, compensations = _collect_node_names()
    missing = compensations - catalog["side_effects"]
    assert not missing, f"Compensations sin handler: {sorted(missing)}"


def test_bidirectional_completeness(catalog):
    """Checklist BIDIRECCIONAL: todo handler registrado tiene uso en el workflow."""
    _, side_effects, compensations = _collect_node_names()
    used = side_effects | compensations
    # Un handler está usado si aparece como side_effect O como compensation
    orphans = catalog["side_effects"] - used
    # Los handlers huérfanos conocidos están permitidos hasta Fase 1
    known_orphans = {"detect_decisions", "rollback_ckg"}
    # También permitir handlers que son compensaciones reales
    compensation_only = {"delete_ckg_nodes", "restore_cpg_snapshot"}
    allowed = known_orphans | compensation_only
    unexpected = orphans - allowed
    assert not unexpected, f"Handlers sin uso en workflow: {sorted(unexpected)}"


def test_three_active_bugs_registered(catalog):
    """Los 3 bugs activos de SPEC-014 están registrados."""
    required = {"advance_sprint_task", "create_adr", "documenting_knowledge"}
    missing = required - catalog["side_effects"]
    assert not missing, f"Handlers de Fase 0 no registrados: {sorted(missing)}"
