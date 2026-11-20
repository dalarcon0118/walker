"""workflow/domain/artifacts.py — validación por FORMA de artefactos de fase.

AGENT-EFFICIENCY §2.3 (AE-INV-06): el gate valida el artefacto, no "que el
agente haya pensado". `design_brief` es un objeto —no 8 submits— con
alternativas, descartada con motivo, invariantes y verificación.

El error es estructurado y trae `remedy`: un deny sin remedio es un bug
(FC-INV-07).
"""

from __future__ import annotations

from typing import Any

_REMEDY = (
    "Enviá `design_brief` en el submit de DESIGN_ANALYZE con: problem, "
    "options (>=2, cada una con id/summary/cost/risk), chosen, "
    "rejected_because (motivo por cada opción no elegida), "
    "invariants_touched (>=1) y verification (cada una con criterion, "
    "check=test|command y ref)."
)


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def validate_design_brief(brief: Any) -> dict | None:
    """Devuelve None si el brief es válido, o un error con `missing`."""
    if not isinstance(brief, dict):
        return {"error": "design_brief debe ser un objeto", "missing": ["design_brief"],
                "remedy": _REMEDY}

    missing: list[str] = []
    if len(str(brief.get("problem") or "").strip()) < 10:
        missing.append("problem: describí el problema (>=10 caracteres)")

    options = _as_list(brief.get("options"))
    if len(options) < 2:
        missing.append("options: se exigen al menos 2 alternativas")
    ids: list[str] = []
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            missing.append(f"options[{index}]: debe ser un objeto")
            continue
        oid = str(option.get("id") or "").strip()
        if not oid:
            missing.append(f"options[{index}].id: vacío")
        elif oid in ids:
            missing.append(f"options[{index}].id: '{oid}' repetido")
        ids.append(oid)
        for key in ("summary", "cost", "risk"):
            if not str(option.get(key) or "").strip():
                missing.append(f"options[{oid or index}].{key}: vacío")

    chosen = str(brief.get("chosen") or "").strip()
    if chosen not in ids:
        missing.append("chosen: debe ser el id de una de las alternativas")

    rejected = brief.get("rejected_because")
    if not isinstance(rejected, dict):
        missing.append("rejected_because: debe ser un objeto {id: motivo}")
        rejected = {}
    for oid in ids:
        if oid and oid != chosen and not str(rejected.get(oid) or "").strip():
            missing.append(f"rejected_because[{oid}]: falta el motivo del descarte")

    invariants = _as_list(brief.get("invariants_touched"))
    if not invariants:
        missing.append("invariants_touched: declarar al menos un invariante")

    verification = _as_list(brief.get("verification"))
    if not verification:
        missing.append("verification: declarar al menos una verificación")
    for index, item in enumerate(verification):
        if not isinstance(item, dict):
            missing.append(f"verification[{index}]: debe ser un objeto")
            continue
        if not str(item.get("criterion") or "").strip():
            missing.append(f"verification[{index}].criterion: vacío")
        if str(item.get("check") or "") not in ("test", "command"):
            missing.append(f"verification[{index}].check: debe ser test|command")
        if not str(item.get("ref") or "").strip():
            missing.append(f"verification[{index}].ref: vacío")

    if not missing:
        return None
    return {"error": "design_brief inválido", "missing": missing, "remedy": _REMEDY}


_VALIDATORS = {"design_brief": validate_design_brief}

#: Contrato versionado de artefactos por (nodo, sub-estado). El nodo puede
#: declararlo con `x-artifact`, pero `data/workflow/` está gitignoreado: en un
#: checkout limpio el nodo no lo trae y el gate quedaría inerte. Esta tabla es
#: la fuente que SÍ viaja con el código (se consulta como fallback).
ARTIFACT_GATES: dict[tuple[str, str], str] = {
    ("DESIGN", "DESIGN_ANALYZE"): "design_brief",
}


def artifact_for(node_id: str, sub_state_key: str) -> str:
    return ARTIFACT_GATES.get((node_id, sub_state_key), "")


def validate_artifact(kind: str, payload: dict) -> dict | None:
    """Valida `payload[kind]` (o el payload si ya es el artefacto)."""
    validator = _VALIDATORS.get(kind)
    if validator is None:
        return {"error": f"artefacto desconocido: '{kind}'", "remedy": "Revisar x-artifact."}
    artifact = payload.get(kind) if isinstance(payload, dict) else None
    if artifact is None:
        return {"error": f"falta el artefacto '{kind}'", "missing": [kind], "remedy": _REMEDY}
    return validator(artifact)
