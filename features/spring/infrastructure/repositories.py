"""
infrastructure/repositories.py — Persistencia de SprintState.

SprintStateRepository extiende WorkflowStateRepository (engine)
agregando manejo de los campos de negocio del sprint.

El archivo en disco (sprint-state.json) contiene tanto los campos
del workflow como los de spring en un solo JSON. SprintStateRepository
es el unico que sabe leer/escribir el formato completo.
"""
from __future__ import annotations

import json
import os

from features.engine.domain.models import WorkflowState
from ..domain.models import SprintState


class SprintStateRepository:
    """Persistencia de SprintState (workflow + spring data).

    Lee/escribe sprint-state.json con todos los campos.
    Usa WorkflowStateRepository internamente para la parte del workflow.
    """

    STATE_FILE = "sprint-state.json"

    def load(self, chain_dir: str) -> SprintState:
        """Carga SprintState completo desde el archivo."""
        path = os.path.join(chain_dir, self.STATE_FILE)
        if not os.path.exists(path):
            return SprintState()

        with open(path) as f:
            raw = json.load(f)
        return SprintState.from_dict(raw)

    def load_dict(self, chain_dir: str) -> dict:
        """Carga sprint-state como dict plano (compatibilidad con legacy).

        Util para consumidores read-only que trabajan con dicts en vez de
        SprintState objects. Retorna dict vacio si no existe el archivo.
        """
        path = os.path.join(chain_dir, self.STATE_FILE)
        if not os.path.exists(path):
            return {}
        with open(path) as f:
            return json.load(f)

    def save(self, chain_dir: str, state: SprintState | WorkflowState) -> None:
        """Persiste SprintState a archivo y sincroniza plan.json."""
        path = os.path.join(chain_dir, self.STATE_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Si es WorkflowState puro, convertirlo a dict con solo campos engine
        if isinstance(state, WorkflowState) and not isinstance(state, SprintState):
            data = state.to_dict()
        else:
            data = state.to_dict()

        self._write(path, data)

    def save_context(self, chain_dir: str, context_updates: dict) -> None:
        """Actualiza el context del sprint-state (API PUBLICA del store).

        SSOT: Este metodo es el UNICO punto de escritura del context de
        sprint-state.json desde fuera del repository. Evita que los
        llamadores usen _write() directamente (privado).

        Args:
            chain_dir: Directorio del sprint.
            context_updates: Dict con las keys a mergear en context.
        """
        state = self.load_dict(chain_dir)
        state.setdefault("context", {}).update(context_updates)
        self._write(os.path.join(chain_dir, self.STATE_FILE), state)

    def save_state_dict(self, chain_dir: str, state: dict) -> None:
        """Persiste un dict completo de estado (API PUBLICA del store).

        SSOT: Punto de escritura de estado completo (creacion de sprint,
        cierre, updates de campos de negocio del sprint). Los llamadores
        de negocio (spring use-cases) NO usan _write() directamente.

        Args:
            chain_dir: Directorio del sprint.
            state: Dict completo del estado a persistir.
        """
        self._write(os.path.join(chain_dir, self.STATE_FILE), state)

    def load_workflow(self, chain_dir: str) -> WorkflowState:
        """Carga solo la parte del workflow (sin datos de spring).

        Retro-compatible: si el archivo tiene campos extra de spring,
        WorkflowState.from_dict() los ignora.
        """
        path = os.path.join(chain_dir, self.STATE_FILE)
        if not os.path.exists(path):
            return WorkflowState()
        with open(path) as f:
            raw = json.load(f)
        return WorkflowState.from_dict(raw)

    def _write(self, path: str, data: dict) -> None:
        """Escritura + sync plan.json."""
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # ── Sync plan.json con task statuses ──────────────────────────
        chain_dir = os.path.dirname(path)
        plan_path = os.path.join(chain_dir, "plan.json")
        if os.path.exists(plan_path):
            try:
                with open(plan_path) as f:
                    plan = json.load(f)
                plan_tasks = (
                    plan.get("tasks")
                    if isinstance(plan.get("tasks"), list)
                    else plan.get("plan", {}).get("tasks", [])
                )
                state_tasks = data.get("tasks", [])
                state_status = {}
                if isinstance(state_tasks, list):
                    for st in state_tasks:
                        tid = st.get("task_id", "") or st.get("id", "")
                        if tid:
                            state_status[tid] = st.get("status", "pending")
                elif isinstance(state_tasks, dict):
                    for tid, st in state_tasks.items():
                        state_status[tid] = (
                            st.get("status", "pending")
                            if isinstance(st, dict)
                            else "pending"
                        )
                if plan_tasks and state_status:
                    for task in plan_tasks:
                        tid = task.get("task_id", "")
                        if tid in state_status:
                            task["status"] = state_status[tid]
                    with open(plan_path, "w") as f:
                        json.dump(plan, f, indent=2, ensure_ascii=False)
            except Exception:
                pass




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
