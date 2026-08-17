"""
domain/contracts.py — Contract expression parser and evaluator.

SRP: Parse and evaluate edge contract expressions (pre/post/invariants).
     Independent from regex — uses a proper token-based parser.

Expression grammar:
  expression  = sub_state_key "." field "==" value
  value       = "'" string "'" | '"' string '"' | bool | number | ident
  sub_state_key = identifier ("." identifier)?  (e.g. "REVIEW" or "ANALYZE.GATHER")
  field       = identifier

Examples:
  REVIEW.decision == 'approve'
  CODE_REVIEW_SCAN.passed == true
  ANALYZE.GATHER.status == 'pending'
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .models import WorkflowState

logger = logging.getLogger(__name__)


@dataclass
class ContractExpression:
    """Parsed contract expression."""

    sub_key: str  # sub_state key (e.g. "REVIEW" or "ANALYZE.GATHER")
    field: str  # field within sub_state_data
    expected: str  # expected string value
    raw: str  # original expression text


class ContractParser:
    """Parser for contract expressions. Returns ContractExpression or None."""

    @staticmethod
    def parse(expression: str) -> ContractExpression | None:
        """Parse a contract expression.

        Returns ContractExpression if valid, None if expression is free text
        (not a parsable expression).

        Format: SUB.field == 'value' or NODE.SUB.field == 'value'
        """
        expr = expression.strip()
        if not expr:
            return None

        # Find the == operator
        eq_pos = expr.find("==")
        if eq_pos < 0:
            return None  # Free text, not an expression

        left = expr[:eq_pos].strip()
        right = expr[eq_pos + 2 :].strip()

        # Parse left side: SUB.field or NODE.SUB.field
        parts = left.split(".")
        if len(parts) == 2:
            sub_key, field = parts
        elif len(parts) == 3:
            # NODE.SUB.field → use SUB as sub_key
            _, sub_key, field = parts
        else:
            return None  # Invalid format

        # Parse right side: strip quotes if present
        expected = right
        if (
            len(expected) >= 2
            and expected[0] == expected[-1]
            and expected[0] in ("'", '"')
        ):
            expected = expected[1:-1]

        return ContractExpression(
            sub_key=sub_key,
            field=field,
            expected=expected,
            raw=expression,
        )


class ContractEvaluator:
    """Evaluates contract pre/post/invariants against WorkflowState.

    SRP: Single responsibility = check if contract conditions are met.
    """

    @staticmethod
    def evaluate_pre(state: WorkflowState, contract: dict) -> list[str]:
        """Evaluate contract.pre and invariants BEFORE transition.

        Soporta expresiones especiales:
          - ckg_has_decision('approve'): verifica que la decision existe en CKG
            (enforcement: el workflow NO avanza si la decision no se registro).
          - SUB.field == 'value': expresion estandar contra sub_state_data.

        Returns:
            List of error messages. Empty = contract OK, transition allowed.
        """
        errors: list[str] = []

        pre = contract.get("pre", "")
        if pre:
            stripped = pre.strip()
            if stripped.startswith("ckg_has_decision"):
                err = ContractEvaluator._check_ckg_decision(state, stripped)
                if err:
                    errors.append(f"contract.pre: {err}")
            else:
                parsed = ContractParser.parse(pre)
                if parsed is None:
                    logger.warning(
                        "contract.pre '%s' is not a valid expression — skipped", pre
                    )
                else:
                    err = ContractEvaluator._check(state, parsed)
                    if err:
                        errors.append(f"contract.pre: {err}")

        for inv in contract.get("invariants", []):
            parsed = ContractParser.parse(inv)
            if parsed is None:
                logger.warning(
                    "contract.invariant '%s' is not a valid expression — skipped", inv
                )
            else:
                err = ContractEvaluator._check(state, parsed)
                if err:
                    errors.append(f"contract.invariant: {err}")

        return errors

    @staticmethod
    def _check_ckg_decision(state: WorkflowState, expression: str) -> str | None:
        """Verifica que una decision quedo registrada en el CKG.

        Sintaxis: ckg_has_decision('approve')
        Consulta el CKG del proyecto para verificar que existe un nodo
        tipo decision con ese valor. Es el ENFORCEMENT estructural de la
        persistencia de decisiones.
        """
        import re

        m = re.search(r"ckg_has_decision\(\s*['\"]([^'\"]+)['\"]\s*\)", expression)
        if not m:
            return f"expresion '{expression}' invalida. Formato: ckg_has_decision('approve')"
        decision = m.group(1)

        # Resolver project_root desde chain_dir (contexto del estado)
        chain_dir = state.context.get("chain_dir", "")
        if not chain_dir:
            return f"no chain_dir para verificar decision '{decision}' en CKG"
        try:
            from core.engine.bootstrap import get_module_service

            project_svc = get_module_service("project_service")
            ckg_factory = get_module_service("ckg_service")
            if not project_svc or not ckg_factory:
                return f"no se pudo resolver CKG para {chain_dir} (sin composition root)"
            paths = project_svc.resolve_paths(chain_dir)
            ckg = ckg_factory(str(paths.project_root))
            # Filtrar por metadata.decision EN LA CONSULTA (no en resultados):
            # el search limita a 50 hits y las decisiones recientes quedarian
            # fuera si solo se filtra despues. Con el filtro estructurado el
            # indice devuelve exactamente las que matchean.
            results = (
                ckg.query({"type": "decision", "metadata.decision": decision})
                if hasattr(ckg, "query")
                else []
            )
            found = any(
                (n.get("metadata", {}).get("decision") == decision)
                or (n.get("label") == f"decision:{decision}")
                for n in results
            )
            if not found:
                return (
                    f"decision '{decision}' NO registrada en CKG. "
                    f"El workflow no avanza hasta que capture_decision la persista."
                )
            return None
        except Exception as e:
            return f"error verificando decision '{decision}' en CKG: {e}"

    @staticmethod
    def evaluate_post(state: WorkflowState, contract: dict) -> list[str]:
        """Evaluate contract.post BEFORE transition (as extra precondition).

        Although called 'post', it evaluates before the transition occurs,
        using the current state to predict post-conditions.
        """
        errors: list[str] = []
        post = contract.get("post", "")
        if post:
            parsed = ContractParser.parse(post)
            if parsed is None:
                logger.warning(
                    "contract.post '%s' is not a valid expression — skipped", post
                )
            else:
                err = ContractEvaluator._check(state, parsed)
                if err:
                    errors.append(f"contract.post: {err}")
        return errors

    @staticmethod
    def _check(state: WorkflowState, expr: ContractExpression) -> str | None:
        """Check a parsed expression against state. Returns error or None."""
        # Collect candidate keys in sub_state_data
        candidates = [expr.sub_key]
        if state.current_node:
            candidates.append(f"{state.current_node}.{expr.sub_key}")

        # Also search all keys ending with .sub_key (scoped to current node)
        for data_key in state.sub_state_data:
            if data_key.endswith(f".{expr.sub_key}"):
                # Only include if it belongs to current node
                if not state.current_node or data_key.startswith(
                    f"{state.current_node}."
                ):
                    candidates.append(data_key)

        # Try each candidate
        found_value = None
        for ck in candidates:
            ss_data = state.sub_state_data.get(ck)
            if ss_data is None:
                continue
            if hasattr(ss_data, "fields"):
                found_value = ss_data.fields.get(expr.field)
            elif isinstance(ss_data, dict):
                found_value = ss_data.get(expr.field) or ss_data.get("fields", {}).get(
                    expr.field
                )
            if found_value is not None:
                break

        if found_value is None:
            return f"field '{expr.field}' not found in '{expr.sub_key}' (tried: {', '.join(candidates)})"

        found_str = str(found_value).lower()
        expected_lower = expr.expected.lower()
        if found_str != expected_lower:
            return f"expected '{expr.field}' == '{expr.expected}', got '{found_value}'"

        return None
