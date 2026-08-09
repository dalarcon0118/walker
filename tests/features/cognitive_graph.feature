# language: es
Característica: Cognitive graph persistence and search
  Como desarrollador del sistema
  Quiero que el grafo cognitivo persista y se pueda buscar
  Para que el conocimiento del proyecto se preserve y sea consultable

  Antecedentes:
    Dado que el proyecto cuenta con un grafo cognitivo como repositorio de conocimiento

  Escenario: El conocimiento del proyecto se preserva a través del tiempo
    Dado que he almacenado decisiones y convenciones del proyecto en el grafo cognitivo
    Cuando el sistema cierra y se reinicia posteriormente
    Entonces todo el conocimiento permanece disponible
    Y ninguna decisión o convención se pierde entre sesiones

  Escenario: Puedo encontrar el conocimiento del proyecto mediante búsquedas
    Dado que el proyecto acumula tareas, decisiones y convenciones en el grafo
    Cuando necesito encontrar conocimiento por palabra clave o por dominio
    Entonces el sistema devuelve los resultados relevantes
    Y la búsqueda respeta el contexto del proyecto

  Escenario: La información del proyecto se mantiene sin duplicados ni inconsistencias
    Dado que almaceno conocimiento en el grafo cognitivo
    Cuando guardo la misma información más de una vez
    Entonces el sistema no genera duplicados
    Y actualizar conocimiento previo conserva la información existente

  Escenario: Las relaciones entre conocimientos son explorables
    Dado que el proyecto registra dependencias entre tareas, archivos y decisiones
    Cuando consulto las relaciones de un elemento de conocimiento
    Entonces puedo navegar las conexiones relevantes
    Y las relaciones se mantienen al persistir y recuperar el grafo

  Escenario: Las decisiones del proyecto conservan su historial completo
    Dado que el equipo toma decisiones que evolucionan con el tiempo
    Cuando una decisión reemplaza a otra sobre el mismo tema
    Entonces la decisión anterior se preserva como historial
    Y las consultas habituales muestran solo la versión vigente

  Escenario: El trabajo del equipo se organiza y recupera por sprints
    Dado que el equipo ejecuta trabajo en sprints y sesiones informales
    Cuando consulto el trabajo realizado en un sprint o sesión
    Entonces puedo ver las tareas y su estado de completitud
    Y el sistema distingue entre sprints formales e informales

  Escenario: El conocimiento legacy se puede incorporar al sistema actual
    Dado que el proyecto cuenta con conocimiento almacenado en formatos anteriores
    Cuando importo ese conocimiento al modelo unificado actual
    Entonces el conocimiento queda disponible en el sistema actual
    Y la información de estado y contexto se preserva correctamente

  Escenario: Las consultas al grafo quedan trazadas para auditoría
    Dado que el sistema registra cada consulta de conocimiento realizada
    Cuando diferentes componentes consultan el grafo cognitivo
    Entonces cada consulta se registra con su contexto de sesión y tarea
    Y no se generan registros duplicados por rutas alternativas

  Escenario: La re-planificación de trabajo agrega contenido sin perder lo anterior
    Dado que un sprint tiene una planificación existente
    Cuando el equipo re-planifica agregando nuevas tareas
    Entonces las tareas nuevas se incorporan sin eliminar las existentes
    Y la trazabilidad de qué era viejo y qué es nuevo se mantiene

  Escenario: El grafo cognitivo permite extraer conocimiento del proyecto de forma estructurada
    Dado que el proyecto acumula conocimiento a lo largo del tiempo
    Cuando ejecuto la extracción estructurada del conocimiento
    Entonces obtengo un resumen coherente del estado del proyecto
    Y el cuestionario de extracción cubre los aspectos relevantes del dominio
