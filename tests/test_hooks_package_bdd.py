"""test_hooks_package_bdd.py — hooks instalables (manifiesto + registro + versión).

Un hook inline declara la suscripción en el `hooks.json` del que lo instala. Un
paquete se declara a sí mismo con `hook.json`, y `install` valida tres cosas
antes de copiar: eventos existentes, compatibilidad de versión con walker e id
libre. Sin eso, un typo en `events` o un plugin para otra versión falla recién
en runtime (o nunca, que es peor).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fast_mcp.shared_kernel.hooks import package as pkg_mod
from fast_mcp.shared_kernel.hooks.bus import HookBus
from fast_mcp.shared_kernel.hooks.package import (
    HookManifestError,
    check_compat,
    doctor_report,
    install,
    installed,
    parse_source,
    read_manifest,
    remove,
    set_enabled,
    update,
)

MANIFEST = {
    "id": "demo-hook",
    "version": "1.2.0",
    "walker": ">=3.0,<4",
    "events": ["commit"],
    "entry": "main.py",
    "timeout_s": 10,
    "description": "hook de prueba",
    # el hook de prueba escribe en $HOOK_OUT: necesita el entorno completo
    "permissions": ["env:all"],
}


def _package(tmp_path: Path, manifest: dict | None = None) -> Path:
    directory = tmp_path / "src" / "demo"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "hook.json").write_text(json.dumps(manifest or MANIFEST))
    (directory / "main.py").write_text(
        "import os, sys\n"
        "open(os.environ['HOOK_OUT'], 'a').write("
        "os.environ['WALKER_EVENT'] + ' ' + sys.stdin.read().strip() + '\\n')\n"
    )
    return directory


def _home(tmp_path: Path) -> Path:
    return tmp_path / "hooks-home"


def _walker_root(tmp_path: Path) -> Path:
    """Walker root aislado: sin `config/hooks/hooks.json` global del repo real."""
    root = tmp_path / "walker-root"
    root.mkdir(exist_ok=True)
    return root


def test_read_manifest_valido(tmp_path):
    package = read_manifest(_package(tmp_path))

    assert package.id == "demo-hook"
    assert package.version == "1.2.0"
    assert package.events == ("commit",)
    assert package.command().startswith('"$WALKER_PYTHON"')


@pytest.mark.parametrize("missing", ["id", "version", "walker", "events", "entry"])
def test_manifiesto_incompleto_falla(tmp_path, missing):
    manifest = {k: v for k, v in MANIFEST.items() if k != missing}
    with pytest.raises(HookManifestError):
        read_manifest(_package(tmp_path, manifest))


def test_evento_desconocido_falla_al_leer(tmp_path):
    manifest = {**MANIFEST, "events": ["commti"]}
    with pytest.raises(HookManifestError) as exc:
        read_manifest(_package(tmp_path, manifest))

    assert "commti" in str(exc.value)
    assert "commit" in str(exc.value), "el error debe listar los eventos válidos"


def test_entry_inexistente_falla(tmp_path):
    manifest = {**MANIFEST, "entry": "no-existe.py"}
    with pytest.raises(HookManifestError):
        read_manifest(_package(tmp_path, manifest))


def test_check_compat(tmp_path):
    package = read_manifest(_package(tmp_path))

    assert check_compat(package, "3.0.0") == ""
    assert "requiere walker" in check_compat(package, "4.1.0")

    bad = read_manifest(_package(tmp_path, {**MANIFEST, "walker": "~~3"}))
    assert "constraint inválido" in check_compat(bad, "3.0.0")


def test_install_copia_y_registra(tmp_path):
    source = _package(tmp_path)
    home = _home(tmp_path)

    package = install(source, home, walker_version="3.0.0")

    assert (home / "demo-hook" / "hook.json").is_file()
    assert (home / "demo-hook" / "main.py").is_file()
    registry = json.loads((home / "registry.json").read_text())
    assert registry["hooks"][0]["id"] == "demo-hook"
    assert registry["hooks"][0]["sha256"]
    assert package.id == "demo-hook"


def test_install_rechaza_incompatible(tmp_path):
    source = _package(tmp_path, {**MANIFEST, "walker": ">=99"})
    with pytest.raises(HookManifestError) as exc:
        install(source, _home(tmp_path), walker_version="3.0.0")
    assert "requiere walker" in str(exc.value)


def test_reinstalar_pide_force(tmp_path):
    source = _package(tmp_path)
    home = _home(tmp_path)
    install(source, home, walker_version="3.0.0")

    with pytest.raises(HookManifestError) as exc:
        install(source, home, walker_version="3.0.0")
    assert "--force" in str(exc.value)

    (source / "hook.json").write_text(json.dumps({**MANIFEST, "version": "1.3.0"}))
    package = install(source, home, walker_version="3.0.0", force=True)
    assert package.version == "1.3.0"


def test_remove_borra_dir_y_registro(tmp_path):
    home = _home(tmp_path)
    install(_package(tmp_path), home, walker_version="3.0.0")

    outcome = remove("demo-hook", home)

    assert outcome["removed"] is True
    assert not (home / "demo-hook").exists()
    assert installed(home) == []


def test_enable_disable(tmp_path):
    home = _home(tmp_path)
    install(_package(tmp_path), home, walker_version="3.0.0")

    assert set_enabled("demo-hook", home, False) is True
    assert installed(home)[0].enabled is False
    assert set_enabled("demo-hook", home, True) is True
    assert installed(home)[0].enabled is True


def test_el_bus_ejecuta_un_paquete_en_sus_eventos(tmp_path, monkeypatch):
    home = _home(tmp_path)
    install(_package(tmp_path), home, walker_version="3.0.0")
    out = tmp_path / "out.log"
    monkeypatch.setenv("HOOK_OUT", str(out))
    project = tmp_path / "repo"
    project.mkdir()

    bus = HookBus.load(project, walker_root=_walker_root(tmp_path), hooks_home=home)
    spec = bus.hooks_for("commit")[0]
    assert spec.source == "package:global"
    assert spec.version == "1.2.0"

    results = bus.emit("commit", {"sha": "abc"})

    assert results[0].ok is True
    assert out.read_text().startswith("commit ")
    assert '"sha": "abc"' in out.read_text()


def test_paquete_incompatible_se_saltea_sin_romper(tmp_path):
    home = _home(tmp_path)
    install(_package(tmp_path, {**MANIFEST, "walker": ">=3.0,<4"}),
            home, walker_version="3.0.0")
    # simular que walker avanzó a 4.x
    package_dir = home / "demo-hook"
    manifest = json.loads((package_dir / "hook.json").read_text())
    manifest["walker"] = ">=4.0"
    (package_dir / "hook.json").write_text(json.dumps(manifest))
    project = tmp_path / "repo"
    project.mkdir()

    bus = HookBus.load(project, walker_root=_walker_root(tmp_path), hooks_home=home)

    assert bus.hooks_for("commit") == []
    assert any("demo-hook" in item for item in bus.incompatible)


def test_paquete_deshabilitado_no_carga(tmp_path):
    home = _home(tmp_path)
    install(_package(tmp_path), home, walker_version="3.0.0")
    set_enabled("demo-hook", home, False)
    project = tmp_path / "repo"
    project.mkdir()

    assert HookBus.load(project, walker_root=_walker_root(tmp_path), hooks_home=home).hooks_for("commit") == []


def test_evento_desconocido_inline_queda_como_diagnostico(tmp_path):
    project = tmp_path / "repo"
    (project / ".cognitive" / "hooks").mkdir(parents=True)
    (project / ".cognitive" / "config.json").write_text(
        json.dumps({"hooks": {"trusted": True}})
    )
    (project / ".cognitive" / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": [{"id": "typo", "event": "commti", "command": "true"}]})
    )

    bus = HookBus.load(project, walker_root=_walker_root(tmp_path), hooks_home=_home(tmp_path))

    assert any("typo" in item and "commti" in item for item in bus.unknown_events)
    assert bus.hooks_for("commit") == []


def test_doctor_reporta_incompatibilidad_y_eventos_desconocidos(tmp_path, monkeypatch):
    home = _home(tmp_path)
    install(_package(tmp_path), home, walker_version="3.0.0")
    package_dir = home / "demo-hook"
    manifest = json.loads((package_dir / "hook.json").read_text())
    manifest["walker"] = ">=4.0"
    (package_dir / "hook.json").write_text(json.dumps(manifest))
    monkeypatch.setenv("WALKER_HOOKS_HOME", str(home))

    report = doctor_report(tmp_path, walker_version="3.0.0",
                           hooks_home_dir=str(home))

    assert report["global"][0]["id"] == "demo-hook"
    assert any("requiere walker" in issue for issue in report["issues"])


def test_version_del_paquete_coincide_con_pyproject():
    """`__version__` y pyproject deben decir lo mismo (el constraint depende)."""
    import tomllib

    from fast_mcp import __version__

    pyproject = Path(pkg_mod.__file__).resolve().parents[3] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert __version__ == declared


# ── Instalación desde git: pin, update y verificación de hash ───────────────

import subprocess


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def _upstream(tmp_path: Path, version: str = "1.0.0") -> Path:
    repo = tmp_path / "upstream"
    repo.mkdir()
    (repo / "hook.json").write_text(json.dumps({**MANIFEST, "version": version}))
    (repo / "main.py").write_text("print('v" + version + "')\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", f"v{version}")
    _git(repo, "branch", "-M", "main")
    return repo


def _bump(repo: Path, version: str) -> None:
    (repo / "hook.json").write_text(json.dumps({**MANIFEST, "version": version}))
    (repo / "main.py").write_text("print('v" + version + "')\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", f"v{version}")


def test_parse_source_formatos():
    from fast_mcp.shared_kernel.hooks.package import parse_source

    gh = parse_source("github:user/repo#v1")
    assert (gh.kind, gh.location, gh.ref) == ("git", "https://github.com/user/repo.git", "v1")

    url = parse_source("git+https://x/y.git#abc")
    assert (url.kind, url.ref) == ("git", "abc")

    assert parse_source("./local/dir").kind == "local"
    assert parse_source("./local", ref="v2").kind == "local"


def test_install_desde_git_registra_commit_y_ref(tmp_path):
    repo = _upstream(tmp_path)
    _git(repo, "tag", "v1")
    head = _git(repo, "rev-parse", "HEAD")
    home = _home(tmp_path)

    install(f"git+file://{repo}#v1", home, walker_version="3.0.0")

    entry = json.loads((home / "registry.json").read_text())["hooks"][0]
    assert entry["source_type"] == "git"
    assert entry["ref"] == "v1"
    assert entry["commit"] == head
    assert entry["sha256"]
    assert f"#v1" in entry["source"]


def test_update_sigue_la_rama_y_reporta_version_nueva(tmp_path):
    repo = _upstream(tmp_path)
    home = _home(tmp_path)
    install(f"git+file://{repo}#main", home, walker_version="3.0.0")

    _bump(repo, "1.1.0")
    check = update("demo-hook", home, walker_version="3.0.0", check=True)
    assert check["changed"] is True
    assert check["available_version"] == "1.1.0"
    assert check["installed"] is False

    applied = update("demo-hook", home, walker_version="3.0.0")
    assert applied["installed"] is True
    entry = json.loads((home / "registry.json").read_text())["hooks"][0]
    assert entry["version"] == "1.1.0"
    assert entry["commit"] == _git(repo, "rev-parse", "HEAD")


def test_update_respeta_un_pin_inmutable(tmp_path):
    repo = _upstream(tmp_path)
    _git(repo, "tag", "v1")
    home = _home(tmp_path)
    install(f"git+file://{repo}#v1", home, walker_version="3.0.0")
    _bump(repo, "1.1.0")

    outcome = update("demo-hook", home, walker_version="3.0.0", check=True)

    assert outcome["changed"] is False, "un tag no se mueve: queda pinneado"


def test_update_de_paquete_local_pide_reinstall(tmp_path):
    home = _home(tmp_path)
    install(_package(tmp_path), home, walker_version="3.0.0")

    with pytest.raises(HookManifestError) as exc:
        update("demo-hook", home, walker_version="3.0.0")
    assert "path local" in str(exc.value)


def test_verify_installed_detecta_modificacion_en_disco(tmp_path):
    from fast_mcp.shared_kernel.hooks.package import verify_installed

    home = _home(tmp_path)
    install(_package(tmp_path), home, walker_version="3.0.0")
    assert verify_installed(home) == []

    (home / "demo-hook" / "main.py").write_text("print('modificado')\n")

    drift = verify_installed(home)
    assert drift and drift[0]["id"] == "demo-hook"
    assert drift[0]["actual"] != drift[0]["expected"]


# ── Integridad: digest del árbol + verificación contra un hash publicado ────

def test_tree_digest_cubre_archivos_auxiliares(tmp_path):
    """El digest viejo (manifiesto+entry) no veía un helper malicioso."""
    from fast_mcp.shared_kernel.hooks.package import tree_digest

    source = _package(tmp_path)
    helper = source / "helper.py"
    helper.write_text("X = 1\n")
    before = tree_digest(source)

    helper.write_text("X = 2\n")

    assert tree_digest(source) != before


def test_tree_digest_es_deterministico_y_ignora_caches(tmp_path):
    from fast_mcp.shared_kernel.hooks.package import tree_digest

    source = _package(tmp_path)
    first = tree_digest(source)

    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "x.pyc").write_text("basura")
    (source / ".git").mkdir()
    (source / ".git" / "HEAD").write_text("ref: refs/heads/main")

    assert tree_digest(source) == first


def test_install_con_sha256_incorrecto_no_instala(tmp_path):
    home = _home(tmp_path)
    source = _package(tmp_path)

    with pytest.raises(HookManifestError) as exc:
        install(source, home, walker_version="3.0.0", expect_sha256="0" * 64)

    assert "no coincide" in str(exc.value)
    assert "obtenido" in str(exc.value)
    assert installed(home) == []
    assert not (home / "demo-hook").exists()


def test_install_con_sha256_correcto_registra_el_digest(tmp_path):
    from fast_mcp.shared_kernel.hooks.package import tree_digest

    home = _home(tmp_path)
    source = _package(tmp_path)
    digest = tree_digest(source)

    install(source, home, walker_version="3.0.0", expect_sha256=digest)

    entry = json.loads((home / "registry.json").read_text())["hooks"][0]
    assert entry["sha256"] == digest
    assert entry["digest"] == "tree-v1"


def test_paquete_con_symlinks_se_rechaza(tmp_path):
    source = _package(tmp_path)
    (source / "link").symlink_to("/etc/hosts")

    with pytest.raises(HookManifestError) as exc:
        install(source, _home(tmp_path), walker_version="3.0.0")
    assert "symlink" in str(exc.value)


def test_update_verifica_el_digest_de_la_version_nueva(tmp_path):
    from fast_mcp.shared_kernel.hooks.package import tree_digest

    repo = _upstream(tmp_path)
    home = _home(tmp_path)
    install(f"git+file://{repo}#main", home, walker_version="3.0.0")
    _bump(repo, "1.1.0")
    new_digest = tree_digest(repo)

    with pytest.raises(HookManifestError) as exc:
        update("demo-hook", home, walker_version="3.0.0", expect_sha256="f" * 64)
    assert "digest disponible" in str(exc.value)
    entry = json.loads((home / "registry.json").read_text())["hooks"][0]
    assert entry["version"] == "1.0.0", "un digest que no coincide no actualiza"

    outcome = update("demo-hook", home, walker_version="3.0.0", expect_sha256=new_digest)
    assert outcome["installed"] is True
    assert outcome["current_sha256"] != outcome["available_sha256"]
