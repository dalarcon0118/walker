"""domain/analysis.py — Analysis findings as domain entities.

Result pattern (dry-python/returns): las lecturas de archivos del scanner
usan cores @safe; los fallos se loggean (convention-001: nunca silencioso).
"""

from __future__ import annotations
import logging
import os
from dataclasses import dataclass

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


# Entry points invocados desde FUERA de Python (Makefile `check`/`doctor`,
# scripts). No tienen CALLS edge dentro del repo, pero son API pública real.
_EXTERNAL_ENTRYPOINTS = {"check_and_report"}


def is_false_positive_dead_code(
    name: str,
    node_type: str = "",
    signature: str = "",
    path: str = "",
    kind: str = "",
) -> bool:
    """Filtra falsos positivos estructurales del análisis de dead code.

    El CPG marca como 'unused' símbolos que el runtime sí usa:
      1. test_*            → pytest los descubre por naming, no por imports
      2. __x__ (dunder)    → invocados implícitamente por el framework/ODM
                             (__index__, __search__, __id_field__, __str__...)
      3. fields/constantes → members de clase con firma SIN 'def' se indexan
                             como 'method' (dataclass fields, ODM fields,
                             constantes tipo TYPE_SPRING). No son callables.

    Retorna True si el símbolo es un falso positivo y NO debe reportarse
    como dead code.
    """
    # Los callee stubs externos (`server.add_tool`, `Path`,
    # `traceback.print_exc`) se indexan como `type=function` +
    # `kind=external_call`: no son funciones del proyecto y no pueden ser
    # dead code. Medido 2026-11-18: eran 1.014 de los 1.014 "unused_functions".
    if kind == "external_call":
        return True
    if name in _EXTERNAL_ENTRYPOINTS:
        return True
    if name.startswith("test_") or name.endswith("_test"):
        return True
    if name.startswith("__") and name.endswith("__"):
        return True
    if node_type == "method" and not (signature or "").strip().startswith("def "):
        return True
    return False


@safe
def _read_source_file(fp: str) -> str:
    """Core puro: lee un archivo fuente (puede lanzar OSError; @safe captura)."""
    with open(fp, errors="ignore") as fh:
        return fh.read()


class DeadCodeReferenceScanner:
    """Escaneo de referencias por texto para detectar usos no-CTT.

    Complementa el análisis de edges del CPG. Un símbolo sin CALLS incoming
    puede estar vivo por:
      - registro dinámico por string (registry.register("task", handler))
      - imports lazy (dentro de funciones) — el CPG no los indexa como IMPORTS
      - dispatch por nombre (getattr, maps de handlers)

    Si el nombre del símbolo aparece como identificador (word boundary) en
    CUALQUIER otro archivo del proyecto distinto del que lo define, se
    considera que está referenciado y NO es dead code.
    """

    _IDENT_RE = None  # compilada lazy para evitar coste de import

    def __init__(self, source_files: list[str]):
        # Archivos fuente del proyecto (paths absolutos), para resolver
        # referencias fuera del propio archivo de definición.
        self._source_files = [os.path.realpath(f) for f in source_files if f]
        # Caché de textos (una lectura por archivo y por BUILD, no por
        # símbolo). Sin esto, is_referenced re-leía TODO el proyecto por
        # cada función candidata: 2.7k funciones × 1.1k archivos ≈ 3M de
        # lecturas de disco (~260s de un build de 290s, y el server MCP
        # entero bloqueado hasta el timeout -32001).
        self._text_cache: dict[str, str] = {}

    def _text(self, fp: str) -> str:
        """Texto del archivo (cacheado). Vacío si ilegible (skip idéntico)."""
        cached = self._text_cache.get(fp)
        if cached is None:
            result = _read_source_file(fp)
            if not is_successful(result):
                error = result.failure()
                logger.debug(
                    "Archivo ilegible %s (%s); se omite del scan de referencias",
                    fp, error,
                )
                cached = ""
            else:
                cached = result.value_or("")
            self._text_cache[fp] = cached
        return cached

    @classmethod
    def _ident_re(cls):
        if cls._IDENT_RE is None:
            import re

            cls._IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
        return cls._IDENT_RE

    def _is_registered_string(self, name: str, path: str) -> bool:
        """True si el nombre aparece como STRING literal (entre comillas) en
        cualquier archivo — patrón típico de registry.register("name", fn).

        Se busca también en el propio archivo de definición (donde suele
        estar el registro), porque allí el string aparece, no el edge.
        """
        quoted = f'"{name}"'
        quoted_single = f"'{name}'"
        for fp in self._source_files:
            text = self._text(fp)
            if quoted in text or quoted_single in text:
                return True
        return False

    def _other_files(self, path: str) -> list[str]:
        own = os.path.realpath(path) if path else ""
        return [f for f in self._source_files if f != own]

    def _referenced_in_own_file(self, name: str, path: str, def_line: int) -> bool:
        """True si `name` aparece como identificador en el MISMO archivo, en
        una línea distinta a la de definición.

        Captura dispatch intra-archivo: maps de comandos ("goto": cmd_goto),
        dicts de handlers ("symbol_exists": _verify_symbol_exists), etc.
        El CPG no crea edges CALLS para referencias a funciones como valores.
        """
        if not path:
            return False
        content = self._text(path)
        for lineno, line in enumerate(content.splitlines(), 1):
            if lineno == def_line:
                continue
            if name in line:
                return True
        return False

    def is_pytest_fixture(self, path: str, def_line: int = 0) -> bool:
        """True si la función está decorada con `@pytest.fixture`.

        Las fixtures se inyectan por nombre en los tests: no tienen CALLS ni
        una referencia textual que el scanner pueda ver (y nombres genéricos
        como `ctx` se descartan a propósito en `is_referenced`). Sin este
        filtro, cada fixture del repo aparece como dead code.
        """
        if not path or def_line <= 0:
            return False
        text = self._text(os.path.realpath(path))
        if not text:
            return False
        lines = text.splitlines()
        idx = min(def_line - 1, len(lines) - 1)
        while idx > 0 and not lines[idx].lstrip().startswith("def "):
            idx -= 1
        for j in range(max(0, idx - 6), idx):
            if "@pytest.fixture" in lines[j] or "@fixture" in lines[j]:
                return True
        return False

    def is_referenced(self, name: str, path: str, def_line: int = 0) -> bool:
        """True si `name` aparece como identificador en otro archivo, como
        string literal de registro, o como identificador en el propio archivo
        fuera de su línea de definición."""
        if not name or len(name) < 2:
            return False
        # Nombres demasiado genéricos se descartan (evita falsos negativos
        # por variables locales de nombres comunes: data, result, value...).
        if name.lower() in {
            "data", "result", "value", "status", "name", "path", "file",
            "config", "ctx", "context", "item", "items", "key", "node",
            "state", "index", "params", "args", "kwargs", "meta", "extra",
            "type", "id", "info", "count", "total", "error", "log",
        }:
            return False

        # 1. Registro dinámico por string (registry.register("name", ...))
        if self._is_registered_string(name, path):
            return True

        # 2. Identificador en otro archivo (imports, dispatch, llamadas).
        #    `name in text` es equivalente al chequeo por-línea: name es un
        #    identificador, por lo que regex.search(line) es redundante.
        for other in self._other_files(path):
            if name in self._text(other):
                return True

        # 3. Identificador en el propio archivo (dispatch intra-archivo)
        if self._referenced_in_own_file(name, path, def_line):
            return True

        return False


@dataclass
class BugFinding:
    """A bug found during CPG analysis."""

    bug_type: str  # dead_branches, missing_returns, infinite_loops, unused_functions
    function: str
    file: str
    line: int = 0
    detail: str = ""
    is_private: bool = False
    is_method: bool = False
    incoming_calls: int = 0
    incoming_imports: int = 0
    parent_class: str = ""
    parent_type: str = ""

    def to_dict(self) -> dict:
        return {
            "bug": self.bug_type,
            "function": self.function,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
            "is_private": self.is_private,
            "is_method": self.is_method,
            "incoming_calls": self.incoming_calls,
            "incoming_imports": self.incoming_imports,
            "parent_type": self.parent_type,
            "parent_class": self.parent_class,
        }


@dataclass
class SecurityFinding:
    """A security vulnerability found during CPG analysis."""

    finding_type: str  # taint_flow, hardcoded_secret
    risk: str
    file: str
    line: int = 0
    detail: str = ""
    variable: str = ""
    flow: str = ""

    def to_dict(self) -> dict:
        d = {
            "type": self.finding_type,
            "risk": self.risk,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }
        if self.variable:
            d["variable"] = self.variable
        if self.flow:
            d["flow"] = self.flow
        return d


@dataclass
class DeadCodeCandidate:
    """A function that may be dead code, with context for classification."""

    function: str
    file: str
    line: int
    is_private: bool
    is_method: bool
    incoming_calls: int
    incoming_imports: int
    parent_class: str
    parent_type: str

    def classify(self) -> str:
        """Classify as dead_code, unintegrated, or dynamic_dispatch.

        The agent should override this with rules from task manifest.
        This is a heuristic fallback.
        """
        if self.incoming_calls > 0:
            return "called"
        return "dead_code" if self.is_private else "unintegrated"

    def to_dict(self) -> dict:
        return {
            "bug": "unused_functions",
            "function": self.function,
            "file": self.file,
            "line": self.line,
            "is_private": self.is_private,
            "is_method": self.is_method,
            "incoming_calls": self.incoming_calls,
            "incoming_imports": self.incoming_imports,
            "parent_type": self.parent_type,
            "parent_class": self.parent_class,
        }
