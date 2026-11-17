"""spec_derive.py — criterios y precondiciones derivados del pedido.

El usuario enuncia condiciones en lenguaje natural ("sin romper los tests",
"debe devolver 0", "dado que el test falla"). El workflow las necesita como
campos estructurados (`acceptance_criteria`, `preconditions`) para poder
verificarlas después.

Es heurística, no comprensión: parte el pedido en cláusulas y clasifica las que
traen una marca de obligación o de supuesto. Lo que NO encuentra queda vacío y
marcado (`source: "empty"`); inventar criterios sería peor que no tenerlos,
porque después alguien los verifica contra el código.
"""

from __future__ import annotations

import re

CRITERIA_MARKERS = (
    "sin romper", "no romper", "no rompas", "no modificar", "no modifiques",
    "no cambiar", "no cambies", "sin cambiar", "sin tocar", "preserv",
    "manten", "conserv", "debe", "debería", "deberia", "tiene que", "tienen que",
    "han de", "must", "should", "without breaking", "don't break", "keep ",
    "preserve", "los tests", "el test", "tests pass", "pase", "pasen",
)

PRECONDITION_MARKERS = (
    "dado que", "asumiendo", "asumimos", "si ya", "requiere", "requieren",
    "parte de", "given that", "assuming", "requires",
)

_MAX_ITEMS = 5
_MAX_LEN = 200
_CLAUSE_SPLIT = re.compile(r"(?<=[.;!?])\s+|,\s+(?=(?:sin|no|que|con|sin\s|debe|deber|tiene|manten|preserv|los tests|el test)\b)", re.IGNORECASE)


def _clauses(text: str) -> list[str]:
    raw = _CLAUSE_SPLIT.split(text or "")
    out: list[str] = []
    for chunk in raw:
        for piece in re.split(r"\s+y\s+(?=(?:sin|no|que|debe|tiene)\b)", chunk, flags=re.IGNORECASE):
            cleaned = " ".join(piece.split()).strip(" .;,")
            if cleaned:
                out.append(cleaned)
    return out


def _first_marker_index(low: str, markers: tuple[str, ...]) -> int:
    found = [low.find(m) for m in markers if m in low]
    return min(found) if found else -1


def _add(items: list[str], seen: set[str], text: str) -> None:
    item = text.strip(" .;,")[:_MAX_LEN]
    key = item.lower()
    if item and key not in seen and len(items) < _MAX_ITEMS:
        seen.add(key)
        items.append(item)


def derive(objective: str) -> dict:
    """Deriva criterios de aceptación y precondiciones del pedido.

    - Una cláusula con marca de supuesto ("dado que", "asumiendo") es
      PRECONDICIÓN, aunque además mencione tests.
    - De una cláusula con marca de obligación se recorta desde la marca: de
      "Arreglá X sin romper los tests" el criterio es "sin romper los tests",
      no toda la frase.
    - Lo que no matchea no se inventa: `source: "empty"`.

    Returns:
        {"acceptance_criteria": [...], "preconditions": [...], "source": ...}
    """
    clauses = _clauses(objective)

    preconditions: list[str] = []
    used: set[str] = set()
    seen_pre: set[str] = set()
    for clause in clauses:
        if _first_marker_index(clause.lower(), PRECONDITION_MARKERS) < 0:
            continue
        _add(preconditions, seen_pre, clause)
        used.add(clause)

    criteria: list[str] = []
    seen_crit: set[str] = set()
    for clause in clauses:
        if clause in used:
            continue
        low = clause.lower()
        idx = _first_marker_index(low, CRITERIA_MARKERS)
        if idx < 0:
            continue
        _add(criteria, seen_crit, clause[idx:] if idx > 0 else clause)

    return {
        "acceptance_criteria": criteria,
        "preconditions": preconditions,
        "source": "heuristic" if (criteria or preconditions) else "empty",
    }
