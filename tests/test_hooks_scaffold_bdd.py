"""test_hooks_scaffold_bdd.py — `hooks new` genera un paquete que funciona.

El costo de arrancar un hook no debería ser leer cuatro documentos y copiar un
manifiesto a mano. `new` genera manifiesto + entry con la API pública + test, y
el paquete resultante tiene que instalar y ejecutar sin editar nada.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

WALKER_ROOT = Path(__file__).resolve().parents[1]
CLI = WALKER_ROOT / "scripts" / "hooks_cli.py"


def _new(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), "new", *args],
        cwd=str(tmp_path), capture_output=True, text=True,
    )


def test_new_genera_paquete_instalable(tmp_path):
    proc = _new(tmp_path, "demo-hook", "--event", "cpg.indexed")

    assert proc.returncode == 0, proc.stderr
    package = tmp_path / "demo-hook"
    manifest = json.loads((package / "hook.json").read_text())
    assert manifest["id"] == "demo-hook"
    assert manifest["kind"] == "hook"
    assert manifest["events"] == ["cpg.indexed"]
    assert (package / "main.py").is_file()
    assert (package / "test_main.py").is_file()

    from fast_mcp.shared_kernel.hooks.package import read_manifest

    parsed = read_manifest(package)
    assert parsed.entry == "main.py"
    assert parsed.permissions == ("fs:repo",)


def test_el_test_generado_pasa(tmp_path):
    _new(tmp_path, "demo-hook", "--event", "task.after")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "demo-hook" / "test_main.py"), "-q"],
        cwd=str(WALKER_ROOT), capture_output=True, text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_new_rechaza_evento_inexistente(tmp_path):
    proc = _new(tmp_path, "demo-hook", "--event", "commti")
    assert proc.returncode == 2
    assert "commiti" in proc.stderr or "commti" in proc.stderr
    assert not (tmp_path / "demo-hook").exists()


def test_new_no_pisa_un_paquete_existente(tmp_path):
    _new(tmp_path, "demo-hook")
    proc = _new(tmp_path, "demo-hook")
    assert proc.returncode == 2

    forced = _new(tmp_path, "demo-hook", "--force")
    assert forced.returncode == 0


def test_new_rechaza_id_invalido(tmp_path):
    proc = _new(tmp_path, "Demo Hook")
    assert proc.returncode == 2
    assert "id inválido" in proc.stderr
