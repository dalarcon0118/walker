#!/usr/bin/env python3
"""scripts/request_fidelity.py — ¿la spec dice lo que el usuario pidió?

El engine sólo puede embudar lo que registra. Hasta 2026-11-21 el primer
mensaje del usuario no se persistía (`session_open` guardaba el placeholder
`sesión <id>` y el `brief` del intake se llamaba sin `text`), así que la spec
heredaba únicamente la paráfrasis del agente.

Este reporte mide la distancia entre el PEDIDO (session.goal) y el OBJETIVO
declarado en la spec, con solapamiento de tokens (Jaccard). No juzga si el
agente acertó: mide si el engine tiene con qué juzgarlo.

Uso:
    python3 scripts/request_fidelity.py walker
    python3 scripts/request_fidelity.py walker --json
    python3 scripts/request_fidelity.py walker --min-overlap 0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

# Palabras sin contenido para la comparación (español básico).
_STOP = {
    "para", "como", "este", "esta", "esto", "debe", "deben", "tiene", "hace",
    "desde", "todo", "todos", "todas", "pero", "cuando", "donde", "porque",
    "sobre", "entre", "hacia", "solo", "sólo", "muy", "más", "mas", "los",
    "las", "del", "que", "con", "por", "una", "uno", "unos", "unas", "sin",
}

# El placeholder que escribe `session_bind` cuando no hay objective.
_PLACEHOLDER = re.compile(r"^sesi[oó]n\s+\S+$", re.IGNORECASE)


def tokens(text: str) -> set[str]:
    """Tokens con contenido (>=4 letras/dígitos), sin stopwords."""
    words = re.findall(r"[a-záéíóúñü0-9]{4,}", str(text or "").lower())
    return {w for w in words if w not in _STOP}


def is_placeholder(goal: str) -> bool:
    return bool(_PLACEHOLDER.match(str(goal or "").strip()))


def overlap(request: str, objective: str) -> float:
    """Jaccard entre el pedido y el objetivo. 0.0 = no comparten nada."""
    a, b = tokens(request), tokens(objective)
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


def summarize_pairs(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega los pares {session, request, objective, overlap} ya calculados."""
    valid = [p for p in pairs if not p.get("placeholder") and p.get("request")]
    scores = sorted(p["overlap"] for p in valid)
    if not scores:
        return {
            "pairs": len(pairs),
            "measurable": 0,
            "without_request": sum(1 for p in pairs if p.get("placeholder")),
            "mean": None,
            "median": None,
            "buckets": {},
            "worst": [],
        }
    mid = scores[len(scores) // 2]
    buckets = {"bajo": 0, "medio": 0, "alto": 0}
    for score in scores:
        if score < 0.15:
            buckets["bajo"] += 1
        elif score < 0.35:
            buckets["medio"] += 1
        else:
            buckets["alto"] += 1
    worst = sorted(valid, key=lambda p: p["overlap"])[:5]
    return {
        "pairs": len(pairs),
        "measurable": len(valid),
        "without_request": sum(1 for p in pairs if p.get("placeholder")),
        "mean": round(sum(scores) / len(scores), 3),
        "median": mid,
        "buckets": buckets,
        "worst": worst,
    }


def _collect(project_id: str) -> list[dict[str, Any]]:
    # El script se ejecuta como archivo: el root del repo no está en sys.path.
    from pathlib import Path

    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)

    from fast_mcp.composition import build_sprint_service

    svc = build_sprint_service(project_id)
    if svc is None:
        return []
    pairs: list[dict[str, Any]] = []
    # Sin filtro por `project_id`: el registro lo deja vacío en muchas
    # sesiones (el join real es por `task_context.session_id`).
    for session in svc.list_sessions(limit=500):
        sid = str(session.get("session_id") or "")
        spring = str(session.get("spring_id") or "")
        goal = str(session.get("goal") or "")
        if not sid or not spring:
            continue
        try:
            specs = svc.get_specs(spring)
        except Exception:  # noqa: BLE001 — un sprint ilegible no corta el reporte
            continue
        for spec in specs:
            if str((spec.get("task_context") or {}).get("session_id") or "") != sid:
                continue
            objective = str(spec.get("objective") or "")
            pairs.append(
                {
                    "session": sid,
                    "spec_id": str(spec.get("spec_id") or ""),
                    "request": goal,
                    "objective": objective,
                    "placeholder": is_placeholder(goal),
                    "overlap": overlap(goal, objective),
                }
            )
    return pairs


def _print(summary: dict[str, Any], pairs: list[dict[str, Any]]) -> None:
    print(f"pares sesión↔spec: {summary['pairs']}")
    print(f"  con request registrada: {summary['measurable']}")
    print(f"  con placeholder: {summary['without_request']}")
    if summary["measurable"]:
        print(f"solapamiento pedido↔objetivo: media={summary['mean']} mediana={summary['median']}")
        for name, count in summary["buckets"].items():
            print(f"  {name}: {count}")
        print("peores (posible paráfrasis que recorta el pedido):")
        for pair in summary["worst"]:
            print(f"  {pair['spec_id']} {pair['overlap']}: {pair['request'][:60]!r} -> {pair['objective'][:60]!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    pairs = _collect(args.project_id)
    summary = summarize_pairs(pairs)
    if args.json:
        print(json.dumps({"summary": summary, "pairs": pairs}, ensure_ascii=False, indent=2))
    else:
        _print(summary, pairs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
