"""delta_tracking/tasks — Shim de backward-compat (ADR-051 G2).

Las tasks del sprint viven en features/main/task_execution/tasks/
(ADR-050 §3: el task layer es de main, no del feature). Este paquete solo
re-exporta los símbolos legacy para no romper imports de tests/consumidores
antiguos (migración incremental, nunca big-bang).
"""
