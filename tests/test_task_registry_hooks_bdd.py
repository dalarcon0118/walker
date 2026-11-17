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
