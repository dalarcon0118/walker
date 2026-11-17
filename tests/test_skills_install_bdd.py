"""test_skills_install_bdd.py — `kind: skill` en el instalador.

Un skill no se ejecuta: es un documento. Pero comparte manifiesto con los hooks
(`kind`, versión, compat de walker, firma, digest) para tener un solo
`install`/`doctor`. Lo propio de un skill es `requires` (otros skills de los que
depende) y que su `entry` es `SKILL.md`, sin bit de ejecución ni shebang.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fast_mcp.shared_kernel.hooks.package import (
    HookManifestError,
    doctor_report,
    install,
    installed,
    read_manifest,
)
from fast_mcp.shared_kernel.skills import discover, skills_home, skills_roots

WALKER_ROOT = Path(__file__).resolve().parents[1]
CLI = WALKER_ROOT / "scripts" / "hooks_cli.py"

MANIFEST = {
    "id": "ui-review",
    "kind": "skill",
    "version": "1.0.0",
    "description": "Checklist de review de UI",
    "walker": ">=3.0,<4",
    "requires": ["skill-que-no-existe"],
}


def _skill_package(tmp_path: Path, manifest: dict | None = None) -> Path:
    directory = tmp_path / "pkg"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "hook.json").write_text(json.dumps(manifest or MANIFEST))
    (directory / "SKILL.md").write_text(
        "---\nname: ui-review\ndescription: Checklist de review de UI\n"
        "tags: [review, ui]\n---\nRevisá contraste, foco y jerarquía.\n",
        encoding="utf-8",
    )
    return directory


def test_manifest_de_skill_no_exige_events_y_entry_es_skill_md(tmp_path):
    package = read_manifest(_skill_package(tmp_path))

    assert package.kind == "skill"
    assert package.entry == "SKILL.md"
    assert package.requires == ("skill-que-no-existe",)


def test_skill_no_necesita_bit_de_ejecucion(tmp_path):
    # SKILL.md queda 644: si se aplicara la validación de hooks, fallaría
    package = read_manifest(_skill_package(tmp_path))
    assert 'SKILL.md"' in package.command()


def test_kind_hook_sigue_exigiendo_events(tmp_path):
    manifest = {**MANIFEST, "kind": "hook"}
    with pytest.raises(HookManifestError) as exc:
        read_manifest(_skill_package(tmp_path, manifest))
    assert "events" in str(exc.value)


def test_install_de_skill_registra_kind_y_requires(tmp_path, monkeypatch):
    home = tmp_path / "skills-home"
    monkeypatch.setenv("WALKER_SKILLS_HOME", str(home))

    install(_skill_package(tmp_path), home, walker_version="3.0.0", scope="walker")

    entry = json.loads((home / "registry.json").read_text())["hooks"][0]
    assert entry["kind"] == "skill"
    assert entry["requires"] == ["skill-que-no-existe"]
    assert (home / "ui-review" / "SKILL.md").is_file()
    assert installed(home, scope="walker")[0].kind == "skill"


def test_el_skill_instalado_lo_encuentra_el_indice(tmp_path, monkeypatch):
    home = tmp_path / "skills-home"
    monkeypatch.setenv("WALKER_SKILLS_HOME", str(home))
    install(_skill_package(tmp_path), home, walker_version="3.0.0", scope="walker")

    skills = discover(skills_roots(None, None))

    assert "ui-review" in skills
    assert skills["ui-review"].scope == "walker"


def test_doctor_reporta_require_faltante(tmp_path, monkeypatch):
    home = tmp_path / "skills-home"
    monkeypatch.setenv("WALKER_SKILLS_HOME", str(home))
    install(_skill_package(tmp_path), home, walker_version="3.0.0", scope="walker")

    report = doctor_report(tmp_path / "repo", walker_version="3.0.0",
                           hooks_home_dir=str(tmp_path / "hooks-home"))

    assert report["skills"][0]["id"] == "ui-review"
    assert report["skills"][0]["missing_requires"] == ["skill-que-no-existe"]
    assert any("requiere 'skill-que-no-existe'" in issue for issue in report["issues"])


def test_cli_instala_skill_en_el_scope_de_skills(tmp_path, monkeypatch):
    home = tmp_path / "skills-home"
    monkeypatch.setenv("WALKER_SKILLS_HOME", str(home))
    package = _skill_package(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(CLI), "install", str(package)],
        capture_output=True, text=True, cwd=str(WALKER_ROOT),
    )

    assert proc.returncode == 0, proc.stderr
    assert (home / "ui-review" / "SKILL.md").is_file()
    listing = subprocess.run(
        [sys.executable, str(CLI), "list"], capture_output=True, text=True,
        cwd=str(WALKER_ROOT),
    )
    assert "ui-review" in listing.stdout

    removed = subprocess.run(
        [sys.executable, str(CLI), "remove", "ui-review"],
        capture_output=True, text=True, cwd=str(WALKER_ROOT),
    )
    assert removed.returncode == 0
    assert not (home / "ui-review").exists()


def test_install_de_skill_en_proyecto_va_a_cognitive_skills(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    package = _skill_package(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(CLI), "--project", str(repo), "install", str(package)],
        capture_output=True, text=True, cwd=str(WALKER_ROOT),
    )

    assert proc.returncode == 0, proc.stderr
    assert (repo / ".cognitive" / "skills" / "ui-review" / "SKILL.md").is_file()
