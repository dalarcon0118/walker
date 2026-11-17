"""test_hooks_bus_bdd.py — hooks de ciclo de vida con scopes global y proyecto.

Diseño: global (`<walker>/config/hooks/hooks.json`) + proyecto
(`<repo>/.cognitive/hooks/hooks.json`), el proyecto pisa por `id` y puede
deshabilitar globales. Los hooks corren como subprocesos (un import roto no
puede tumbar el server MCP) y `emit()` nunca lanza.

Trust: un hook de proyecto es código arbitrario al abrir un repo ajeno, así que
solo corre si el proyecto se declara confiable.
"""

from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.shared_kernel.hooks import HookBus, HookSpec


def _walker(tmp_path: Path, hooks: list[dict]) -> Path:
    root = tmp_path / "walker"
    (root / "config" / "hooks").mkdir(parents=True)
    (root / "config" / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}))
    return root


def _project(tmp_path: Path, hooks: list[dict] | None = None,
             disabled: list[str] | None = None, trusted: bool | None = None) -> Path:
    root = tmp_path / "repo"
    (root / ".cognitive" / "hooks").mkdir(parents=True)
    if hooks is not None or disabled is not None:
        (root / ".cognitive" / "hooks" / "hooks.json").write_text(
            json.dumps({"hooks": hooks or [], "disabled": disabled or []})
        )
    if trusted is not None:
        (root / ".cognitive" / "config.json").write_text(
            json.dumps({"hooks": {"trusted": trusted}})
        )
    return root


def test_proyecto_pisa_global_por_id_y_agrega(tmp_path):
    walker = _walker(tmp_path, [
        {"id": "sync", "event": "commit", "command": "echo global"},
        {"id": "solo-global", "event": "commit", "command": "echo g"},
    ])
    project = _project(tmp_path, trusted=True, hooks=[
        {"id": "sync", "event": "commit", "command": "echo proyecto"},
        {"id": "propio", "event": "cpg.indexed", "command": "echo p"},
    ])

    bus = HookBus.load(project, walker)
    by_id = {spec.id: spec for spec in bus.specs}

    assert by_id["sync"].command == "echo proyecto"
    assert by_id["sync"].source == "project"
    assert by_id["solo-global"].source == "global"
    assert by_id["propio"].event == "cpg.indexed"


def test_hooks_de_proyecto_sin_trust_se_ignoran(tmp_path):
    walker = _walker(tmp_path, [])
    project = _project(tmp_path, hooks=[
        {"id": "peligroso", "event": "commit", "command": "rm -rf /"},
    ])

    bus = HookBus.load(project, walker)

    assert bus.specs == []
    assert bus.skipped_project_hooks == 1


def test_env_de_trust_habilita_los_hooks_del_proyecto(tmp_path, monkeypatch):
    monkeypatch.setenv("WALKER_HOOKS_TRUST", "1")
    walker = _walker(tmp_path, [])
    project = _project(tmp_path, hooks=[
        {"id": "propio", "event": "commit", "command": "echo ok"},
    ])

    assert [s.id for s in HookBus.load(project, walker).specs] == ["propio"]


def test_disabled_elimina_un_hook_global(tmp_path):
    walker = _walker(tmp_path, [
        {"id": "sync", "event": "commit", "command": "echo g"},
        {"id": "otro", "event": "commit", "command": "echo g"},
    ])
    project = _project(tmp_path, trusted=True, disabled=["sync"])

    ids = {s.id for s in HookBus.load(project, walker).specs}
    assert ids == {"otro"}


def test_spec_invalida_se_ignora_sin_romper(tmp_path):
    walker = _walker(tmp_path, [
        {"id": "sin-command", "event": "commit"},
        {"id": "sin-event", "command": "echo x"},
        {"id": "ok", "event": "commit", "command": "echo x"},
    ])
    project = _project(tmp_path)

    assert [s.id for s in HookBus.load(project, walker).specs] == ["ok"]


def test_emit_pasa_payload_por_stdin_y_env(tmp_path, monkeypatch):
    project = _project(tmp_path)
    bus = HookBus(
        specs=[HookSpec(id="echo", event="commit", command="cat")],
        project_root=project, walker_root=project,
    )

    results = bus.emit("commit", {"sha": "abc", "n": 2})

    assert len(results) == 1 and results[0].ok
    assert json.loads(results[0].stdout) == {"sha": "abc", "n": 2}


def test_emit_expone_el_evento_por_entorno(tmp_path):
    project = _project(tmp_path)
    bus = HookBus(
        specs=[HookSpec(id="env", event="cpg.indexed",
                        command='printf "%s" "$WALKER_EVENT"')],
        project_root=project, walker_root=project,
    )

    assert bus.emit("cpg.indexed", {})[0].stdout == "cpg.indexed"


def test_timeout_se_reporta_y_no_lanza(tmp_path):
    project = _project(tmp_path)
    bus = HookBus(
        specs=[HookSpec(id="lento", event="commit", command="sleep 3", timeout_s=1)],
        project_root=project, walker_root=project,
    )

    result = bus.emit("commit", {})[0]

    assert result.ok is False
    assert result.timed_out is True
    assert "timeout" in result.error


def test_fallo_del_comando_no_lanza(tmp_path):
    project = _project(tmp_path)
    bus = HookBus(
        specs=[HookSpec(id="rompe", event="commit", command="exit 3")],
        project_root=project, walker_root=project,
    )

    result = bus.emit("commit", {})[0]
    assert result.ok is False and result.rc == 3


def test_blocked_solo_considera_hooks_blocking(tmp_path):
    project = _project(tmp_path)
    bus = HookBus(
        specs=[
            HookSpec(id="aviso", event="task.before", command="exit 1"),
            HookSpec(id="gate", event="task.before", command="exit 1", blocking=True),
        ],
        project_root=project, walker_root=project,
    )

    results = bus.emit("task.before", {})
    assert bus.blocked(results) is True


def test_emit_sin_suscritores_es_noop(tmp_path):
    project = _project(tmp_path)
    bus = HookBus(specs=[], project_root=project, walker_root=project)
    assert bus.emit("commit", {}) == []
