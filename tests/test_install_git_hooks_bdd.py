"""test_install_git_hooks_bdd.py — instalación del hook post-commit.

El hook tiene que poder instalarse en walker y en los repos de producto sin
pisar un hook ajeno, y desinstalarse sin dejar basura.
"""

from __future__ import annotations

from pathlib import Path

from scripts import install_git_hooks as installer


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git" / "hooks").mkdir(parents=True)
    return repo


def test_instala_hook_ejecutable_con_la_ruta_de_walker(tmp_path):
    repo = _repo(tmp_path)

    outcome = installer.install(repo)

    assert outcome["ok"] is True
    hook = repo / ".git" / "hooks" / "post-commit"
    assert hook.exists()
    assert hook.stat().st_mode & 0o111, "debe ser ejecutable"
    content = hook.read_text()
    assert installer.MARKER in content
    assert str(installer.WALKER_ROOT) in content
    assert "sync_cpg_on_commit.py" in content


def test_hook_no_bloquea_el_commit(tmp_path):
    """Corre en background y siempre sale 0 (si no, rompe cada commit)."""
    repo = _repo(tmp_path)
    installer.install(repo)

    content = (repo / ".git" / "hooks" / "post-commit").read_text()
    assert ") >/dev/null 2>&1 &" in content, "el sync va en background"
    assert content.rstrip().endswith("exit 0")


def test_no_pisa_un_hook_ajeno_y_hace_backup(tmp_path):
    repo = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho ajeno\n")

    outcome = installer.install(repo)

    assert outcome["ok"] is True
    assert outcome["backup"] and Path(outcome["backup"]).exists()
    assert Path(outcome["backup"]).read_text() == "#!/bin/sh\necho ajeno\n"
    assert installer.MARKER in hook.read_text()


def test_reinstalar_no_duplica_backups(tmp_path):
    repo = _repo(tmp_path)
    installer.install(repo)
    outcome = installer.install(repo)
    assert outcome["backup"] is None


def test_uninstall_elimina_solo_lo_propio(tmp_path):
    repo = _repo(tmp_path)
    installer.install(repo)
    assert installer.uninstall(repo)["removed"] is True
    assert not (repo / ".git" / "hooks" / "post-commit").exists()

    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho ajeno\n")
    outcome = installer.uninstall(repo)
    assert outcome["ok"] is False
    assert hook.exists(), "un hook ajeno no se toca"


def test_install_falla_fuera_de_un_repo_git(tmp_path):
    outcome = installer.install(tmp_path)
    assert outcome["ok"] is False
    assert "no es un repo git" in outcome["error"]
