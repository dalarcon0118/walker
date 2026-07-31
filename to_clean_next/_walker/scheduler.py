"""
_walker/scheduler.py — Job Scheduler con DAG-based parallelism.

DDD: Modelos puros (dataclasses) + servicios (stateless).
SRP: Cada clase tiene UNA responsabilidad.
SSOT: Lee depends_on de plan.json como fuente unica de dependencias.

Uso:
    from _walker.scheduler import DAGBuilder, JobScheduler, ParallelExecutor

    dag = DAGBuilder.build(tasks)
    ready = JobScheduler.get_ready_tasks(dag)
    results = ParallelExecutor.execute(ready, runner_func)

Arquitectura:
    DAGBuilder       → Construye DAG desde lista de tareas (plana)
    JobScheduler     → Orquesta: build_dag() → get_ready_tasks() → execute()
    ParallelExecutor → Ejecuta tareas ready en paralelo (ThreadPoolExecutor)
"""

from __future__ import annotations

import os as _os
import subprocess as _subprocess
import time as _time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable


# ═══════════════════════════════════════════════════════════════════════════
# Value Objects
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TaskNode:
    """Nodo del DAG: una tarea con sus dependencias.

    Value Object — inmutable, sin identidad propia.
        La identidad es task_id.
    """

    task_id: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    objective: str = ""
    domain: str = ""


@dataclass(frozen=True)
class DAG:
    """Directed Acyclic Graph de tareas.

    Value Object — una vez construido es inmutable.
    Contiene el adjacency list y el reverse index para consultas eficientes.
    """

    nodes: dict[str, TaskNode] = field(default_factory=dict)
    adjacency: dict[str, list[str]] = field(default_factory=dict)
    reverse_adj: dict[str, list[str]] = field(default_factory=dict)
    topological_order: tuple[str, ...] = field(default_factory=tuple)
    has_cycle: bool = False
    cycle_path: tuple[str, ...] = field(default_factory=tuple)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(deps) for deps in self.adjacency.values())


@dataclass(frozen=True)
class ExecutionResult:
    """Resultado de ejecutar una tarea."""

    task_id: str
    success: bool
    error: str | None = None
    output: str | None = None
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class ScheduleReport:
    """Reporte completo de una ejecucion del scheduler."""

    dag: DAG
    results: tuple[ExecutionResult, ...] = field(default_factory=tuple)
    total_tasks: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    blocked: int = 0
    duration_seconds: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Entity: JobScheduler (agreggate root del scheduling)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class JobScheduler:
    """Orquesta el scheduling de tareas.

    Entity — tiene estado mutable (current state de la ejecucion).
    SRP: SOLO orquesta. La construccion del DAG y la ejecucion paralela
    son responsabilidades de DAGBuilder y ParallelExecutor respectivamente.
    """

    dag: DAG | None = None
    task_states: dict[str, str] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None

    TASK_PENDING = "pending"
    TASK_READY = "ready"
    TASK_RUNNING = "running"
    TASK_COMPLETED = "completed"
    TASK_FAILED = "failed"
    TASK_BLOCKED = "blocked"

    def initialize(self, tasks: list[dict]) -> DAG:
        """Construye el DAG desde la lista de tareas e inicializa estados.

        Args:
            tasks: Lista de dicts de tarea (desde plan.json),
                   cada una con task_id y depends_on.

        Returns:
            DAG construido.
        """
        self.dag = DAGBuilder.build(tasks)
        self.task_states = {}
        for tid in self.dag.nodes:
            self.task_states[tid] = self.TASK_PENDING
        return self.dag

    def get_ready_tasks(self) -> list[str]:
        """Retorna tareas listas para ejecutar (dependencias cumplidas).

        READ-ONLY: no modifica estados. Usa update_blocked() para
        marcar tareas bloqueadas por dependencias fallidas.

        Returns:
            Lista de task_ids listos para ejecutar.
        """
        if not self.dag:
            return []
        ready = []
        for tid in self.task_states:
            if self.task_states[tid] != self.TASK_PENDING:
                continue
            node = self.dag.nodes.get(tid)
            if not node:
                continue
            all_deps_met = True
            for dep in node.depends_on:
                dep_state = self.task_states.get(dep, self.TASK_BLOCKED)
                if dep_state != self.TASK_COMPLETED:
                    all_deps_met = False
                    break
            if all_deps_met:
                ready.append(tid)
        return ready

    def update_blocked(self) -> None:
        """Marca como bloqueadas las tareas con dependencias fallidas.

        Separado de get_ready_tasks() para mantener SRP:
        - get_ready_tasks() solo LEE
        - update_blocked() solo MUTA
        """
        if not self.dag:
            return
        for tid in self.task_states:
            if self.task_states[tid] != self.TASK_PENDING:
                continue
            node = self.dag.nodes.get(tid)
            if not node:
                continue
            for dep in node.depends_on:
                if self.task_states.get(dep) == self.TASK_FAILED:
                    self.task_states[tid] = self.TASK_BLOCKED
                    break

    def mark_running(self, task_ids: list[str]) -> None:
        """Marca tareas como 'running' antes de ejecutarlas."""
        for tid in task_ids:
            if tid in self.task_states:
                self.task_states[tid] = self.TASK_RUNNING

    def mark_completed(self, task_id: str) -> None:
        """Marca una tarea como completada exitosamente."""
        if task_id in self.task_states:
            self.task_states[task_id] = self.TASK_COMPLETED

    def mark_failed(self, task_id: str) -> None:
        """Marca una tarea como fallida."""
        if task_id in self.task_states:
            self.task_states[task_id] = self.TASK_FAILED

    def get_blocked_tasks(self) -> list[str]:
        """Retorna tareas bloqueadas (dependencia fallida)."""
        return [
            tid for tid, state in self.task_states.items() if state == self.TASK_BLOCKED
        ]

    def get_status_summary(self) -> dict[str, int]:
        """Resumen de estados."""
        summary: dict[str, int] = defaultdict(int)
        for state in self.task_states.values():
            summary[state] += 1
        return dict(summary)


# ═══════════════════════════════════════════════════════════════════════════
# Domain Service: DAGBuilder (puro, sin IO)
# ═══════════════════════════════════════════════════════════════════════════


class DAGBuilder:
    """Construye un DAG desde una lista plana de tareas con depends_on.

    Domain Service — puro, sin efectos secundarios.
    SRP: Unica responsabilidad = construir DAGs validos.

    Detecta ciclos usando Kahn's algorithm (BFS topological sort).
    """

    @staticmethod
    def build(tasks: list[dict]) -> DAG:
        """Construye DAG desde lista de tareas.

        Args:
            tasks: Lista de dicts con task_id y depends_on.
                   Ej: [{"task_id": "TASK-001", "depends_on": []},
                        {"task_id": "TASK-002", "depends_on": ["TASK-001"]}]

        Returns:
            DAG con topological_order, has_cycle detectado.
        """
        # 1. Construir nodos
        nodes: dict[str, TaskNode] = {}
        for t in tasks:
            tid = t.get("task_id", "")
            if not tid:
                continue
            deps = tuple(t.get("depends_on", []) or [])
            nodes[tid] = TaskNode(
                task_id=tid,
                depends_on=deps,
                objective=t.get("objective", ""),
                domain=t.get("domain", ""),
            )

        # 2. Construir adjacency list (dirigido: dependency -> dependent)
        #    Si A depende de B: B -> A (B debe completarse antes que A)
        adj: dict[str, list[str]] = {tid: [] for tid in nodes}
        rev_adj: dict[str, list[str]] = {tid: [] for tid in nodes}
        for tid, node in nodes.items():
            for dep in node.depends_on:
                if dep in nodes:
                    adj[dep].append(tid)  # dep -> tid
                    rev_adj[tid].append(dep)  # tid depende de dep

        # 3. Topological sort (Kahn's algorithm) con deteccion de ciclos
        queue: deque[str] = deque()
        in_degree: dict[str, int] = {}

        for tid in nodes:
            # Solo considerar dependencias que existen en el grafo
            actual_deps = [d for d in nodes[tid].depends_on if d in nodes]
            in_degree[tid] = len(actual_deps)
            if in_degree[tid] == 0:
                queue.append(tid)

        topo_order: list[str] = []
        while queue:
            current = queue.popleft()
            topo_order.append(current)
            for neighbor in adj.get(current, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        has_cycle = len(topo_order) < len(nodes)
        cycle_path: tuple[str, ...] = ()

        if has_cycle:
            # Encontrar nodos en el ciclo (los que no estan en topo_order)
            cycle_nodes = set(nodes.keys()) - set(topo_order)
            cycle_path = tuple(cycle_nodes)

        return DAG(
            nodes=nodes,
            adjacency=adj,
            reverse_adj=rev_adj,
            topological_order=tuple(topo_order),
            has_cycle=has_cycle,
            cycle_path=cycle_path,
        )

    @staticmethod
    def find_cycle(tasks: list[dict]) -> list[str]:
        """Detecta y retorna el ciclo en las dependencias (si existe).

        Args:
            tasks: Lista de tareas con depends_on.

        Returns:
            Lista vacia si no hay ciclo, o los task_ids en el ciclo.
        """
        dag = DAGBuilder.build(tasks)
        return list(dag.cycle_path)


# ═══════════════════════════════════════════════════════════════════════════
# Domain Service: ParallelExecutor (puro, sin IO en construccion)
# ═══════════════════════════════════════════════════════════════════════════


class ParallelExecutor:
    """Ejecuta tareas ready en paralelo usando ThreadPoolExecutor.

    Domain Service — depende de un callable runner para la ejecucion real.
    SRP: Unica responsabilidad = ejecutar N tareas en paralelo y reportar.

    El runner_func recibe (task_id: str) -> ExecutionResult.
    Ejemplo:
        def my_runner(task_id: str) -> ExecutionResult:
            try:
                result = subprocess.run([...], capture_output=True)
                return ExecutionResult(task_id, success=True, output=result.stdout)
            except Exception as e:
                return ExecutionResult(task_id, success=False, error=str(e))
    """

    DEFAULT_MAX_WORKERS = 4
    DEFAULT_TIMEOUT = 300  # 5 min por tarea

    @staticmethod
    def execute(
        task_ids: list[str],
        runner_func: Callable[[str], ExecutionResult],
        max_workers: int = DEFAULT_MAX_WORKERS,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> list[ExecutionResult]:
        """Ejecuta tareas en paralelo.

        Args:
            task_ids: Lista de task_ids a ejecutar.
            runner_func: Callable que ejecuta UNA tarea.
            max_workers: Maximo de workers paralelos.
            timeout: Timeout total en segundos.

        Returns:
            Lista de ExecutionResult (1 por tarea).
        """
        if not task_ids:
            return []

        results: list[ExecutionResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(runner_func, tid): tid for tid in task_ids}
            for future in as_completed(future_map, timeout=timeout):
                tid = future_map[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append(
                        ExecutionResult(
                            task_id=tid,
                            success=False,
                            error=str(e),
                        )
                    )
        return results

    @staticmethod
    def execute_batches(
        scheduler: JobScheduler,
        runner_func: Callable[[str], ExecutionResult],
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> ScheduleReport:
        """Ejecuta el scheduler por batches (ready tasks en cada ronda).

        Algoritmo:
            1. get_ready_tasks() → tareas listas
            2. execute_parallel() sobre las ready
            3. Actualizar estados (completed/failed)
            4. Repetir hasta que no queden ready o todas fallen

        Args:
            scheduler: JobScheduler inicializado con DAG y estados.
            runner_func: Callable que ejecuta UNA tarea.

        Returns:
            ScheduleReport con resultados completos.
        """
        start = _time.time()

        all_results: list[ExecutionResult] = []

        while True:
            ready = scheduler.get_ready_tasks()
            if not ready:
                break

            scheduler.mark_running(ready)
            batch_results = ParallelExecutor.execute(ready, runner_func, max_workers)

            for result in batch_results:
                all_results.append(result)
                if result.success:
                    scheduler.mark_completed(result.task_id)
                else:
                    scheduler.mark_failed(result.task_id)

            # Actualizar tareas bloqueadas por dependencias fallidas
            scheduler.update_blocked()

        elapsed = _time.time() - start

        completed = sum(1 for r in all_results if r.success)
        failed = sum(1 for r in all_results if not r.success)

        return ScheduleReport(
            dag=scheduler.dag if scheduler.dag else DAG(),
            results=tuple(all_results),
            total_tasks=len(scheduler.task_states) if scheduler.dag else 0,
            completed=completed,
            failed=failed,
            skipped=0,
            blocked=len(scheduler.get_blocked_tasks()),
            duration_seconds=elapsed,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Factory: TaskRunner (helper para crear runner_func desde plan.json)
# ═══════════════════════════════════════════════════════════════════════════


class ScriptTaskRunner:
    """Runner que ejecuta tareas via subprocess (scripts configurados).

    Ejemplo:
        runner = ScriptTaskRunner({
            "TASK-001": "python3 scripts/task.py advance <chain> TASK-001",
            "TASK-002": "python3 scripts/task.py advance <chain> TASK-002",
        })
        result = runner.run("TASK-001")
    """

    def __init__(
        self,
        commands: dict[str, str] | None = None,
        chain_dir: str | None = None,
        default_timeout: int = 300,
    ):
        self.commands: dict[str, str] = dict(commands or {})
        self.chain_dir = chain_dir
        self.default_timeout = default_timeout
        # Usar find_project_root para resolver project root (robusto)
        self._project_root = (
            self._resolve_project_root(chain_dir) if chain_dir else None
        )

    @staticmethod
    def _resolve_project_root(chain_dir: str) -> str | None:
        """Resuelve el project root desde chain_dir, buscando .cognitive/."""
        cwd = _os.path.abspath(chain_dir)
        for _ in range(10):
            if _os.path.exists(_os.path.join(cwd, ".cognitive")):
                return cwd
            candidate = _os.path.join(cwd, "..", ".cognitive")
            if _os.path.exists(candidate):
                return _os.path.normpath(_os.path.join(cwd, ".."))
            parent = _os.path.dirname(cwd)
            if parent == cwd:
                break
            cwd = parent
        return _os.path.normpath(_os.path.join(chain_dir, "..", "..", "..", ".."))

    def add_command(self, task_id: str, command: str) -> None:
        """Registra un comando para una tarea."""
        self.commands[task_id] = command

    def run(self, task_id: str) -> ExecutionResult:
        """Ejecuta el comando asociado a una tarea.

        Si no hay comando registrado, retorna exito (no-op) para
        permitir tareas que no requieren ejecucion (solo planificacion).
        """
        command = self.commands.get(task_id)
        if not command:
            return ExecutionResult(
                task_id=task_id,
                success=True,
                output="(no command configured — no-op)",
            )

        start = _time.time()
        try:
            cmd = command
            if self.chain_dir:
                cmd = cmd.replace("<chain>", self.chain_dir)
            result = _subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.default_timeout,
                cwd=self._project_root,
            )
            elapsed = _time.time() - start
            return ExecutionResult(
                task_id=task_id,
                success=result.returncode == 0,
                error=result.stderr[:500] if result.returncode != 0 else None,
                output=result.stdout[:500] if result.stdout else None,
                duration_seconds=elapsed,
            )
        except Exception as e:
            elapsed = _time.time() - start
            return ExecutionResult(
                task_id=task_id,
                success=False,
                error=str(e),
                duration_seconds=elapsed,
            )
