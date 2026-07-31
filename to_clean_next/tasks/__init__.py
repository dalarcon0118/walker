"""tasks/ — Task handlers for workflow.v3.

Each module exports a `register(registry)` function that registers
its task handlers with a TaskRegistry instance.

Usage:
    from tasks import planning, gathering, implementation
    planning.register(registry)
    gathering.register(registry)
"""
