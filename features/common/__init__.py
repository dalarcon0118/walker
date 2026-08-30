"""features/common — PathModule / CommonModule (ADR-WALKER-068 Fase 1: declaración).

RESPONSABILIDAD ÚNICA: utilidades globales del host con dependencia de config
— PathResolver (SSOT identificadores→rutas). Equivalente a CoreModule/SharedModule
de NestJS (@Global).
FASE 1 (declaración): el código aún vive en `app/path_resolver.py`; el move se
hace en Fase 4.
"""