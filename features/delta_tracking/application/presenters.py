"""application/presenters.py — Presentación de springs (read models).

Funciones PURAS de presentación: derivan nombres legibles y hacen backfill
de campos de lectura. No contienen IO ni dependen del repositorio.

Extraídas de SpringService (SRP): _derive_display_name y _with_display_name
vivían en el servicio mezclando presentación con orquestación.
"""

from __future__ import annotations


def derive_display_name(goal: str, spring_id: str) -> str:
    """Deriva un nombre legible del goal del spring.

    Toma la primera frase del goal (hasta ':', ';', '.', '—' o '-'),
    recortada a 60 chars. Fallback al spring_id si no hay goal.
    """
    import re

    text = (goal or "").strip()
    if text:
        for sep in (":", ";", ".", "—", "-"):
            if sep in text:
                text = text.split(sep, 1)[0].strip()
                break
        text = re.sub(r"\s+", " ", text).strip(" ,.")
    if not text:
        return spring_id
    return text if len(text) <= 60 else text[:57].rstrip() + "..."


def with_display_name(spring: dict | None) -> dict | None:
    """Backfill de display_name en lecturas (docs legacy sin el campo).

    Pura: devuelve el mismo dict con el campo completado (o None). No
    persiste — el backfill es solo de presentación.
    """
    if spring and not spring.get("display_name"):
        spring["display_name"] = derive_display_name(
            spring.get("goal", ""),
            spring.get("_id") or spring.get("spring_id", ""),
        )
    return spring
