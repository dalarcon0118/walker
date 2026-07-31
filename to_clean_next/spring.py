#!/usr/bin/env python3
"""spring.py — Sprint lifecycle commands (dispatch).

Extraido a spring/ module. Este archivo es solo el entry point.
"""
import sys
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from spring import (
    cmd_create, cmd_close, cmd_status, cmd_health, cmd_list,
    cmd_add_task, cmd_update_task, cmd_update_goal, cmd_bulk_update,
    cmd_graph, cmd_reopen, cmd_blockers, cmd_diff, cmd_rebase, cmd_remove,
    cmd_validate_plan, eprint,
)
from spring.approve import cmd_approve_task

def main():
    if len(sys.argv) < 2:
        eprint("Uso: spring.py <comando> [args...]")
        return 2
    sub = sys.argv[1]
    sub_args = sys.argv[2:]
    dispatch = {
        "create": cmd_create, "close": cmd_close, "status": cmd_status,
        "health": cmd_health, "list": cmd_list,
        "add-task": cmd_add_task, "update-task": cmd_update_task,
        "update-goal": cmd_update_goal, "bulk-update": cmd_bulk_update,
        "graph": cmd_graph, "reopen": cmd_reopen,
        "blockers": cmd_blockers, "diff": cmd_diff, "rebase": cmd_rebase,
        "remove": cmd_remove, "validate-plan": cmd_validate_plan,
        "approve-task": cmd_approve_task,
    }
    if sub not in dispatch:
        eprint(f"Comando desconocido: {sub}")
        return 2
    return dispatch[sub](sub_args)

if __name__ == "__main__":
    sys.exit(main())
