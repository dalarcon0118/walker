# language: es
# tests/features/cli_router.feature
#
# Feature: CLI Router — entry point del walker
#
# Como usuario del walker
# Quiero que el CLI router dispatche correctamente los comandos
# Para poder interactuar con el sistema vía REST

Característica: CLI Router — entry point del walker
  Como usuario del walker
  Quiero que el CLI router dispatche correctamente los comandos
  Para poder interactuar con el sistema vía REST

  Escenario: sin argumentos muestra help
    Dado que ejecuto main con []
    Entonces el exit code es 0
    Y la salida contiene "Walker REST protocol"

  Escenario: GET status resuelve desde CWD
    Dado que ejecuto main con ["status"]
    Entonces el exit code es 0
    Y la salida contiene "workflow"

  Escenario: POST submit con body JSON
    Dado que ejecuto main con ["POST", "submit", '{"status":"submitted"}']
    Entonces el exit code es 1

  Escenario: verbos HTTP explícitos
    Dado que ejecuto main con ["GET", "status"]
    Entonces el exit code es 0

  Escenario: ruta project/scoped
    Dado que ejecuto main con ["walker/status"]
    Entonces el exit code es 0

  Escenario: task directa
    Dado que ejecuto main con ["task", "recall_knowledge", "--query", "ckg"]
    Entonces el exit code es 0

  Escenario: JSON body en positional
    Dado que ejecuto main con ["submit", '{"field":"value"}']
    Entonces el exit code es 1

  Escenario: query params estilo URL
    Dado que ejecuto main con ["recall_knowledge?index=ckg"]
    Entonces el exit code es 0

  Escenario: ruta desconocida
    Dado que ejecuto main con ["no_existe"]
    Entonces el exit code es 1
    Y la salida contiene "no registrada"
