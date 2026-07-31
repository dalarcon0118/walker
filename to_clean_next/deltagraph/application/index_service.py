"""application/index_service.py — Indexación de sprints en DeltaGraph.

Usa TokenIndex para indexar tareas de plan.json.
"""

from __future__ import annotations
import json
import os
import re

from deltagraph.domain.models import TokenIndex, IndexEntry


def _tokenize(text: str) -> list[str]:
    """Divide texto en tokens significativos."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[-_/]", " ", text)
    tokens = re.findall(r"[a-zA-Záéíóúñ][a-zA-Záéíóúñ0-9]{1,}", text)
    stop_words = {
        "the",
        "is",
        "in",
        "at",
        "of",
        "to",
        "for",
        "on",
        "and",
        "or",
        "with",
        "from",
        "by",
        "as",
        "an",
        "que",
        "de",
        "en",
        "la",
        "el",
        "los",
        "las",
        "del",
        "para",
        "con",
        "por",
        "se",
        "su",
        "lo",
        "un",
        "una",
        "al",
        "como",
        "mas",
        "pero",
        "task",
        "tarea",
        "implement",
        "implementar",
    }
    result = []
    for t in tokens:
        t = t.lower().strip()
        if t not in stop_words and len(t) >= 2:
            result.append(t)
    return result


def index_sprint(chain_dir: str, index: TokenIndex) -> int:
    """Indexa las tareas de un sprint en el TokenIndex.

    Args:
        chain_dir: Ruta al directorio del sprint
        index: TokenIndex donde indexar

    Returns:
        Número de tareas indexadas
    """
    plan_path = os.path.join(chain_dir, "plan.json")
    if not os.path.exists(plan_path):
        return 0

    try:
        with open(plan_path) as f:
            plan = json.load(f)
    except Exception:
        return 0

    pl = plan.get("plan", plan)
    tasks = pl.get("tasks", [])
    sprint_id = os.path.basename(os.path.normpath(chain_dir))

    for t in tasks:
        tid = t.get("task_id", "?")
        fields_to_index = {
            "objective": t.get("objective", ""),
            "title": t.get("title", ""),
            "description": t.get("description", ""),
            "domain": t.get("domain", ""),
            "result_state": t.get("result_state", ""),
            "type": t.get("type", ""),
        }

        for field, value in fields_to_index.items():
            if not value:
                continue
            for token in _tokenize(value):
                index.add(
                    token,
                    IndexEntry(
                        source="task",
                        source_id=f"{sprint_id}:{tid}",
                        field=field,
                        snippet=value[:200],
                        score=1.0,
                    ),
                )

    return len(tasks)


def index_all(project_dir: str, index: TokenIndex) -> int:
    """Indexa todos los sprints de un proyecto.

    Args:
        project_dir: Directorio del proyecto o .cognitive/projects/<id>/
        index: TokenIndex donde indexar

    Returns:
        Número total de tareas indexadas
    """
    # Buscar directorio delta
    delta_dir = os.path.join(project_dir, "delta")
    if not os.path.isdir(delta_dir):
        # Podría ser .cognitive/projects/<id>/delta/
        cognitive_dir = os.path.join(project_dir, "..", "..")
        delta_dir = os.path.join(cognitive_dir, project_dir, "delta")
        if not os.path.isdir(delta_dir):
            delta_dir = os.path.join(os.path.dirname(project_dir), "delta")
            if not os.path.isdir(delta_dir):
                return 0

    total = 0
    for sprint_id in sorted(os.listdir(delta_dir)):
        sprint_dir = os.path.join(delta_dir, sprint_id)
        if os.path.isdir(sprint_dir):
            total += index_sprint(sprint_dir, index)

    return total
