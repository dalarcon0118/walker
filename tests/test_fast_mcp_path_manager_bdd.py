"""tests/test_fast_mcp_path_manager_bdd.py — PathManager: único SSOT de rutas del workflow.

Valida la política de resolución del manifiesto workflow.json (INV-016 fail-fast):

  1. Override explícito (env WALKER_WORKFLOW_MANIFEST / config 'workflow.manifest')
     gana siempre y se honra TAL CUAL (relativa → contra chain_dir; si no existe,
     se retorna igual — el loader falla ruidosamente, sin fallback silencioso).
  2. Canónico:  {chain_dir}/mcp/data/workflow/workflow.json
  3. Flat:      {chain_dir}/workflow.json
  4. None       (caller decide el error)

La ubicación fantasma legacy {chain_dir}/data/workflow/ NO se busca.
"""
from pathlib import Path

import pytest

from fast_mcp.shared_kernel.config import (
    get_workflow_manifest_dir,
    get_workflow_manifest_path,
)
from fast_mcp.shared_kernel.path_manager import PathManager


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Aísla del entorno real: sin override global salvo el que el test ponga."""
    monkeypatch.delenv(PathManager.ENV_OVERRIDE, raising=False)


def _make_project(tmp_path: Path, layout: str) -> Path:
    """Crea un proyecto tmp con el layout pedido y workflow.json válido."""
    wf = tmp_path / "mcp" / "data" / "workflow" / "workflow.json"
    if layout == "flat":
        wf = tmp_path / "workflow.json"
    if layout == "ghost":
        wf = tmp_path / "data" / "workflow" / "workflow.json"
    wf.parent.mkdir(parents=True, exist_ok=True)
    wf.write_text('{"initial_state": "START"}')
    return tmp_path


# ── 1-3. Candidatos por proyecto ─────────────────────────────────────────

class TestResolveForProject:

    def test_canonical_layout_resolves(self, tmp_path):
        project = _make_project(tmp_path, "canonical")
        resolved = PathManager().resolve_for_project(project)
        assert resolved == project / "mcp" / "data" / "workflow" / "workflow.json"

    def test_flat_layout_resolves(self, tmp_path):
        project = _make_project(tmp_path, "flat")
        resolved = PathManager().resolve_for_project(project)
        assert resolved == project / "workflow.json"

    def test_no_workflow_returns_none(self, tmp_path):
        resolved = PathManager().resolve_for_project(tmp_path)
        assert resolved is None

    def test_ghost_data_workflow_is_NOT_searched(self, tmp_path):
        """INV-016: la ubicación fantasma legacy no legitima datos parásitos."""
        project = _make_project(tmp_path, "ghost")
        resolved = PathManager().resolve_for_project(project)
        assert resolved is None, "el fantasma data/workflow/ NO debe resolver"

    def test_canonical_wins_over_flat(self, tmp_path):
        project = _make_project(tmp_path, "canonical")
        flat = project / "workflow.json"
        flat.write_text('{"initial_state": "START"}')
        resolved = PathManager().resolve_for_project(project)
        assert resolved == project / "mcp" / "data" / "workflow" / "workflow.json"


# ── Override explícito (env / config) ────────────────────────────────────

class TestExplicitOverride:

    def test_env_override_absolute_wins(self, tmp_path):
        project = _make_project(tmp_path, "canonical")
        custom = tmp_path / "custom" / "wf.json"
        custom.parent.mkdir(parents=True)
        custom.write_text("{}")
        pm = PathManager(env={PathManager.ENV_OVERRIDE: str(custom)})
        assert pm.resolve_for_project(project) == custom

    def test_env_override_relative_resolves_against_chain_dir(self, tmp_path):
        project = _make_project(tmp_path, "canonical")
        pm = PathManager(env={PathManager.ENV_OVERRIDE: "workflow/mine.json"})
        expected = project / "workflow" / "mine.json"
        assert pm.resolve_for_project(project) == expected

    def test_explicit_override_returned_even_if_missing(self, tmp_path):
        """Fail-fast: override explícito inexistente NO cae al canónico — el
        loader debe fallar ruidosamente sobre esa ruta exacta."""
        project = _make_project(tmp_path, "canonical")
        pm = PathManager(env={PathManager.ENV_OVERRIDE: str(tmp_path / "missing.json")})
        assert pm.resolve_for_project(project) == tmp_path / "missing.json"

    def test_config_override_used_when_no_env(self, tmp_path):
        project = _make_project(tmp_path, "canonical")
        custom = tmp_path / "cfg.json"
        custom.write_text("{}")
        pm = PathManager(config_get=lambda key: str(custom) if key == PathManager.CONFIG_KEY else "")
        assert pm.resolve_for_project(project) == custom

    def test_env_wins_over_config(self, tmp_path):
        project = _make_project(tmp_path, "canonical")
        via_env = tmp_path / "env.json"
        via_cfg = tmp_path / "cfg.json"
        for p in (via_env, via_cfg):
            p.write_text("{}")
        pm = PathManager(
            env={PathManager.ENV_OVERRIDE: str(via_env)},
            config_get=lambda key: str(via_cfg) if key == PathManager.CONFIG_KEY else "",
        )
        assert pm.resolve_for_project(project) == via_env

    def test_no_override_empty(self):
        assert PathManager(env={}).explicit_override() == ""


# ── Manifest canónico del propio walker ──────────────────────────────────

class TestEngineManifest:

    def test_default_points_to_repo_ssot(self):
        resolved = PathManager().engine_manifest()
        assert resolved.name == "workflow.json"
        assert resolved.parts[-4:-1] == ("mcp", "data", "workflow")
        assert resolved.is_file(), "el SSOT restaurado debe existir en el repo"

    def test_engine_env_override_honored(self, tmp_path):
        custom = tmp_path / "engine-wf.json"
        custom.write_text("{}")
        pm = PathManager(env={PathManager.ENV_OVERRIDE: str(custom)})
        assert pm.engine_manifest() == custom
        assert pm.engine_workflow_dir() == custom.parent

    def test_engine_workflow_dir_is_parent_of_manifest(self):
        pm = PathManager()
        assert pm.engine_workflow_dir() == pm.engine_manifest().parent


# ── Delegados de compatibilidad en config.py ─────────────────────────────

class TestConfigDelegates:

    def test_config_get_workflow_manifest_path_delegates_to_path_manager(self):
        assert get_workflow_manifest_path() == str(PathManager().engine_manifest())

    def test_config_get_workflow_manifest_dir_delegates_to_path_manager(self):
        assert get_workflow_manifest_dir() == str(PathManager().engine_workflow_dir())


# ── Integración: el engine consume PathManager ───────────────────────────

class TestEngineUsesPathManager:

    def test_context_find_workflow_path_delegates(self, tmp_path, monkeypatch):
        from fast_mcp.features.workflow.context import _find_workflow_path

        project = _make_project(tmp_path, "canonical")
        assert _find_workflow_path(str(project)) == str(
            project / "mcp" / "data" / "workflow" / "workflow.json"
        )

    def test_context_find_workflow_path_honors_env_override(self, tmp_path, monkeypatch):
        from fast_mcp.features.workflow.context import _find_workflow_path

        project = _make_project(tmp_path, "canonical")
        custom = tmp_path / "over.json"
        custom.write_text("{}")
        monkeypatch.setenv(PathManager.ENV_OVERRIDE, str(custom))
        assert _find_workflow_path(str(project)) == str(custom)

    def test_context_resolve_end_to_end_with_override(self, tmp_path, monkeypatch):
        """El engine completo carga un workflow vía el override explícito."""
        from fast_mcp.features.workflow.context import WorkflowContext

        project = _make_project(tmp_path, "canonical")
        custom_dir = tmp_path / "elsewhere"
        custom_dir.mkdir()
        custom = custom_dir / "workflow.json"
        shutil = __import__("shutil")
        shutil.copy(project / "mcp" / "data" / "workflow" / "workflow.json", custom)
        monkeypatch.setenv(PathManager.ENV_OVERRIDE, str(custom))

        ctx = WorkflowContext.resolve(str(project), task_registry=None)
        assert ctx is not None, "el override explícito debe permitir cargar el engine"
        assert ctx.orchestrator is not None
