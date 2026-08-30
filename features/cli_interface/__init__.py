"""features/cli_interface — WorkflowApiModule (ADR-WALKER-068 Fase 1: declaración).

RESPONSABILIDAD ÚNICA: API surface del workflow para los adaptadores (CLI/MCP)
— facade submit/transition/status/prepare + gates host-side + context_resolver
(adapter del puerto IContextValidator del engine).
FASE 1 (declaración): el código aún vive en `app/api/workflow_facade.py` +
`app/context_resolver.py`; el move se hace en Fase 3.
"""