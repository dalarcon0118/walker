# language: es
# tests/features/trace_channel.feature
#
# Feature: Canal de invocación en task_execute (SPEC-002 G-W1).
#
# Como mantenedor del walker
# Quiero que cada evento task_execute del TraceGraph lleve el canal de
# invocación (cli | mcp | workflow)
# Para saber desde dónde se ejecutó cada task (agente CLI, bridge MCP o el
# motor interno del workflow) sin tener que inferirlo del contexto.

Característica: Canal de invocación en task_execute
  Como mantenedor del walker
  Quiero que task_execute registre channel=cli|mcp|workflow
  Para que el TraceGraph sepa el origen de cada ejecución de task

  @trace-channel
  Escenario: Task ejecutada desde MCP lleva channel mcp
    Dado un TaskRegistry con un tracer y una task "ping" registrada
    Cuando ejecuto la task "ping" desde el canal mcp
    Entonces se emite un evento task_execute
    Y el evento task_execute lleva task "ping" y ok true
    Y el evento task_execute lleva channel "mcp"
    Y el evento task_execute lleva source "walker" y duration_ms

  @trace-channel
  Escenario: Task ejecutada desde el CLI lleva channel cli
    Dado un TaskRegistry con un tracer y una task "ping" registrada
    Cuando ejecuto la task "ping" desde el canal cli
    Entonces se emite un evento task_execute
    Y el evento task_execute lleva channel "cli"

  @trace-channel
  Escenario: Task ejecutada por el workflow engine lleva channel workflow
    Dado un TaskRegistry con un tracer y una task "ping" registrada
    Cuando ejecuto la task "ping" desde el canal workflow
    Entonces se emite un evento task_execute
    Y el evento task_execute lleva channel "workflow"

  @trace-channel
  Escenario: Sin tracer no se emite ningún evento task_execute (degradación segura)
    Dado un TaskRegistry sin tracer y una task "ping" registrada
    Cuando ejecuto la task "ping" desde el canal cli
    Entonces no se emite ningún evento task_execute