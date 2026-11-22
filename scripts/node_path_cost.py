#!/usr/bin/env python3
"""node_path_cost.py — cuánto cuesta llegar al trabajo (F5, principio P2).

Mide el PEAJE del workflow, no la calidad del diseño: cuántas llamadas y
cuántos milisegundos hacen falta desde `session_bind` hasta el primer nodo que
pide CONTENIDO, y lo compara con el camino que el agente usa de verdad
(`implement_task` + `brief`).

No usa el modelo: camina los nodos rellenando los campos obligatorios que el
propio `workflow_state` declara. Mide ceremonia (llamadas, latencia, bytes), que
es exactamente lo que el documento de referencia dice que sobra.

Uso:
    python3 scripts/node_path_cost.py --project <id> [--task <id>] [--max-steps 25] [--json]

El proyecto debe existir (se crea con `_/prepare`). El script NO toca `walker`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

CLI = "./bin/walker3"
SID = f"nodepath-{int(time.time())}"
TASK = "nodepath-probe"


def call(project: str, tool: str, payload: dict, timeout: int = 120) -> tuple[dict, int, int, int]:
    """Ejecuta una tool del CLI. Devuelve (json, ms, request_chars, response_chars)."""
    req = json.dumps(payload)
    started = time.perf_counter()
    proc = subprocess.run(
        [CLI, f"{project}/{tool}", req],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    ms = int((time.perf_counter() - started) * 1000)
    out = proc.stdout or ""
    try:
        # El CLI puede imprimir warnings; el JSON es la última línea.
        line = [ln for ln in out.splitlines() if ln.strip().startswith("{")][-1]
        data = json.loads(line)
    except (IndexError, json.JSONDecodeError):
        data = {"ok": False, "error": (proc.stderr or out)[:400], "returncode": proc.returncode}
    return data, ms, len(req), len(out)


def synth(field: str, spec: dict, position: str) -> object:
    """Rellena un campo obligatorio con algo plausible, sin inventar dominio."""
    spec = spec or {}
    enum = spec.get("enum")
    if enum:
        if field == "decision" and "select" in enum:
            return "select"
        return enum[0]
    kind = spec.get("type")
    if kind == "array":
        return [f"{field}: valor de prueba"]
    if kind == "object":
        return {}
    if kind == "integer":
        return 1
    if kind == "boolean":
        return True
    if kind == "string":
        if field == "project_id":
            return position  # el caller lo reemplaza
        return f"{field}: valor sintético para medir el peaje del nodo"
    return "valor"


def fill(required: list[str], expected: dict, project: str, gates: list[str] | None = None) -> dict:
    """Rellena TODO lo que el paso declara, no sólo lo obligatorio.

    Dos inconsistencias reales del primer nodo, que este script NO esconde:
      * el enum de `decision` ofrece `select|create`, pero los gates que existen
        son `project_selected|create_project|refresh`;
      * el template pide `intent`, el schema lee `decision`.
    Un agente que siga el enum se queda trabado. Acá se prefiere el nombre del
    gate, que es lo que el motor acepta.
    """
    payload: dict = {}
    for field, spec in (expected or {}).items():
        payload[str(field)] = synth(str(field), spec or {}, project)
    for field in required:
        payload.setdefault(field, synth(field, expected.get(field) or {}, project))
    if "project_id" in payload:
        payload["project_id"] = project
    if "intent" in payload:
        payload["intent"] = "project_selected"
    if "decision" in payload and gates:
        payload["decision"] = gates[0]
    return payload


def walk(project: str, sid: str, max_steps: int) -> dict:
    steps: list[dict] = []
    first_content_node = None
    error = ""
    last_position = ""
    repeats = 0
    visited: dict[str, int] = {}
    base = {"session_id": sid}

    for _ in range(max_steps):
        state, ms, rq, rs = call(project, "workflow_state", dict(base))
        steps.append(
            {
                "tool": "workflow_state",
                "ms": ms,
                "req": rq,
                "res": rs,
                "position": (state.get("current_position") or ""),
            }
        )
        if state.get("ok") is False:
            error = str(state.get("error") or "workflow_state falló")
            break
        position = str(state.get("current_position") or "")
        # Detector de ciclo: si el grafo vuelve a un nodo ya visto, seguir mide
        # el loop, no el peaje. Se corta con el ciclo declarado.
        visited[position] = visited.get(position, 0) + 1
        if visited[position] > 2:
            error = f"ciclo: {position} visitado {visited[position]} veces"
            break
        required = [str(f) for f in (state.get("required_fields") or [])]
        expected = state.get("expected_payload") or {}
        exec_type = str(state.get("execution_type") or "")

        # El primer nodo que pide contenido (no un gate de decisión) es donde
        # empieza el trabajo real. Antes de eso, todo es peaje.
        if (
            exec_type == "llm_task"
            and position
            and not position.startswith("START")
            and not first_content_node
        ):
            first_content_node = {"position": position, "step": len(steps)}

        if not required:
            # Sin campos: el paso avanza solo (auto) o ya terminó.
            if not state.get("next_action"):
                break
            action = state.get("next_action") or {}
            if action.get("tool") == "transition":
                gate = (action.get("payload") or {}).get("gate") or ""
                out, ms, rq, rs = call(project, "transition", {**base, **({"gate": gate} if gate else {})})
                steps.append({"tool": "transition", "ms": ms, "req": rq, "res": rs, "gate": gate})
                if out.get("ok") is False:
                    # Un `transition` sin gate declarado espera un avance
                    # automático (el motor corre la acción). Se intenta; si
                    # tampoco avanza, se corta con el motivo visible.
                    out2, ms2, rq2, rs2 = call(project, "advance", dict(base))
                    steps.append({"tool": "advance", "ms": ms2, "req": rq2, "res": rs2})
                    if out2.get("ok") is False:
                        error = f"transition/advance: {out.get('error')} / {out2.get('error')}"
                        break
                continue
            out, ms, rq, rs = call(project, "advance", dict(base))
            steps.append({"tool": "advance", "ms": ms, "req": rq, "res": rs})
            if out.get("ok") is False:
                error = f"advance: {out.get('error')}"
                break
            continue

        # Preferencia de valores para `decision`: el map explícito del nodo es
        # el contrato; si no hay, los gates disponibles; último, el enum (que en
        # START.SELECT está desactualizado).
        decisions = list((state.get("decision_map") or {}).keys())
        payload = fill(
            required,
            expected,
            project,
            decisions or (state.get("available_gates") or []),
        )
        out, ms, rq, rs = call(project, "submit", {**payload, **base})
        steps.append(
            {
                "tool": "submit",
                "ms": ms,
                "req": rq,
                "res": rs,
                "position": position,
                "fields": sorted(payload),
            }
        )
        if out.get("ok") is False:
            error = f"submit en {position}: {out.get('error')}"
            break
        # El nodo pide DOS llamadas: `submit` registra y `transition` cruza. Es
        # el peaje que el documento de referencia cuenta como "4 submits + 4
        # transitions". Se mide acá, no se asume.
        action = out.get("next_action") or {}
        if action.get("tool") == "transition":
            gates = [str(g) for g in (action.get("gates") or [])]
            gate = ""
            payload_gate = (action.get("payload") or {}).get("gate")
            if payload_gate:
                gate = str(payload_gate)
            elif gates:
                decision = str(payload.get("decision") or "")
                gate = decision if decision in gates else gates[0]
            out2, ms2, rq2, rs2 = call(
                project, "transition", {**base, **({"gate": gate} if gate else {})}
            )
            steps.append(
                {"tool": "transition", "ms": ms2, "req": rq2, "res": rs2, "gate": gate}
            )
            if out2.get("ok") is False:
                if "gate" in str(out2.get("error") or "").lower():
                    # Sin gate declarado el nodo espera un avance automático.
                    out3, ms3, rq3, rs3 = call(project, "advance", dict(base))
                    steps.append({"tool": "advance", "ms": ms3, "req": rq3, "res": rs3})
                    if out3.get("ok") is False:
                        error = f"advance tras {position}: {out3.get('error')}"
                        break
                else:
                    error = f"transition {gate} tras {position}: {out2.get('error')}"
                    break
        if position and position == last_position:
            repeats += 1
            if repeats >= 2:
                error = f"el workflow no avanza en {position} (campos: {sorted(payload)})"
                break
        else:
            repeats = 0
        last_position = position
    return {
        "steps": steps,
        "first_content_node": first_content_node,
        "error": error,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--task", default=TASK)
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    project = args.project

    started = datetime.now(timezone.utc).isoformat()
    report: dict = {"project": project, "session": SID, "started_at": started}

    # ── Camino A: nodos ────────────────────────────────────────────────
    out, ms, rq, rs = call(project, "session_bind", {"session_id": SID, "mode": "new", "objective": "Medir el peaje del workflow."})
    bind_ms = ms
    out2, ms2, rq2, rs2 = call(
        project,
        "implement_task",
        {
            "task_id": args.task,
            "session_id": SID,
            "objective": "Medir el peaje. Debe declarar la tarea. Sin romper los tests.",
            "files": [],
        },
    )
    report["setup"] = {"bind_ms": bind_ms, "task_ms": ms2, "task_ok": out2.get("ok") is not False}

    walked = walk(project, SID, args.max_steps)
    steps = walked["steps"]
    report["nodes"] = {
        "calls": len(steps) + 2,
        "ms": sum(s["ms"] for s in steps) + bind_ms + ms2,
        "first_content_node": walked["first_content_node"],
        "positions": [s.get("position") or s.get("gate") or s["tool"] for s in steps],
        "error": walked["error"],
        "steps": steps,
    }

    # ── Camino B: tools (lo que hace el agente de verdad) ──────────────
    sid_b = f"{SID}-tools"
    out, ms, rq, rs = call(project, "session_bind", {"session_id": sid_b, "mode": "new", "objective": "Camino por tools."})
    b_bind = ms
    out, ms, rq, rs = call(
        project,
        "implement_task",
        {
            "task_id": f"{args.task}-tools",
            "session_id": sid_b,
            "objective": "Camino por tools. Debe declarar. Sin romper los tests.",
            "files": [],
        },
    )
    b_task = ms
    out, ms, rq, rs = call(project, "brief", {"session_id": sid_b, "task_id": f"{args.task}-tools"})
    b_brief = ms
    report["tools"] = {
        "calls": 3,
        "ms": b_bind + b_task + b_brief,
        "brief_ok": out.get("ok") is not False,
        "brief_chars": rs,
    }

    if report["nodes"]["calls"]:
        report["ratio"] = {
            "calls": round(report["nodes"]["calls"] / report["tools"]["calls"], 2),
            "ms": round(report["nodes"]["ms"] / max(1, report["tools"]["ms"]), 2),
        }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        n, t = report["nodes"], report["tools"]
        print(f"nodos : {n['calls']} llamadas · {n['ms']} ms · primer nodo de contenido: {n['first_content_node']}")
        print(f"tools : {t['calls']} llamadas · {t['ms']} ms · brief: {t['brief_chars']} chars")
        if report.get("ratio"):
            print(f"ratio : {report['ratio']['calls']}x llamadas · {report['ratio']['ms']}x tiempo")
        if n["error"]:
            print(f"⚠ nodos se detuvo: {n['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
