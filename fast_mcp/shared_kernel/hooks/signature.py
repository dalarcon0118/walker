"""shared_kernel/hooks/signature.py — firmas ed25519 y claves confiables.

El digest del árbol (`tree-v1`) da integridad contra un hash publicado, pero no
autoría: si el hash sale del mismo repo que el código, un atacante publica los
dos. La firma cierra eso: se firma el digest con una clave ed25519 y se verifica
contra una lista de claves confiables del usuario, que es el único canal de
confianza.

Archivos:

    ~/.walker/trusted_keys.json        ← {"keys": [{id, ed25519, added_at}]}
    <paquete>/hook.sig.json            ← {alg, key_id, sha256, signature}

`hook.sig.json` queda FUERA del digest (si no, firmar cambiaría el hash firmado).

Formato de firma: ed25519 sobre el digest hex en UTF-8. `cryptography` es
dependencia declarada; si no está, verificar falla con un error accionable (no
se degrada a "válido").
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SIG_NAME = "hook.sig.json"
TRUSTED_KEYS_ENV = "WALKER_TRUSTED_KEYS"


class SignatureError(ValueError):
    """Firma inválida, clave desconocida o soporte criptográfico ausente."""


@dataclass(frozen=True)
class TrustedKey:
    id: str
    ed25519: str  # public key raw en base64
    added_at: str = ""
    revoked_at: str = ""
    revoked_reason: str = ""
    rotated_at: str = ""
    rotated_from: str = ""  # fingerprint corto de la clave anterior

    @property
    def revoked(self) -> bool:
        return bool(self.revoked_at)


def keys_path() -> Path:
    override = os.environ.get(TRUSTED_KEYS_ENV)
    return Path(override) if override else Path.home() / ".walker" / "trusted_keys.json"


def load_keys(path: str | Path | None = None) -> dict[str, TrustedKey]:
    target = Path(path) if path else keys_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    keys: dict[str, TrustedKey] = {}
    for raw in data.get("keys") or []:
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("ed25519"):
            continue
        keys[str(raw["id"])] = TrustedKey(
            id=str(raw["id"]),
            ed25519=str(raw["ed25519"]),
            added_at=str(raw.get("added_at", "")),
            revoked_at=str(raw.get("revoked_at", "")),
            revoked_reason=str(raw.get("revoked_reason", "")),
            rotated_at=str(raw.get("rotated_at", "")),
            rotated_from=str(raw.get("rotated_from", "")),
        )
    return keys


def save_keys(keys: dict[str, TrustedKey], path: str | Path | None = None) -> Path:
    target = Path(path) if path else keys_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "keys": [
            {
                "id": key.id,
                "ed25519": key.ed25519,
                "added_at": key.added_at,
                "revoked_at": key.revoked_at,
                "revoked_reason": key.revoked_reason,
                "rotated_at": key.rotated_at,
                "rotated_from": key.rotated_from,
            }
            for key in keys.values()
        ]
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SignatureError(f"base64 inválido: {exc}") from exc


def public_key_b64(public_key) -> str:
    """Serializa una Ed25519PublicKey (objeto cryptography) a base64 raw."""
    from cryptography.hazmat.primitives import serialization

    return _b64(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def load_private_key(path: str | Path):
    from cryptography.hazmat.primitives import serialization

    data = Path(path).read_bytes()
    return serialization.load_pem_private_key(data, password=None)


def load_public_key(key: TrustedKey):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    raw = _unb64(key.ed25519)
    if len(raw) != 32:
        raise SignatureError(f"clave ed25519 de {key.id} no tiene 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def generate_keypair(directory: str | Path, key_id: str) -> tuple[Path, Path]:
    """Genera par ed25519 (PEM) y devuelve (privada, pública_raw_b64 en .pub)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    priv_path = out / f"{key_id}.pem"
    priv_path.write_bytes(private_pem)
    os.chmod(priv_path, 0o600)
    pub_path = out / f"{key_id}.pub"
    pub_path.write_text(public_key_b64(private.public_key()) + "\n")
    return priv_path, pub_path


def revoke_key(
    key_id: str, reason: str = "", path: str | Path | None = None
) -> TrustedKey:
    """Marca una clave como revocada (deja de verificar firmas nuevas)."""
    keys = load_keys(path)
    key = keys.get(key_id)
    if key is None:
        raise SignatureError(f"no existe la clave '{key_id}'")
    updated = TrustedKey(
        id=key.id,
        ed25519=key.ed25519,
        added_at=key.added_at,
        revoked_at=datetime.now(timezone.utc).isoformat(),
        revoked_reason=reason,
        rotated_at=key.rotated_at,
        rotated_from=key.rotated_from,
    )
    keys[key_id] = updated
    save_keys(keys, path)
    return updated


def rotate_key(
    key_id: str, new_public_b64: str, path: str | Path | None = None
) -> TrustedKey:
    """Reemplaza el material de una clave conservando el id.

    La clave anterior deja de validar: las firmas viejas fallan (esperado) y hay
    que firmar de nuevo con la privada nueva. Se registra `rotated_from` para
    auditar el cambio.
    """
    keys = load_keys(path)
    key = keys.get(key_id)
    if key is None:
        raise SignatureError(f"no existe la clave '{key_id}'")
    updated = TrustedKey(
        id=key.id,
        ed25519=new_public_b64,
        added_at=key.added_at,
        revoked_at=key.revoked_at,
        revoked_reason=key.revoked_reason,
        rotated_at=datetime.now(timezone.utc).isoformat(),
        rotated_from=key.ed25519[:16],
    )
    keys[key_id] = updated
    save_keys(keys, path)
    return updated


def sign_digest(digest_hex: str, private_key_path: str | Path, key_id: str) -> dict:
    private = load_private_key(private_key_path)
    signature = private.sign(digest_hex.encode("utf-8"))
    return {
        "alg": "ed25519",
        "key_id": key_id,
        "sha256": digest_hex,
        "signature": _b64(signature),
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }


def write_signature(package_dir: str | Path, private_key_path: str | Path,
                    key_id: str) -> dict:
    from fast_mcp.shared_kernel.hooks.package import tree_digest

    digest = tree_digest(package_dir)
    payload = sign_digest(digest, private_key_path, key_id)
    (Path(package_dir) / SIG_NAME).write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def verify_package(
    package_dir: str | Path,
    *,
    keys: dict[str, TrustedKey] | None = None,
    require: bool = False,
) -> tuple[bool, str]:
    """(ok, detalle). `require=True` falla si el paquete no está firmado."""
    from fast_mcp.shared_kernel.hooks.package import tree_digest

    directory = Path(package_dir)
    sig_file = directory / SIG_NAME
    if not sig_file.is_file():
        return (False, "sin firma") if require else (True, "sin firma (no exigida)")
    try:
        payload = json.loads(sig_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SignatureError(f"{SIG_NAME} ilegible: {exc}") from exc

    if payload.get("alg") != "ed25519":
        raise SignatureError(f"algoritmo no soportado: {payload.get('alg')!r}")
    key_id = str(payload.get("key_id", ""))
    trusted = keys if keys is not None else load_keys()
    key = trusted.get(key_id)
    if key is None:
        raise SignatureError(
            f"firmado por '{key_id}', que NO está en las claves confiables "
            f"({keys_path()}). Si confiás en esa clave: walker hooks keys add"
        )
    if key.revoked:
        motivo = f" — {key.revoked_reason}" if key.revoked_reason else ""
        raise SignatureError(
            f"firmado por '{key_id}', clave REVOCADA el {key.revoked_at[:10]}"
            f"{motivo}. Un paquete firmado con una clave revocada no se instala: "
            "pedile al autor que firme con la clave nueva"
        )
    if str(payload.get("sha256", "")).lower() != tree_digest(directory):
        raise SignatureError(
            "el digest firmado no coincide con el contenido actual del paquete "
            "(fue modificado después de firmarse)"
        )
    try:
        load_public_key(key).verify(_unb64(payload.get("signature", "")),
                                    str(payload.get("sha256")).encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — InvalidSignature y variantes
        raise SignatureError(f"firma inválida para la clave '{key_id}': {exc}") from exc
    return True, f"firmado por {key_id}"
