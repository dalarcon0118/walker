"""test_implement_sprint_selection_bdd.py — selección de sprint en implement_task.

Cubre el bug: _select_spring devolvía el primer spring que poseía la sesión
sin preferir el sprint actual del registro ni filtrar cerrados, escribiendo
specs en sprints cerrados.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.handlers import implement
from fast_mcp.features.tasks.handlers import _di


def _sid(s):
    return str(s.get("spring_id") or s.get("id") or s.get("_id") or "")


class _FakeService:
    def __init__(self, springs=None, session=None, active=None):
        self.springs = [dict(s) for s in (springs or [])]
        self.session = dict(session) if session else None
        self._active = active
        self.specs = {}
        for s in self.springs:
            self.specs.setdefault(_sid(s), [])
        self.created = []

    def get_session(self, session_id):
        if self.session and self.session.get("session_id") == session_id:
            return dict(self.session)
        return None

    def list_springs(self, status=""):
        if status:
            return [dict(s) for s in self.springs if s.get("status") == status]
        return [dict(s) for s in self.springs]

    def get_active_spring(self):
        if self._active is not None:
            return dict(self._active) if self._active else None
        for s in self.springs:
            if s.get("status") == "active":
                return dict(s)
        return None

    def create_spring(self, spring_id, goal="", status="planning", **extra):
        sp = {"spring_id": spring_id, "status": status, "goal": goal}
        sp.update(extra)
        self.springs.append(sp)
        self.specs.setdefault(spring_id, [])
        self.created.append(sp)
        return dict(sp)

    def get_spring(self, spring_id):
        for s in self.springs:
            if _sid(s) == str(spring_id):
                return dict(s)
        return None

    def get_specs(self, spring_id, status=""):
        out = [dict(x) for x in self.specs.get(str(spring_id), [])]
        if status:
            out = [x for x in out if x.get("status") == status]
        return out

    def add_spec(self, spring_id, objective, files=None, spec_type="feature",
                 status="draft", description=""):
        n = len(self.specs.setdefault(str(spring_id), [])) + 1
        spec = {
            "spring_id": str(spring_id), "spec_id": f"SPEC-{n:03d}",
            "objective": objective, "files": list(files or []), "spec_type": spec_type,
            "status": status, "description": description, "task_context": {},
            "acceptance_criteria": [], "preconditions": [], "predicates": {},
        }
        self.specs[str(spring_id)].append(spec)
        return dict(spec)

    def update_spec(self, spring_id, spec_id, updates):
        for spec in self.specs.get(str(spring_id), []):
            if spec.get("spec_id") == spec_id:
                spec.update(updates or {})
                return dict(spec)
        return None


@pytest.fixture()
def svc_factory(monkeypatch):
    def _make(springs=None, session=None, active=None):
        svc = _FakeService(springs=springs, session=session, active=active)
        monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: svc)
        return svc
    return _make


def _ctx(session_id="ses-1"):
    return SimpleNamespace(chain_dir="/tmp/proyecto", task_id="", session_id=session_id, cpg_adapter=None)


def test_closed_and_active_both_own_session_active_wins(svc_factory):
    svc = svc_factory(
        springs=[
            {"spring_id": "SPR-closed", "status": "closed", "session_id": "ses-1"},
            {"spring_id": "SPR-active", "status": "active", "session_id": "ses-1"},
        ],
        session=None,
    )
    chosen = implement._select_spring(svc, "ses-1")
    assert chosen is not None
    assert _sid(chosen) == "SPR-active"
    assert chosen.get("status") == "active"


def test_session_record_points_to_active_wins_even_if_another_first(svc_factory):
    svc = svc_factory(
        springs=[
            {"spring_id": "SPR-first", "status": "planning", "session_id": "ses-1"},
            {"spring_id": "SPR-active", "status": "active", "session_id": "ses-other"},
        ],
        session={"session_id": "ses-1", "spring_id": "SPR-active"},
        active=None,
    )
    chosen = implement._select_spring(svc, "ses-1")
    assert chosen is not None
    assert _sid(chosen) == "SPR-active"


def test_only_closed_returns_none_and_no_spec_created(svc_factory):
    svc = svc_factory(
        springs=[
            {"spring_id": "SPR-closed", "status": "closed", "session_id": "ses-1"},
        ],
        session={"session_id": "ses-1", "spring_id": "SPR-closed"},
    )
    out = implement.implement_task(
        _ctx("ses-1"), {"task_id": "T-1", "objective": "x", "files": [], "session_id": "ses-1"}
    )
    assert not out.ok
    assert "no hay sprint vivo" in (out.error or "")
    # no spec created anywhere
    assert all(len(v) == 0 for v in svc.specs.values())
    # _select_spring itself None
    assert implement._select_spring(svc, "ses-1") is None


def test_no_springs_adhoc_behavior(svc_factory):
    svc = svc_factory(springs=[], session=None)
    out = implement.implement_task(
        _ctx("ses-1"), {"task_id": "T-1", "objective": "x", "files": [], "session_id": "ses-1"}
    )
    assert out.ok
    assert "SPR-ADHOC" in svc.specs or any("SPR-ADHOC" in k for k in svc.specs)
    assert svc.created and svc.created[0]["spring_id"] == "SPR-ADHOC"


def test_paused_owned_chosen(svc_factory):
    svc = svc_factory(
        springs=[
            {"spring_id": "SPR-paused", "status": "paused", "session_id": "ses-1"},
        ],
        session={"session_id": "ses-1", "spring_id": "SPR-paused"},
    )
    chosen = implement._select_spring(svc, "ses-1")
    assert chosen is not None
    assert _sid(chosen) == "SPR-paused"
    assert chosen.get("status") == "paused"
