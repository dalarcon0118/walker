# language: es
# tests/features/cpg_infra_caches.feature
#
# Feature: Caches incrementales de infraestructura del CPG.
# calls_cache (CFG calls por archivo), scan_dfg (DFG con cache + async).
# Contrato: ante cache poblado devuelven hits; al modificar el archivo se
# invalida (convention-001: nunca except silencioso).
#
# metrics_cache fue eliminado junto a qlty (ADR-WALKER-054): las métricas
# son CPG-nativas y no requieren cache externo.

Característica: Caches incrementales de la infraestructura del CPG
  Como arquitecto del módulo CPG
  Quiero que los caches eviten re-escanear archivos sin cambios
  Para que el build incremental sea eficiente

  @cache
  Escenario: calls_cache reusa resultados y persiste
    Dado un módulo Python con funciones
    Cuando se detectan calls por primera vez y luego de nuevo
    Entonces la segunda detección proviene del caché
    Y el caché persiste entre instancias

  @cache
  Escenario: scan_dfg usa el caché en la segunda corrida
    Dado un builder del CPG con un módulo que contiene un secreto
    Cuando se escanea el dfg dos veces
    Entonces la primera corrida no tiene cache_hits
    Y la segunda corrida tiene cache_hits
    Y los taint_paths coinciden entre corridas

  @cache
  Escenario: scan_dfg async persiste el estado
    Dado un builder del CPG con un módulo bajo src
    Cuando se lanza el scan dfg en background
    Entonces el estado inicial es running
    Y el estado final es ok