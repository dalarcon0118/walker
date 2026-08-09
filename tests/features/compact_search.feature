# language: es
# tests/features/compact_search.feature
#
# Feature: Compact search (poda de respuestas)
#
# Como sistema de conocimiento
# Quiero proyectar, rankear y limitar las respuestas de busqueda
# Para reducir el tamanio de las respuestas enviadas al LLM

Característica: Poda de respuestas en busquedas
  Como sistema de conocimiento
  Quiero proyectar, rankear y limitar las respuestas de busqueda
  Para reducir el tamanio de las respuestas enviadas al LLM

  Antecedentes:
    Dado que existe un proyecto temporal con CKG, delta y trace con datos de prueba

  @compact-search
  Escenario: search_many proyecta campos sin rationale ni metadata
    Cuando ejecuto search_many con query ckg type:convention
    Entonces los hits no contienen rationale en _source
    Y los hits no contienen metadata en _source

  @compact-search
  Escenario: Strings largos se truncan a max_chars
    Cuando ejecuto search_many con _max_chars 50
    Entonces los statement truncados tienen longitud <= 53

  @compact-search
  Escenario: El campo data del trace se resume
    Cuando ejecuto search_many sobre trace
    Entonces las claves de data estan dentro de {index, query}
    Y los valores de query truncados tienen longitud <= 163

  @compact-search
  Escenario: CKG rankea obligatoria antes que recomendada
    Cuando ejecuto search_many con query ckg type:convention
    Entonces la primera severidad es obligatoria

  @compact-search
  Escenario: Trace rankea por timestamp reciente
    Cuando ejecuto search_many sobre trace
    Entonces el primer evento tiene el timestamp mas reciente

  @compact-search
  Escenario: top_k limita hits por capa y conserva el total real
    Cuando ejecuto search_many con _top_k 2 sobre ckg
    Entonces se devuelven 2 hits
    Y el total del indice sigue siendo 3

  @compact-search
  Escenario: La estructura ES-like se conserva
    Cuando ejecuto search_many con query ckg type:convention
    Entonces la respuesta contiene took, index, hits y schema
    Y hits contiene total y hits

  @compact-search
  Escenario: _compact=False desactiva la poda
    Cuando ejecuto search_many con _compact False sobre ckg
    Entonces algun hit contiene rationale

  @compact-search
  Escenario: _with_compact_defaults inyecta defaults
    Cuando se aplica _with_compact_defaults a una query base
    Entonces _top_k es 10
    Y _max_chars es 400
    Y _compact es True
    Y no pisa valores explicitos del caller
    Y no muta el diccionario original

  @compact-search
  Escenario: La respuesta compactada es significativamente menor
    Cuando comparo la respuesta compacta contra la completa
    Entonces la respuesta compactada tiene menos bytes que la completa
