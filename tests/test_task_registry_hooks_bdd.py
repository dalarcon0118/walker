"""test_task_registry_hooks_bdd.py — `task.after` como hook inyectado.

El registro de tasks no debe conocer el bus de hooks ni el disco (R1): recibe
una función `on_task_done` desde composition. Un observador roto no puede
cambiar el resultado del task.
"""

from __future__ import annotations

from fast_mcp.features.tasks.domain.task_registry import (
    ExecutionContext,
    TaskRegistry,
    TaskResult,
)


def _ctx() -> ExecutionContext:
    return ExecutionContext.create(chain_dir="/tmp/proj", task_id="T-1")


def test_notifica_task_after_con_nombre_y_resultado():
    seen: list[tuple] = []
    reg = TaskRegistry(on_task_done=lambda name, ctx, result: seen.append(
        (name, ctx.task_id, result.ok)
    ))
    reg.register("ok_task", lambda ctx, data: TaskResult.success({"x": 1}))

    result = reg.execute("ok_task", _ctx(), {})

    assert result.ok is True
    assert seen == [("ok_task", "T-1", True)]


def test_notifica_tambien_cuando_el_task_falla():
    seen: list[bool] = []
    reg = TaskRegistry(on_task_done=lambda name, ctx, result: seen.append(result.ok))
    reg.register("boom", lambda ctx, data: (_ for _ in ()).throw(RuntimeError("x")))

    result = reg.execute("boom", _ctx(), {})

    assert result.ok is False
    assert seen == [False]


def test_notifica_task_desconocido():
    seen: list[str] = []
    reg = TaskRegistry(on_task_done=lambda name, ctx, result: seen.append(name))

    reg.execute("no_existe", _ctx(), {})

    assert seen == ["no_existe"]


def test_observador_roto_no_cambia_el_resultado():
    def boom(name, ctx, result):
        raise RuntimeError("hook roto")

    reg = TaskRegistry(on_task_done=boom)
    reg.register("ok_task", lambda ctx, data: {"ok": True, "data": {"n": 2}})

    result = reg.execute("ok_task", _ctx(), {})

    assert result.ok is True and result.data == {"n": 2}


def test_emit_task_after_resuelve_el_repo_desde_el_layout(tmp_path):
    """`ctx.chain_dir` es el layout cognitivo, no la raíz del repo.

    Los hooks viven en `<repo>/.cognitive/hooks/` y el trust en
    `<repo>/.cognitive/config.json`; si se carga el bus con el layout, no se
    encuentra ninguno y el evento se pierde en silencio (medido con `capture`).
    """
    import json

    from fast_mcp.composition import _emit_task_after

    repo = tmp_path / "repo"
    layout = repo / ".cognitive" / "projects" / "demo"
    (layout / "data").mkdir(parents=True)
    (repo / "project.json").write_text(json.dumps({"project_id": "demo", "repo_path": str(repo)}))
    (repo / ".cognitive" / "config.json").write_text(json.dumps({"hooks": {"trusted": True}}))
    out = repo / "hook.log"
    (repo / ".cognitive" / "hooks").mkdir()
    (repo / ".cognitive" / "hooks" / "hooks.json").write_text(json.dumps({"hooks": [{
        "id": "task-after", "event": "task.after",
        "command": f'printf "%s " "$WALKER_EVENT" >> {out}; cat >> {out}',
        "timeout_s": 10,
        "permissions": ["fs:repo", "exec"],
    }]}))

    ctx = ExecutionContext.create(chain_dir=str(layout), task_id="T-1")
    _emit_task_after("capture", ctx, TaskResult.success({"captured": True}))

    content = out.read_text()
    assert content.startswith("task.after ")
    assert '"task": "capture"' in content
