"""Verification tasks: verify_predicates.

Wrappers de workflow sobre el motor real (infrastructure/verify_engine.py).
El motor verifica predicates contra código real con cache de sesión.
Recibe un input TIPADO y VALIDADO (VerifyInput).
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any
from shared.common.shared_domain import ExecutionContext, TaskResult
from shared.common.shared_domain.validated_vo import TaskInput

from features.main.task_execution.infrastructure.verify_engine import (
    invalidate_verify_cache,
    verify_predicates_batch,
)


@dataclass(frozen=True)
class VerifyInput(TaskInput):
    """Input validado de verify_predicates.

    predicates: lista de strings o dicts tipados (o data["predicate"] single).
    project_root: ruta del proyecto (override del chain_dir).
    fresh: True → limpiar cache de verificación.
    """

    predicates: list | None = None
    predicate: Any = None
    project_root: str = ""
    fresh: bool = False

    def _validate(self) -> str:
        if self.predicates is None and self.predicate is None:
            return "se requiere 'predicates' o 'predicate'"
        if self.predicates is not None and not isinstance(self.predicates, list):
            return "predicates debe ser una lista"
        return ""

    def normalized_predicates(self) -> list:
        """Aplana a la forma que espera el motor.

        Acepta:
          - predicates: lista de strings o dicts tipados
          - predicate: single string/dict
          - items loop_verification: {"predicate": ..., "type": ...}
            → extrae el campo "predicate"
        """
        raw = self.predicates
        if raw is None:
            raw = [self.predicate] if self.predicate is not None else []

        predicates: list = []
        for item in raw:
            if isinstance(item, dict) and "predicate" in item:
                predicates.append(item["predicate"])
            else:
                predicates.append(item)
        return predicates


def _translate_predicates(predicates: list) -> list:
    """Traduce predicates declarativos (spec) a los TIPOS verificables del motor.

    ANTI-ALUCINACIÓN (G-W6): los predicates de una spec son strings en
    lenguaje natural (ej: "Nivel 1 MCP Tools: test_mcp_tool_status_e2e, ...").
    El motor NO sabe interpretar un párrafo como símbolo — verificaría el
    párrafo completo como símbolo inexistente. Este traductor los convierte
    a dicts typed antes de la verificación:

      - Dict typed (symbol_exists/test_pass/command_exit/...) → tal cual.
      - String que ES un comando shell → tal cual (el motor lo detecta).
      - String con nombre de método test_x / test_x_bdd → symbol_exists
        sobre el nombre (verifica que el test EXISTE en el código).
      - String con "pytest ..." → test_pass ejecutable.
      - Párrafo con nombres de tests (SPEC-003) → descompone en múltiples
        symbol_exists, uno por nombre.
      - Sin patrón verificable → dict {"type": "manual"} (auditable, se
        revela como verificación manual — nunca éxito vacío).
    """
    translated: list = []
    for p in predicates:
        if isinstance(p, dict):
            translated.append(p)
            continue
        if not isinstance(p, str):
            # Fallback honesto: no verificable de forma automática.
            translated.append({"type": "manual", "description": str(p)})
            continue

        text = p.strip()
        if not text:
            continue
        from features.main.task_execution.infrastructure.verify_engine import (
            _is_string_a_shell_command,
        )

        # 1. Comando shell (pytest, python3, grep...) → el motor lo ejecuta.
        if _is_string_a_shell_command(text):
            translated.append(text)
            continue

        # 2. Párrafo con nombres de tests → uno por nombre (SPEC-003 style).
        test_names = re.findall(r"\btest_[a-zA-Z0-9_]+\b", text)
        if len(test_names) > 1 or (text.startswith("test_") and test_names):
            for name in dict.fromkeys(test_names):  # dedupe preservando orden
                translated.append({"type": "symbol_exists", "symbol": name})
            continue

        # 3. Single test name o símbolo simple → symbol_exists.
        if test_names:
            translated.append(
                {"type": "symbol_exists", "symbol": test_names[0]}
            )
            continue

        # 4. Sin patrón verificable → manual explícito (auditable).
        translated.append({"type": "manual", "description": text})

    return translated


def verify_predicates(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """VERIFY.PREDICATE_CHECK — Verifica DoD predicates contra código real."""
    # Input validado: la lógica de negocio recibe una entidad limpia
    if data.get("_fresh"):
        invalidate_verify_cache()

    input_result = VerifyInput.from_dict(data)
    if not input_result.ok:
        # ANTI-ALUCINACIÓN (G-W6): un gate de verificación SIN predicates no
        # puede declarar éxito. Antes esto era un no-op "all_passed=True"
        # (verification.py) que permitía pasar el gate VERIFY.PREDICATE_CHECK
        # vacío: el agente podía completar una task sin que nada se verificara.
        # Ahora falla con error explícito → on_failure del gate (re-trabajo).
        if data.get("predicates") is None and data.get("predicate") is None:
            return TaskResult.failure(
                "verify_predicates: no predicates provided — "
                "el gate no puede validar nada. Inyecta task.validation.predicates "
                "desde la spec (DeltaGraph) o define predicates en la spec."
            )
        return TaskResult.failure(input_result.error or "VerifyInput inválido")
    inp: VerifyInput = input_result.data

    if inp.fresh:
        invalidate_verify_cache()

    predicates = inp.normalized_predicates()
    project_root = inp.project_root or ctx.chain_dir or ""
    if not project_root:
        return TaskResult.failure("project_root/chain_dir no disponible")

    if not predicates:
        return TaskResult.failure(
            "verify_predicates: lista de predicates vacía — "
            "imposible verificar un DoD sin criterios."
        )

    # Traductor spec→motor (anti-alucinación): los predicates declarativos
    # (strings de la spec, ej: "Nivel 1: test_x, test_y") no son símbolos
    # individuales; se convierten a dicts typed verificables por el motor.
    predicates = _translate_predicates(predicates)

    results = verify_predicates_batch(predicates, project_root)

    detailed = []
    failed = 0
    for pred, (passed, info) in zip(predicates, results):
        if not passed:
            failed += 1
        detailed.append({
            "predicate": info.get("label") or (pred if isinstance(pred, str) else str(pred)),
            "method": info.get("method", "unknown"),
            "passed": passed,
            "detail": info,
        })

    return TaskResult.success(data={
        "task_id": inp.task_id or ctx.task_id,
        "all_passed": failed == 0,
        "results": detailed,
        "failed_count": failed,
        "passed_count": len(detailed) - failed,
        "total": len(detailed),
        "project_root": project_root,
    })


def register(registry: Any) -> None:
    # Accion del workflow VERIFY.PREDICATE_CHECK — interna
    registry.register("verify_predicates", verify_predicates,
                      metadata={"description": "Verify DoD predicates against code", "tags": ["verification"], "scope": "internal"})
