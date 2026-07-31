"""cli — Módulo CLI del Walker v3.

Separado de walker_v3.py para evitar el god object.
Cada archivo tiene una responsabilidad única:
  - utils.py:    Utilidades compartidas (_safe, _output, _get_api)
  - project.py:  Descubrimiento de proyectos/sprints y dashboards
  - tracing.py:  Handlers de hooks (register_file, register_message, trace_log)
  - prompt.py:   Texto de ayuda para el agente (_AGENT_PROMPT)
  - commands.py: Handlers CLI de workflow, sesión y tareas
  - router.py:   Routing/dispatch: main(), _resolve_path, COMMANDS, agent routing
"""
