"""domain/degradations.py — degradaciones visibles en el resultado de un task.

Un handler puede completar su contrato (`ok: True`) y aun así dejar una parte
sin hacer: el CPG no estaba disponible, el contexto de sprint falló, un archivo
no se pudo revisar. Hoy esas señales viven en el `data` del resultado y sólo las
ve quien lo lee.

`result_notes` las extrae con un contrato explícito y ACOTADO — no adivina
cualquier texto: `warnings`, `available: False` (propio o de un nivel anidado)
y `findings[].error`. La composición las traza y el reporte las cuenta.

Es una función PURA (sin IO) para poder testearla sin registry ni disco.
"""
from __future__ import annotations

from typing import Any

#: Techo de notas por task: una degradación repetida 50 veces no es 50 señales.
MAX_NOTES = 5
#: El trace no es un log: el detalle se acota.
DETAIL_MAX = 300


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())[:DETAIL_MAX]


def result_notes(result: Any) -> list[dict[str, str]]:
    """Notas de degradación de un `TaskResult` (vacío si no hay ninguna).

    Formato de cada nota: `{"kind", "detail", "source"}`.
      - kind=warning        el handler reportó `warnings`
      - kind=unavailable    reportó `available: False` (con su error)
      - kind=finding_error  un hallazgo con `error` (archivo que no se pudo revisar)
    """
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return []

    notes: list[dict[str, str]] = []

    def add(kind: str, detail: Any, source: str = "") -> None:
        if len(notes) >= MAX_NOTES:
            return
        text = _text(detail)
        if not text:
            return
        notes.append({"kind": kind, "detail": text, "source": source})

    warnings = data.get("warnings")
    if isinstance(warnings, (list, tuple)):
        for warning in warnings:
            add("warning", warning)
    elif warnings:
        add("warning", warnings)

    if data.get("available") is False:
        add("unavailable", data.get("error") or "sin detalle", source="result")

    # Un nivel: los resultados anidan estados por dimensión (review.cpg).
    for key, value in data.items():
        if isinstance(value, dict) and value.get("available") is False:
            add("unavailable", value.get("error") or "sin detalle", source=str(key))

    findings = data.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, dict) and item.get("error"):
                source = str(item.get("source") or item.get("file") or "findings")
                add("finding_error", f"{source}: {item['error']}", source=source)

    return notes


def degradation_event(
    task: str,
    note: dict[str, str],
    *,
    task_id: str = "",
    session_id: str = "",
) -> dict:
    """Evento de trace de UNA nota (shape único, testeable sin IO).

    El literal `event_type` queda acá, junto al contrato de la degradación: la
    composición sólo cablea (y el guard de SSOT de schemas sigue valiendo).
    """
    return {
        "event_type": "task_degraded",
        "source": "walker_task",
        "session_id": session_id,
        "task_id": task_id,
        "data": {"task": task, **note},
    }


__all__ = ["MAX_NOTES", "degradation_event", "result_notes"]
