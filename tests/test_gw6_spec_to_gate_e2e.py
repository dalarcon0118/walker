"""tests/test_gw6_spec_to_gate_e2e.py — E2E anti-alucinación (G-W6): spec → gate → motor.

Ejercita la cadena REAL de producción (sin mocks del delta):

  1. Fixture: proyecto con data/project.json + CognitiveGraph + código REAL
     (símbolos verificables) + chain_dir bajo data/spring/.
  2. Se siembra un spring + spec ACTUAL en el DeltaGraph (vía SpringService
     real) con validation.predicates.
  3. El engine inyecta los predicates de la spec mediante el MISMO método
     que usa SubmitDataUseCase._execute_task en runtime:
         fields = self._inject_spec_predicates(chain_dir, task_id, fields)
  4. El gate ejecuta la MISMA tarea registrada en producción:
         registry.register("verify_predicates", verify_predicates, ...)
         registry.execute("verify_predicates", ctx, fields)

Garantías cerradas (G-W6 ante alucinación):
  - La spec del delta es la fuente única de predicates (SSOT): con spec
    con predicates, la cadena pasa CONTRA el código real.
  - Spec SIN predicates → fields vacíos → el gate FALLA (no más all_passed
    no-op).
  - Predicate que cita símbolo inexistente → FAIL (el motor verifica de
    verdad, no confirma).
  - Si el input del agente ya trae predicates, se respetan (no se pisa la
    intención legítima).
"""
from __future__ import annotations

import json

import pytest

from features.delta_tracking import spring_service
from core.engine.application.use_cases import SubmitDataUseCase
from core.engine.domain.models import ExecutionContext
from features.delta_tracking.tasks.verification import register as register_verify_tasks
from core.engine.infrastructure.task_registry import InMemoryTaskRegistry


# ═══════════════════════════════════════════════════════════════════
# Fixtures: proyecto real + delta real
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def env(tmp_path):
    """Proyecto real con código verificable, delta (CognitiveGraph) y chain."""
    from shared.core.cognitive_graph import CognitiveGraph

    proj = tmp_path / "proj"
    data_dir = proj / "data"
    (data_dir / "spring").mkdir(parents=True, exist_ok=True)
    with open(data_dir / "project.json", "w") as f:
        json.dump({"project_id": "prodemo", "name": "Demo G-W6"}, f)

    # Código REAL: símbolos que el motor debe encontrar
    src = proj / "src"
    src.mkdir(exist_ok=True)
    (src / "hello.py").write_text(
        "def hello_world():\n"
        "    return 42\n"
        "\n"
        "class Greeter:\n"
        "    def greet(self):\n"
        "        return 'hi'\n"
    )

    CognitiveGraph(str(proj), auto_register=True)
    chain_dir = str(data_dir / "spring" / "chain-gw6")

    # svc real (mismo factory que usa _inject_spec_predicates)
    svc = spring_service(chain_dir=chain_dir)
    assert svc is not None, "SpringService debe resolverse desde chain_dir"

    return {
        "proj": str(proj),
        "chain_dir": chain_dir,
        "svc": svc,
        "data_dir": str(data_dir),
    }


def _seed_spec(env, *, predicates, status: str = "specified") -> None:
    """Crea spring + una spec ACTUAL (con o sin predicates) en el delta real."""
    svc = env["svc"]
    spring_id = svc.get_spring_id(env["chain_dir"])
    svc.create_spring(
        spring_id,
        goal="G-W6 demo",
        status="planning",
        project_id="prodemo",
    )
    spec = svc.add_spec(
        spring_id,
        objective="Implementar hello module",
        domain="core",
        spec_type="feature",
        status="draft",
    )
    # La entidad Spec guarda predicates en el campo raíz `predicates`
    # (no dentro de `validation`), y _inject_spec_predicates lee ambos.
    svc.update_spec(
        spring_id,
        spec["spec_id"],
        {"predicates": predicates, "status": status},
    )


def _run_gate(env, *, task_id: str = "SPEC-001", fields: dict | None = None) -> dict:
    """Reproduce EXACTAMENTE el paso 5 de _execute_task (use_cases.py:388-390):

        fields = self._inject_spec_predicates(chain_dir, task_id, fields)
        task_result = self._task_registry.execute("verify_predicates", exec_ctx, fields)

    - El use case real (SubmitDataUseCase) con repos inertes: la inyección
      solo toca el delta, no los repos del workflow.
    - La tarea real registrada en producción (verification.register).
    """
    fields = fields or {}
    uc = SubmitDataUseCase(
        workflow_repo=object(),  # type: ignore[arg-type]
        state_repo=object(),  # type: ignore[arg-type]
        script_runner=object(),  # type: ignore[arg-type]
    )
    injected = uc._inject_spec_predicates(env["chain_dir"], task_id, fields)
    # El motor resuelve el root del proyecto: en producción el workflow
    # inyecta project_root en data (o ctx.project_root); sin él, verificaría
    # contra chain_dir — no contra el código del proyecto.
    injected.setdefault("project_root", env["proj"])

    registry = InMemoryTaskRegistry()
    register_verify_tasks(registry)
    ctx = ExecutionContext.create(
        session_id="sess-gw6",
        chain_dir=env["chain_dir"],
        task_id=task_id,
    )
    task_result = registry.execute("verify_predicates", ctx, injected)
    assert task_result.ok is False or task_result.ok is True
    return {"fields_injected": injected, "task_result": task_result}


def _tr(task_result) -> dict:
    return task_result.data or {}


# ═══════════════════════════════════════════════════════════════════
# E2E: la cadena spec → gate → motor contra datos reales
# ═══════════════════════════════════════════════════════════════════


def test_espec_con_predicates_reales_pasa_contra_codigo_real(env):
    """Spec con predicates que existen en el código → E2E pasa (SSOT manda)."""
    _seed_spec(
        env,
        predicates=[
            "Nivel 1: hello_world, Greeter",
            {"type": "file_exists", "path": "src/hello.py"},
        ],
    )
    out = _run_gate(env)
    tr = out["task_result"]
    assert tr.ok, f"la spec define predicciones reales; gate debe pasar: {tr.error}"
    data = _tr(tr)
    assert data["all_passed"] is True
    assert data["failed_count"] == 0
    # La inyección trajo predicates DESDE el delta, no del vacío.
    assert out["fields_injected"]["predicates"], "engine inyectó predicates de la spec"


def test_spec_sin_predicates_el_gate_falla_no_hay_noop(env):
    """Spec SIN predicates → fields queda sin predicates → FAIL explícito."""
    _seed_spec(env, predicates=[])
    out = _run_gate(env)
    tr = out["task_result"]
    assert not tr.ok, "spec sin predicates ⇒ el gate NO puede pasar vacío"
    assert "predicates" in (tr.error or "").lower() or out["fields_injected"] == {}


def test_predicate_de_simbolo_inexistente_falla_con_spec_real(env):
    """La spec cita código que NO existe → el motor verifica y FALLA."""
    _seed_spec(
        env,
        predicates=[
            {"type": "symbol_exists", "symbol": "function_que_no_existe",
             "file": "src/hello.py"}
        ],
    )
    out = _run_gate(env)
    tr = out["task_result"]
    # Semántica del motor: la tarea "corre" (ok=True) pero la DECISIÓN del
    # gate está en all_passed: el predicate falló → gate bloqueado.
    assert tr.ok, f"la task debe ejecutarse: {tr.error}"
    assert _tr(tr)["all_passed"] is False
    assert _tr(tr)["failed_count"] == 1


def test_comando_shell_de_la_spec_se_ejecuta_de_verdad(env):
    """Predicate tipo comando shell (exit 0) en la spec → se ejecuta en el motor."""
    _seed_spec(env, predicates=['python3 -c "import sys; sys.exit(0)"'])
    out = _run_gate(env)
    tr = out["task_result"]
    assert tr.ok, f"exit 0 debe pasar: {tr.error}"
    assert _tr(tr)["all_passed"] is True


def test_command_output_contains_en_spec_real(env):
    """Predicate command_output+contains se verifica contra la salida real."""
    _seed_spec(
        env,
        predicates=[
            {"type": "command_output", "command": 'python3 -c "print(42)"',
             "contains": "42"}
        ],
    )
    out = _run_gate(env)
    tr = out["task_result"]
    assert tr.ok
    assert _tr(tr)["all_passed"] is True


def test_input_del_agente_con_predicates_es_respetado(env):
    """Si el agente ya trajo predicates legítimos, la spec NO los pisa."""
    _seed_spec(env, predicates=["test_otra_cosa_que_no_importa"])
    fields = {
        "predicates": [
            {"type": "symbol_exists", "symbol": "hello_world", "file": "src/hello.py"}
        ]
    }
    out = _run_gate(env, fields=fields)
    tr = out["task_result"]
    assert tr.ok
    assert _tr(tr)["total"] == 1, "input propio prevalece sobre la spec"


def test_proyecto_sin_spec_el_gate_falla_seguro(env):
    """Sin spring/spec en el delta → fields vacíos → FAIL (no miente)."""
    out = _run_gate(env)
    tr = out["task_result"]
    assert not tr.ok, "sin spec, nada que verificar ⇒ gate no puede pasar"
