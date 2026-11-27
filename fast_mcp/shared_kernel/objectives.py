"""Comparación de objetivos de tareas para evitar duplicados (4D Duplicidad).

El sprint se llenó de copias con el mismo objetivo y distinto texto
("Versionar data/workflow" vs "Versionar data/workflow o eliminar la
dependencia JSON..."). La comparación exacta no las ve. Acá se normaliza y se
compara por subconjunto de tokens: si el objetivo corto está contenido en el
largo, es la misma tarea.

Es una heurística deliberadamente conservadora: exige al menos 3 tokens y 15
caracteres en el lado corto, para no fusionar "arreglar resta" con "arreglar
suma".
"""
from __future__ import annotations

import re
import unicodedata

_MIN_TOKENS = 3
_MIN_CHARS = 15


def normalize_objective(text: str) -> str:
    """Minúsculas, sin acentos, sin puntuación, espacios colapsados."""
    raw = unicodedata.normalize("NFKD", str(text or ""))
    raw = "".join(char for char in raw if not unicodedata.combining(char))
    raw = raw.lower()
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return " ".join(raw.split())


def objectives_match(a: str, b: str) -> bool:
    """¿Dos objetivos describen la misma tarea?

    Reglas, en orden:
      1. normalizados iguales;
      2. el conjunto de tokens de uno está contenido en el otro (objetivo
         truncado o ampliado);
      3. uno es prefijo textual del otro;
      4. Jaccard >= 0.85 (paráfrasis casi idéntica).
    """
    left = normalize_objective(a)
    right = normalize_objective(b)
    if not left or not right:
        return False
    if left == right:
        return True

    left_tokens = left.split()
    right_tokens = right.split()
    if min(len(left_tokens), len(right_tokens)) < _MIN_TOKENS:
        return False
    if min(len(left), len(right)) < _MIN_CHARS:
        return False

    left_set = set(left_tokens)
    right_set = set(right_tokens)
    if left_set <= right_set or right_set <= left_set:
        return True
    if left.startswith(right) or right.startswith(left):
        return True
    return len(left_set & right_set) / max(1, len(left_set | right_set)) >= 0.85
