#!/usr/bin/env python3
"""Tests end-to-end para add-task, update-task, advance, approve-task."""

import json
import os
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))  # .../delta
_BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(_SKILL_DIR))
)  # project root


def _spring():
    return os.path.join(_BASE_DIR, ".claude", "skills", "delta", "scripts", "spring.py")


def _task():
    return os.path.join(_BASE_DIR, ".claude", "skills", "delta", "scripts", "task.py")


def _chain():
    return os.path.join(
        _BASE_DIR,
        ".cognitive",
        "projects",
        "delta-skill",
        "delta",
        "skill-spring-delta",
    )


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


PASS = 0
FAIL = 0


def _check(ok, msg):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"✅ {msg}")
    else:
        FAIL += 1
        print(f"❌ {msg}")


def test_add_rejects_incomplete():
    rc, out, err = _run(
        [
            "python3",
            _spring(),
            "add-task",
            _chain(),
            "--task",
            json.dumps(
                {
                    "task_id": "TEST-1",
                    "type": "feature",
                    "domain": "tooling",
                    "task_description": "test",
                    "objective": "test sin criterios reales definidos",
                    "result_state": "test completado correctamente sin datos suficientes",
                    "scrum_role": "dev",
                    "validation": {
                        "predicates": [{"type": "command_exit", "command": "true"}]
                    },
                    "impacted_artifacts": {"files": ["x.py"]},
                    "risks": {"curvature": 0.1, "pre_churn": 0.1, "commit_trust": 0.9},
                }
            ),
        ]
    )
    _check(
        "QUALITY GATE" in out + err,
        "add-task rechaza tarea sin criteria/gates/preconditions",
    )


def test_add_accepts_complete():
    rc, out, err = _run(
        [
            "python3",
            _spring(),
            "add-task",
            _chain(),
            "--task",
            json.dumps(
                {
                    "task_id": "TEST-2",
                    "type": "feature",
                    "domain": "tooling",
                    "task_description": "test completo",
                    "objective": "test con criterios gates y preconditions reales",
                    "result_state": "test completado correctamente con todos los campos requeridos",
                    "scrum_role": "dev",
                    "validation": {
                        "predicates": [{"type": "command_exit", "command": "true"}]
                    },
                    "impacted_artifacts": {"files": ["x.py"]},
                    "risks": {"curvature": 0.1, "pre_churn": 0.1, "commit_trust": 0.9},
                    "acceptance_criteria": [
                        "El comando se ejecuta sin errores en consola"
                    ],
                    "quality_gates": ["El codigo no introduce efectos secundarios"],
                    "preconditions": [
                        "Python 3.10 instalado y configurado correctamente"
                    ],
                }
            ),
        ]
    )
    _check(rc == 0 and "TASK ADDED" in out, "add-task acepta tarea completa")


def test_advance_blocked():
    rc, out, err = _run(["python3", _task(), "advance", _chain(), "TEST-2"])
    _check("BLOQUEADO" in out + err, "advance bloquea sin PO approval")


def test_approve_unblocks():
    rc, out, err = _run(["python3", _spring(), "approve-task", _chain(), "TEST-2"])
    _check(rc == 0 and "PO APPROVED" in out, "approve-task marca aprobada")


def test_cleanup():
    path = os.path.join(_chain(), "plan.json")
    with open(path) as f:
        plan = json.load(f)
    plan["plan"]["tasks"] = [
        t for t in plan["plan"]["tasks"] if not t["task_id"].startswith("TEST-")
    ]
    with open(path, "w") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    _check(True, "cleanup: TEST tasks removed")


if __name__ == "__main__":
    print("=" * 60)
    print("TESTS: skill delta e2e")
    print("=" * 60)
    test_add_rejects_incomplete()
    test_add_accepts_complete()
    test_advance_blocked()
    test_approve_unblocks()
    test_cleanup()
    print()
    print(f"RESULTADO: {PASS} pasaron, {FAIL} fallaron")
    sys.exit(0 if FAIL == 0 else 1)
