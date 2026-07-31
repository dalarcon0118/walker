"""_json_utils.py — Shared JSON I/O"""

from __future__ import annotations
import json
import sys


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_json(path: str) -> dict | None:
    """Load a JSON file. Returns None on error."""
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        eprint(f"❌ JSON inválido en {path}: {e}")
        return None
    except FileNotFoundError:
        eprint(f"❌ Archivo no encontrado: {path}")
        return None
    except Exception as e:
        eprint(f"❌ Error leyendo {path}: {e}")
        return None


def write_json(path: str, data: dict) -> bool:
    """Write a JSON file. Returns True on success."""
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        eprint(f"❌ Error escribiendo {path}: {e}")
        return False
