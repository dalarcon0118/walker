"""
infrastructure/repositories.py — Persistencia de plan.json.

PRINCIPIO estructural: el estado de negocio del spring (goal, status,
current_spec_index, task_id, specs con estado) vive en el DELTA
(SpringGraphService — DeltaGraph). plan.json es la PROYECCION derivada
del delta (regenerada por sync_plan_projection). sprint-state.json fue
ELIMINADO como archivo de estado.
"""
from __future__ import annotations

import json
import os


class PlanRepository:
    """Persistencia de plan.json.

    SRP: Unica responsabilidad = leer/escribir plan.json.
    """

    def load(self, chain_dir: str) -> dict:
        path = os.path.join(chain_dir, "plan.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"plan.json not found in {chain_dir}")
        with open(path) as f:
            return json.load(f)

    def save(self, chain_dir: str, plan: dict) -> None:
        path = os.path.join(chain_dir, "plan.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

    def exists(self, chain_dir: str) -> bool:
        return os.path.exists(os.path.join(chain_dir, "plan.json"))
