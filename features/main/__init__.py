"""features/main — Composition root final de Walker.

Este módulo es el ÚNICO que conoce todos los módulos de la aplicación
y arma el grafo completo de dependencias:

- core/engine: infraestructura de workflow
- delta_tracking: modelo de desarrollo
- code_analysis: análisis de código
- cognitive_knowledge: conocimiento
- event_tracing: trazabilidad
- project: gestión de proyectos

Entry points (CLI, MCP, WorkflowAPI) consumen esta composición.
"""

from features.main.container import AppContainer
from features.main.front_controller import FrontController
from features.main.app import WalkerApp

__all__ = ["AppContainer", "WalkerApp", "FrontController"]
