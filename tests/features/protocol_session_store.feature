# language: es
# tests/features/protocol_session_store.feature
#
# Feature: Repositorio InMemory de Sesiones MCP (F8).
#
# Provee persistencia en memoria de McpSession con return pattern,
# lifecycle completo (save/load/close) y soporte multi-sesión.

Característica: Repositorio InMemory de Sesiones MCP
  Como sistema de gestión de sesiones MCP
  Quiero un repositorio en memoria con lifecycle completo
  Para persistir, recuperar y cerrar sesiones de protocolo

  @contrato
  Escenario: Guardar y cargar una sesión
    Dado un InMemorySessionRepository vacío
    Y una McpSession creada
    Cuando guardo la sesión
    Y la cargo por su id
    Entonces la sesión cargada coincide con la original

  @contrato
  Escenario: Cargar una sesión inexistente falla
    Dado un InMemorySessionRepository vacío
    Cuando cargo una sesión inexistente
    Entonces la carga retorna None
    Y el error indica not found

  @contrato
  Escenario: Cerrar una sesión la elimina del repositorio
    Dado un InMemorySessionRepository con una sesión guardada
    Cuando cierro la sesión por su id
    Entonces el repositorio queda vacío

  @contrato
  Escenario: Cerrar una sesión inexistente falla
    Dado un InMemorySessionRepository vacío
    Cuando cierro una sesión inexistente
    Entonces la operación falla con errores

  @contrato
  Escenario: Lifecycle completo de sesión
    Dado un InMemorySessionRepository vacío
    Y una McpSession inicializada
    Cuando guardo la sesión
    Y la cargo
    Y la activo
    Y la guardo nuevamente
    Y la cierro
    Entonces el repositorio queda vacío

  @contrato
  Escenario: Múltiples sesiones coexisten en el repositorio
    Dado un InMemorySessionRepository vacío
    Y dos McpSessions creadas
    Cuando guardo ambas sesiones
    Entonces el repositorio tiene 2 sesiones
    Y list_sessions retorna 2 sesiones
