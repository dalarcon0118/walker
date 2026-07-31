"""
infrastructure/parser.py — CLI argument parsing to structured objects.

SRP: Single responsibility = convert list[str] to typed fields objects.
Contains NO business logic. Does NOT execute commands.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedArgs:
    """Result of CLI argument parsing."""
    target: str = ""
    project_id: str = ""
    sprint_id: str = ""
    chain_dir: str = ""
    flags: dict[str, Any] = field(default_factory=dict)
    positional: list[str] = field(default_factory=list)
    raw_args: list[str] = field(default_factory=list)


def _try_parse_json(val: str) -> Any:
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val


def parse_flags(args: list[str]) -> dict[str, Any]:
    """Convert --key value style args to dict.

    Example: ['--goal', 'test', '--tasks', '[{"x":1}]'] -> {'goal': 'test', 'tasks': [{'x': 1}]}
    """
    flags: dict[str, Any] = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            i += 1
            if i < len(args) and not args[i].startswith("--"):
                flags[key] = _try_parse_json(args[i])
                i += 1
            else:
                flags[key] = True
        else:
            i += 1
    return flags


def parse(target: str, args: list[str]) -> ParsedArgs:
    """Parse full CLI arguments into structured ParsedArgs.

    Args:
        target: command (status, submit, transition, etc.)
        args: CLI arguments (without the command)
    """
    result = ParsedArgs(target=target, raw_args=args)

    if not args:
        return result

    first = args[0]
    if first.startswith("--"):
        result.flags = parse_flags(args)
        return result

    # First argument may be project_id
    result.project_id = first

    if len(args) >= 2:
        # Second argument may be sprint_id
        second = args[1]
        if not second.startswith("--"):
            result.sprint_id = second
            # Flags from third argument onward
            if len(args) > 2:
                result.flags = parse_flags(args[2:])
        else:
            result.flags = parse_flags(args[1:])

    return result
