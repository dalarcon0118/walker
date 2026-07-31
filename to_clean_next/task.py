#!/usr/bin/env python3
"""task.py — Delta skill task commands (dispatch).

Los comandos han sido extraidos al modulo task/ para SRP.
Este archivo es solo el entry point.
"""
import sys
from task import (
    cmd_advance, cmd_implement, cmd_approve,
    cmd_research, cmd_verify,
    cmd_check_readiness, cmd_check_context_gaps, cmd_check_analysis,
    cmd_score, cmd_qlty_check,
    eprint,
)

def main():
    if len(sys.argv) < 2:
        eprint("Uso: task.py <comando> [args...]")
        return 2
    sub = sys.argv[1]
    sub_args = sys.argv[2:]
    dispatch = {
        "advance": cmd_advance, "implement": cmd_implement, "approve": cmd_approve,
        "research": cmd_research, "verify": cmd_verify, "score": cmd_score,
        "qlty-check": cmd_qlty_check, "check-readiness": cmd_check_readiness,
        "check-context-gaps": cmd_check_context_gaps, "check-analysis": cmd_check_analysis,
    }
    if sub not in dispatch:
        eprint(f"Comando desconocido: {sub}")
        return 2
    return dispatch[sub](sub_args)

if __name__ == "__main__":
    sys.exit(main())
