"""test_run_tests_env_bdd.py — la suite se certifica con el perfil por defecto.

Bug medido 2026-11-20: el server MCP corre con `WALKER_MCP_PROFILE=agent`;
`run_tests` heredaba ese entorno y 14 tests que fijan el contrato `full`
(46 tools) fallaban **sólo** desde la tool de verificación. El shell del host
pasaba 1542. Un gate que no puede ponerse verde por su propia configuración
convierte "verificado" en un estado inalcanzable.
"""

from __future__ import annotations

from fast_mcp.features.tasks.handlers.implement import _suite_env


def test_suite_env_descarta_los_knobs_del_agente(monkeypatch):
    monkeypatch.setenv("WALKER_MCP_PROFILE", "agent")
    monkeypatch.setenv("WALKER_FLIGHT_CONTROL_INTAKE", "0")
    monkeypatch.setenv("WALKER_FLIGHT_CONTROL_DENY", "1")
    monkeypatch.setenv("WALKER_PROJECT_ID", "walker")
    monkeypatch.setenv("WALKER_SESSION_ID", "ses_x")
    monkeypatch.setenv("WALKER_PROMPT_SKILLS", "0")
    monkeypatch.setenv("WALKER_SKILLS_HOME", "/tmp/skills")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = _suite_env()

    for gone in (
        "WALKER_MCP_PROFILE",
        "WALKER_FLIGHT_CONTROL_INTAKE",
        "WALKER_FLIGHT_CONTROL_DENY",
        "WALKER_PROJECT_ID",
        "WALKER_SESSION_ID",
        "WALKER_PROMPT_SKILLS",
        "WALKER_SKILLS_HOME",
    ):
        assert gone not in env, f"{gone} no debe llegar a la suite"
    assert env["PATH"] == "/usr/bin:/bin"
