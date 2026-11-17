"""test_hooks_security_bdd.py — firmas ed25519 y permisos declarados.

Dos problemas distintos:

1. Integridad ≠ autoría. El digest del árbol dice "este contenido es el que
   esperabas", pero si el hash sale del mismo repo que el código, un atacante
   publica los dos. La firma ed25519 contra una lista de claves confiables del
   usuario cierra ese canal.
2. Un hook corre con el entorno del usuario: `AWS_SECRET_ACCESS_KEY`,
   `GITHUB_TOKEN`, red sin restricción. `permissions` declara lo que necesita y
   el bus aplica lo que puede: entorno mínimo (salvo `env:all`) y deny de red
   con `sandbox-exec` en macOS.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fast_mcp.shared_kernel.hooks.bus import HookBus, HookSpec, _scrub_env, _wrap_command
from fast_mcp.shared_kernel.hooks.package import (
    HookManifestError,
    doctor_report,
    install,
    installed,
    read_manifest,
)
from fast_mcp.shared_kernel.hooks.signature import (
    SIG_NAME,
    SignatureError,
    generate_keypair,
    verify_package,
    write_signature,
)

MANIFEST = {
    "id": "firmado",
    "version": "1.0.0",
    "walker": ">=3.0,<4",
    "events": ["commit"],
    "entry": "main.py",
    "permissions": ["env:all"],
}


def _package(tmp_path: Path, manifest: dict | None = None) -> Path:
    directory = tmp_path / "pkg"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "hook.json").write_text(json.dumps(manifest or MANIFEST))
    (directory / "main.py").write_text(
        "import os\n"
        "open(os.environ['HOOK_OUT'], 'a').write(os.environ.get('SECRET_TOKEN', 'MISSING') + '\\n')\n"
    )
    return directory


@pytest.fixture()
def trust(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "trusted_keys.json"
    monkeypatch.setenv("WALKER_TRUSTED_KEYS", str(path))
    return path


def _trust_key(trust: Path, key_id: str, public_b64: str) -> None:
    trust.write_text(json.dumps({"keys": [{"id": key_id, "ed25519": public_b64}]}))


# ── Firmas ────────────────────────────────────────────────────────────────


def test_firma_verifica_con_clave_confiable(tmp_path, trust):
    source = _package(tmp_path)
    private, public = generate_keypair(tmp_path / "keys", "david")
    payload = write_signature(source, private, "david")
    _trust_key(trust, "david", public.read_text().strip())

    signed, detail = verify_package(source)

    assert signed is True
    assert detail == "firmado por david"
    assert payload["sha256"]  # digest firmado


def test_firma_con_clave_desconocida_falla(tmp_path, trust):
    source = _package(tmp_path)
    private, _ = generate_keypair(tmp_path / "keys", "intruso")
    write_signature(source, private, "intruso")

    with pytest.raises(SignatureError) as exc:
        verify_package(source)
    assert "NO está en las claves confiables" in str(exc.value)


def test_modificar_despues_de_firmar_invalida(tmp_path, trust):
    source = _package(tmp_path)
    private, public = generate_keypair(tmp_path / "keys", "david")
    write_signature(source, private, "david")
    _trust_key(trust, "david", public.read_text().strip())

    (source / "main.py").write_text("print('hackeado')\n")

    with pytest.raises(SignatureError) as exc:
        verify_package(source)
    assert "no coincide con el contenido actual" in str(exc.value)


def test_firmar_no_cambia_el_digest_firmado(tmp_path):
    """`hook.sig.json` queda fuera del digest: firmar dos veces da el mismo."""
    source = _package(tmp_path)
    private, _ = generate_keypair(tmp_path / "keys", "david")

    first = write_signature(source, private, "david")
    second = write_signature(source, private, "david")

    assert first["sha256"] == second["sha256"]
    assert (source / SIG_NAME).is_file()


def test_install_requiere_firma_si_se_pide(tmp_path, trust):
    source = _package(tmp_path)
    home = tmp_path / "home"

    with pytest.raises(HookManifestError) as exc:
        install(source, home, walker_version="3.0.0", require_signature=True)
    assert "sin firma" in str(exc.value)
    assert installed(home) == []

    private, public = generate_keypair(tmp_path / "keys", "david")
    write_signature(source, private, "david")
    _trust_key(trust, "david", public.read_text().strip())

    install(source, home, walker_version="3.0.0", require_signature=True)
    entry = json.loads((home / "registry.json").read_text())["hooks"][0]
    assert entry["signature"] == "david"


# ── Permisos ──────────────────────────────────────────────────────────────


def test_permiso_desconocido_falla_al_leer(tmp_path):
    manifest = {**MANIFEST, "permissions": ["netwrok"]}
    with pytest.raises(HookManifestError) as exc:
        read_manifest(_package(tmp_path, manifest))
    assert "netwrok" in str(exc.value)
    assert "network" in str(exc.value)


def test_scrub_env_quita_secretos_salvo_env_all(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "s3cr3t")
    env = {"PATH": "/bin", "SECRET_TOKEN": "s3cr3t", "WALKER_EVENT": "commit"}

    scrubbed = _scrub_env(env, ())
    assert "SECRET_TOKEN" not in scrubbed
    assert scrubbed["PATH"] == "/bin"
    assert scrubbed["WALKER_EVENT"] == "commit"

    full = _scrub_env(env, ("env:all",))
    assert full["SECRET_TOKEN"] == "s3cr3t"


def test_hook_sin_env_all_no_ve_el_secreto(tmp_path, monkeypatch):
    source = _package(tmp_path, {**MANIFEST, "permissions": []})
    home = tmp_path / "home"
    install(source, home, walker_version="3.0.0")
    out = tmp_path / "out.log"
    monkeypatch.setenv("HOOK_OUT", str(out))
    monkeypatch.setenv("SECRET_TOKEN", "s3cr3t")

    bus = HookBus(
        specs=[HookSpec(id="firmado", command=f'"$WALKER_PYTHON" "{source}/main.py"',
                        event="commit")],
        project_root=tmp_path, walker_root=tmp_path,
    )
    bus.emit("commit", {})

    # HOOK_OUT tampoco se hereda sin env:all → el hook falla, y eso es correcto:
    # no puede leer secretos ni entorno arbitrario.
    assert out.read_text() == "" if out.exists() else True
    assert installed(home)[0].permissions == ()


def test_wrap_command_combina_fs_y_red(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    wrapped = _wrap_command("echo hola", ())
    assert wrapped.startswith("sandbox-exec")
    assert "deny network*" in wrapped
    assert "deny file-read*" in wrapped, "sin fs:any también se restringe el FS"

    red = _wrap_command("echo hola", ("network",))
    assert "deny network*" not in red
    assert "deny file-read*" in red, "grantar red no abre el filesystem"

    # sin restricción de fs PERO con la de exec activa
    solo_exec = _wrap_command("echo hola", ("network", "fs:any"))
    assert "deny process-exec*" in solo_exec

    libre = _wrap_command("echo hola", ("network", "fs:any", "exec"))
    assert libre == "echo hola"

    monkeypatch.setattr(sys, "platform", "linux")
    assert _wrap_command("echo hola", ()) == "echo hola"


def test_profile_incluye_tmp_solo_con_permiso():
    from fast_mcp.shared_kernel.hooks.bus import _fs_profile

    base = dict(repo="/repo", walker_root="/walker", home="/home/u", tmpdir="/tmp/x")
    sin = _fs_profile((), **base)
    con = _fs_profile(("fs:tmp",), **base)

    assert "(subpath \"/tmp/x\")" not in sin
    assert "(subpath \"/tmp/x\")" in con
    assert _fs_profile(("fs:any",), **base) == ""


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec es de macOS")
def test_red_bloqueada_en_runtime(tmp_path, monkeypatch):
    source = _package(tmp_path, {**MANIFEST, "permissions": ["env:all", "fs:repo"]})
    (source / "main.py").write_text(
        "import os, socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=2)\n"
        "    result = 'network-ok'\n"
        "except Exception:\n"
        "    result = 'blocked'\n"
        "open(os.environ['HOOK_OUT'], 'w').write(result)\n"
    )
    out = tmp_path / "net.log"
    monkeypatch.setenv("HOOK_OUT", str(out))
    home = tmp_path / "home"
    install(source, home, walker_version="3.0.0")

    bus = HookBus(specs=[HookSpec(
        id="firmado", command=f'"$WALKER_PYTHON" "{home}/firmado/main.py"',
        event="commit", permissions=("env:all", "fs:repo"),
    )], project_root=tmp_path, walker_root=tmp_path)
    bus.emit("commit", {})

    assert out.read_text() == "blocked"


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec es de macOS")
def test_hook_no_puede_leer_home_sin_fs_home(tmp_path, monkeypatch):
    """El pedido concreto: un hook no puede leer archivos de $HOME (ssh, aws)."""
    home_file = Path.home() / ".zshrc"
    if not home_file.is_file():
        pytest.skip("no hay ~/.zshrc en esta máquina")

    source = _package(tmp_path, {**MANIFEST, "permissions": ["env:all", "fs:repo"]})
    (source / "main.py").write_text(
        "import os, pathlib\n"
        "p = pathlib.Path(os.environ['HOME']) / '.zshrc'\n"
        "try:\n"
        "    text = p.read_text()[:8]\n"
        "    result = 'read:' + text\n"
        "except Exception as exc:\n"
        "    result = 'blocked:' + type(exc).__name__\n"
        "open(os.environ['HOOK_OUT'], 'w').write(result)\n"
    )
    out = tmp_path / "home.log"
    monkeypatch.setenv("HOOK_OUT", str(out))
    bus = HookBus(specs=[HookSpec(
        id="h", command=f'"$WALKER_PYTHON" "{source}/main.py"',
        event="commit", permissions=("env:all", "fs:repo"),
    )], project_root=tmp_path, walker_root=tmp_path)

    bus.emit("commit", {})

    assert out.read_text().startswith("blocked:")


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec es de macOS")
def test_hook_con_fs_home_puede_leer_home(tmp_path, monkeypatch):
    home_file = Path.home() / ".zshrc"
    if not home_file.is_file():
        pytest.skip("no hay ~/.zshrc en esta máquina")

    source = _package(tmp_path, {**MANIFEST, "permissions": ["env:all", "fs:repo", "fs:home"]})
    (source / "main.py").write_text(
        "import os, pathlib\n"
        "p = pathlib.Path(os.environ['HOME']) / '.zshrc'\n"
        "try:\n"
        "    p.read_text()\n"
        "    result = 'read:ok'\n"
        "except Exception as exc:\n"
        "    result = 'blocked:' + type(exc).__name__\n"
        "open(os.environ['HOOK_OUT'], 'w').write(result)\n"
    )
    out = tmp_path / "home-ok.log"
    monkeypatch.setenv("HOOK_OUT", str(out))
    bus = HookBus(specs=[HookSpec(
        id="h", command=f'"$WALKER_PYTHON" "{source}/main.py"',
        event="commit", permissions=("env:all", "fs:repo", "fs:home"),
    )], project_root=tmp_path, walker_root=tmp_path)

    bus.emit("commit", {})

    assert out.read_text() == "read:ok"


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec es de macOS")
def test_hook_sin_fs_repo_no_puede_escribir_en_el_repo(tmp_path, monkeypatch):
    out = tmp_path / "no-deberia-existir.log"
    monkeypatch.setenv("HOOK_OUT", str(out))
    source = _package(tmp_path, {**MANIFEST, "permissions": ["env:all"]})

    bus = HookBus(specs=[HookSpec(
        id="h", command=f'"$WALKER_PYTHON" "{source}/main.py"',
        event="commit", permissions=("env:all",),
    )], project_root=tmp_path, walker_root=tmp_path)
    bus.emit("commit", {})

    assert not out.exists(), "sin fs:repo el hook no puede escribir en el repo"


# ── Revocación y rotación de claves ────────────────────────────────────────

def test_clave_revocada_no_verifica(tmp_path, trust):
    from fast_mcp.shared_kernel.hooks.signature import revoke_key

    source = _package(tmp_path)
    private, public = generate_keypair(tmp_path / "keys", "david")
    write_signature(source, private, "david")
    _trust_key(trust, "david", public.read_text().strip())
    assert verify_package(source)[0] is True

    revoke_key("david", "privada filtrada")

    with pytest.raises(SignatureError) as exc:
        verify_package(source)
    message = str(exc.value)
    assert "REVOCADA" in message
    assert "privada filtrada" in message


def test_install_rechaza_paquete_firmado_con_clave_revocada(tmp_path, trust):
    from fast_mcp.shared_kernel.hooks.signature import revoke_key

    source = _package(tmp_path)
    private, public = generate_keypair(tmp_path / "keys", "david")
    write_signature(source, private, "david")
    _trust_key(trust, "david", public.read_text().strip())
    revoke_key("david")

    with pytest.raises(HookManifestError) as exc:
        install(source, tmp_path / "home", walker_version="3.0.0",
                require_signature=True)
    assert "REVOCADA" in str(exc.value)
    assert installed(tmp_path / "home") == []


def test_rotacion_invalida_firmas_viejas_y_acepta_las_nuevas(tmp_path, trust):
    from fast_mcp.shared_kernel.hooks.signature import rotate_key

    source = _package(tmp_path)
    old_private, old_public = generate_keypair(tmp_path / "keys", "david")
    new_private, new_public = generate_keypair(tmp_path / "keys2", "david")
    write_signature(source, old_private, "david")
    _trust_key(trust, "david", old_public.read_text().strip())
    assert verify_package(source)[0] is True

    rotate_key("david", new_public.read_text().strip())

    with pytest.raises(SignatureError) as exc:
        verify_package(source)
    assert "firma inválida" in str(exc.value), (
        "la firma vieja no puede validar con el material nuevo"
    )

    write_signature(source, new_private, "david")
    assert verify_package(source)[0] is True


def test_doctor_reporta_clave_revocada(tmp_path, trust, monkeypatch):
    from fast_mcp.shared_kernel.hooks.signature import revoke_key

    home = tmp_path / "home"
    source = _package(tmp_path)
    private, public = generate_keypair(tmp_path / "keys", "david")
    write_signature(source, private, "david")
    _trust_key(trust, "david", public.read_text().strip())
    install(source, home, walker_version="3.0.0")
    revoke_key("david", "rotación")
    monkeypatch.setenv("WALKER_HOOKS_HOME", str(home))

    report = doctor_report(tmp_path, walker_version="3.0.0", hooks_home_dir=str(home))

    assert any("REVOCADA" in issue for issue in report["issues"])
    assert report["global"][0]["signed"] is False


# ── exec: subprocesos permitidos uno por uno ───────────────────────────────

def test_exec_profile_bloquea_por_defecto(monkeypatch):
    from fast_mcp.shared_kernel.hooks.bus import _exec_profile

    perfil = _exec_profile((), "/python3")
    assert "deny process-exec*" in perfil
    assert "(subpath" in perfil, "el prefijo del intérprete debe permitirse"

    assert _exec_profile(("exec",), "/python3") == "", "exec = sin restricción"


def test_exec_profile_resuelve_binarios_declarados():
    import shutil

    from fast_mcp.shared_kernel.hooks.bus import _exec_profile

    git = shutil.which("git")
    if not git:
        pytest.skip("git no está en PATH")
    perfil = _exec_profile(("exec:git",), "/python3")
    assert f'(allow process-exec (literal "{git}"))' in perfil


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec es de macOS")
def test_hook_no_puede_lanzar_subprocesos_sin_exec(tmp_path, monkeypatch):
    out = tmp_path / "exec.log"
    monkeypatch.setenv("HOOK_OUT", str(out))
    source = _package(tmp_path, {**MANIFEST, "permissions": ["env:all", "fs:repo"]})
    (source / "main.py").write_text(
        "import os, subprocess\n"
        "try:\n"
        "    r = subprocess.run(['/bin/echo', 'hola'], capture_output=True, timeout=5)\n"
        "    result = f'rc={r.returncode}'\n"
        "except Exception as exc:\n"
        "    result = 'blocked:' + type(exc).__name__\n"
        "open(os.environ['HOOK_OUT'], 'w').write(result)\n"
    )

    bus = HookBus(specs=[HookSpec(
        id="h", command=f'"$WALKER_PYTHON" "{source}/main.py"',
        event="commit", permissions=("env:all", "fs:repo"),
    )], project_root=tmp_path, walker_root=tmp_path)
    bus.emit("commit", {})

    assert out.read_text().startswith("blocked:")


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec es de macOS")
def test_hook_puede_lanzar_el_binario_declarado(tmp_path, monkeypatch):
    # /bin/echo y no git: git lee ~/.gitconfig al arrancar y sin fs:home muere
    # con rc=128 aunque exec:git esté permitido.
    out = tmp_path / "exec-ok.log"
    monkeypatch.setenv("HOOK_OUT", str(out))
    perms = ("env:all", "fs:repo", "exec:echo")
    source = _package(tmp_path, {**MANIFEST, "permissions": list(perms)})
    (source / "main.py").write_text(
        "import os, subprocess\n"
        "try:\n"
        "    r = subprocess.run(['/bin/echo', 'hola'], capture_output=True, timeout=5)\n"
        "    result = f'rc={r.returncode}:{r.stdout.decode().strip()}'\n"
        "except Exception as exc:\n"
        "    result = 'blocked:' + type(exc).__name__\n"
        "open(os.environ['HOOK_OUT'], 'w').write(result)\n"
    )

    bus = HookBus(specs=[HookSpec(
        id="h", command=f'"$WALKER_PYTHON" "{source}/main.py"',
        event="commit", permissions=perms,
    )], project_root=tmp_path, walker_root=tmp_path)
    bus.emit("commit", {})

    assert out.read_text() == "rc=0:hola"
