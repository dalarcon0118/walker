"""
migrate — (vaciado)

Este módulo contenía workflows legacy hardcoded (v4, emergency, presprint)
y auto-migración de workflow.json. Esos fallbacks eran errores silenciosos
que escondían el problema real: el motor no tenía workflow canónico.

SSOT actual: la plantilla workflow.v3 del motor (get_engine_workflow_dir).
No existe fallback hardcoded. Si falta, el caller debe ERROR.
"""
