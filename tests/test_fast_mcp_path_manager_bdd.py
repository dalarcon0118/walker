"""tests/test_fast_mcp_path_manager_bdd.py — PathManager: único SSOT de rutas del workflow.

Valida la política GLOBAL del workflow del motor (project.md § 'Configuración
del workflow: GLOBAL del motor, no por-proyecto'):

  1. Override explícito (env WALKER_WORKFLOW_MANIFEST / config 'workflow.manifest')
     gana siempre: absoluto tal cual, relativo contra walker_root. Si no existe,
     se retorna igual — el loader falla ruidosamente (fail-fast, sin fallback
     silencioso al canónico).
  2. Canónico: <walker_root>/data/workflow/workflow.json.
  3. None si el canónico no existe y no hay override (caller decide el error).

Los proyectos NO llevan workflow propio: cualquier workflow.json dentro del
chain_dir de un proyecto se IGNORA (no se busca nada en el chain_dir).
"""
from pathlib import Path

import pytest

from fast_mcp.shared_kernel.config import (
    get_workflow_manifest_dir,
    get_workflow_manifest_path,
)
from fast_mcp.shared_kernel.path_manager import PathManager
from fast_mcp.shared_kernel.paths import get_walker_root


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Aísla del entorno real: sin override global salvo el que el test ponga."""
    monkeypatch.delenv(PathManager.ENV_OVERRIDE, raising=False)


@pytest.fixture()
def engine_dir(tmp_path: Path) -> Path:
    """Directorio engine inyectable (sustituye al SSOT real en tests)."""
    d = tmp_path / "engine" / "workflow"
    d.mkdir(parents=True)
    return d


# ── Resolución GLOBAL (canónico) ─────────────────────────────────────────

class TestResolveWorkflowManifest:

    def test_canonical_resolves_from_engine_dir(self, engine_dir):
        (engine_dir / "workflow.json").write_text('{"initial_state": "START"}')
        pm = PathManager(engine_dir=engine_dir)
        assert pm.resolve_workflow_manifest() == engine_dir / "workflow.json"

    def test_canonical_missing_returns_none(self, engine_dir):
        pm = PathManager(engine_dir=engine_dir)
        assert pm.resolve_workflow_manifest() is None

    def test_per_project_workflow_is_IGNORED(self, tmp_path, engine_dir):
        """MODELO GLOBAL: un workflow.json dentro del chain_dir del proyecto
        NO se busca ni se usa — el workflow es del motor, no del proyecto."""
        project = tmp_path / "some-project"
        (project / "mcp" / "data" / "workflow").mkdir(parents=True)
        (project / "mcp" / "data" / "workflow" / "workflow.json").write_text("{}")
        (project / "workflow.json").write_text("{}")  # flat también ignorado
        (engine_dir / "workflow.json").write_text('{"initial_state": "START"}')

        pm = PathManager(engine_dir=engine_dir)
        assert pm.resolve_workflow_manifest() == engine_dir / "workflow.json"

    def test_nothing_is_searched_inside_chain_dir(self, tmp_path):
        """Proyecto con workflow propio + engine sin workflow → None (no hay
        fallback al proyecto; el workflow es global)."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "workflow.json").write_text("{}")
        pm = PathManager(engine_dir=tmp_path / "nonexistent-engine-dir")
        assert pm.resolve_workflow_manifest() is None

    def test_default_points_to_real_repo_ssot(self):
        """Sin inyección: el canónico es el SSOT real del repo (existe)."""
        resolved = PathManager().resolve_workflow_manifest()
        assert resolved == get_walker_root() / "data" / "workflow" / "workflow.json"
        assert resolved.is_file()


# ── Override explícito (env / config) ────────────────────────────────────

class TestExplicitOverride:

    def test_env_override_absolute_wins(self, engine_dir, tmp_path):
        (engine_dir / "workflow.json").write_text("{}")
        custom = tmp_path / "custom" / "wf.json"
        custom.parent.mkdir(parents=True)
        custom.write_text("{}")
        pm = PathManager(env={PathManager.ENV_OVERRIDE: str(custom)}, engine_dir=engine_dir)
        assert pm.resolve_workflow_manifest() == custom

    def test_env_override_relative_resolves_against_walker_root(self, tmp_path, engine_dir):
        (engine_dir / "workflow.json").write_text("{}")
        pm = PathManager(env={PathManager.ENV_OVERRIDE: "custom/wf.json"}, engine_dir=engine_dir)
        assert pm.resolve_workflow_manifest() == get_walker_root() / "custom" / "wf.json"

    def test_explicit_override_returned_even_if_missing(self, tmp_path, engine_dir):
        """Fail-fast: override explícito inexistente NO cae al canónico — el
        loader debe fallar ruidosamente sobre esa ruta exacta."""
        (engine_dir / "workflow.json").write_text("{}")
        pm = PathManager(
            env={PathManager.ENV_OVERRIDE: str(tmp_path / "missing.json")},
            engine_dir=engine_dir,
        )
        assert pm.resolve_workflow_manifest() == tmp_path / "missing.json"

    def test_config_override_used_when_no_env(self, tmp_path, engine_dir):
        custom = tmp_path / "cfg.json"
        custom.write_text("{}")
        pm = PathManager(
            config_get=lambda key: str(custom) if key == PathManager.CONFIG_KEY else "",
            engine_dir=engine_dir,
        )
        assert pm.engine_manifest() == custom

    def test_env_wins_over_config(self, tmp_path, engine_dir):
        via_env = tmp_path / "env.json"
        via_cfg = tmp_path / "cfg.json"
        for p in (via_env, via_cfg):
            p.write_text("{}")
        pm = PathManager(
            env={PathManager.ENV_OVERRIDE: str(via_env)},
            config_get=lambda key: str(via_cfg) if key == PathManager.CONFIG_KEY else "",
            engine_dir=engine_dir,
        )
        assert pm.engine_manifest() == via_env

    def test_no_override_empty(self):
        assert PathManager(env={}).explicit_override() == ""


# ── Manifest del propio walker (compat API) ──────────────────────────────

class TestEngineManifest:

    def test_default_points_to_repo_ssot(self):
        resolved = PathManager().engine_manifest()
        assert resolved.name == "workflow.json"
        assert resolved.parts[-3:-1] == ("data", "workflow")
        assert resolved.is_file(), "el SSOT restaurado debe existir en el repo"

    def test_engine_env_override_honored(self, tmp_path):
        custom = tmp_path / "engine-wf.json"
        custom.write_text("{}")
        pm = PathManager(env={PathManager.ENV_OVERRIDE: str(custom)})
        assert pm.engine_manifest() == custom
        assert pm.engine_workflow_dir() == custom.parent

    def test_engine_dir_injectable(self, engine_dir):
        pm = PathManager(engine_dir=engine_dir)
        assert pm.engine_workflow_dir() == engine_dir
        assert pm.engine_manifest() == engine_dir / "workflow.json"


# ── Delegados de compatibilidad en config.py ─────────────────────────────

class TestConfigDelegates:

    def test_config_get_workflow_manifest_path_delegates_to_path_manager(self):
        assert get_workflow_manifest_path() == str(PathManager().engine_manifest())

    def test_config_get_workflow_manifest_dir_delegates_to_path_manager(self):
        assert get_workflow_manifest_dir() == str(PathManager().engine_workflow_dir())


# ── Integración: el engine consume PathManager (global) ──────────────────

class TestEngineUsesPathManager:

    def test_context_find_workflow_path_is_global(self, tmp_path):
        """Sin override: _find_workflow_path() resuelve el SSOT del motor —
        independiente del chain_dir del proyecto."""
        from fast_mcp.features.workflow.context import _find_workflow_path

        expected = get_walker_root() / "data" / "workflow" / "workflow.json"
        assert _find_workflow_path() == str(expected)

    def test_context_find_workflow_path_ignores_project_workflow(self, tmp_path):
        """Un proyecto con workflow propio NO altera la resolución global."""
        from fast_mcp.features.workflow.context import _find_workflow_path

        (tmp_path / "mcp" / "data" / "workflow").mkdir(parents=True)
        (tmp_path / "mcp" / "data" / "workflow" / "workflow.json").write_text("{}")
        expected = get_walker_root() / "data" / "workflow" / "workflow.json"
        assert _find_workflow_path() == str(expected)

    def test_context_find_workflow_path_honors_env_override(self, tmp_path, monkeypatch):
        from fast_mcp.features.workflow.context import _find_workflow_path

        custom = tmp_path / "over.json"
        custom.write_text("{}")
        monkeypatch.setenv(PathManager.ENV_OVERRIDE, str(custom))
        assert _find_workflow_path() == str(custom)

    def test_context_resolve_end_to_end_with_override(self, tmp_path, monkeypatch):
        """El engine completo carga el workflow vía override — chain_dir solo
        para estado/memoria (no se copia el SSOT al proyecto)."""
        from fast_mcp.features.workflow.context import WorkflowContext

        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.setenv(
            PathManager.ENV_OVERRIDE,
            str(get_walker_root() / "data" / "workflow" / "workflow.json"),
        )

        ctx = WorkflowContext.resolve(str(project), task_registry=None)
        assert ctx is not None, "el override explícito debe permitir cargar el engine"
        assert ctx.chain_dir == str(project)
        assert ctx.orchestrator is not None
