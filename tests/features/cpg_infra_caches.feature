# language: es
# tests/features/cpg_infra_caches.feature
#
# Feature: Caches incrementales de infraestructura del CPG.
# metrics_cache (métricas por archivo con snapshot por mtime/size), calls_cache
# (CFG calls por archivo), scan_dfg (DFG con cache + async). Contrato: ante
# cache poblado devuelven hits; al modificar el archivo se invalida
# (convention-001: nunca except silencioso).

Característica: Caches incrementales de la infraestructura del CPG
  Como arquitecto del módulo CPG
  Quiero que los caches eviten re-escanear archivos sin cambios
  Para que el build incremental sea eficiente

  @caché
  Escenario: metrics_cache marca archivos por mtime
    Dado un módulo Python y un caché de métricas
    Cuando se consultan los archivos stale
    Entonces el módulo es stale
    Y al actualizarlo el caché lo marca fresco
    Y al modificar el archivo se vuelve stale

  @caché
  Escenario: calls_cache reusa resultados y persiste
    Dado un módulo Python con funciones
    Cuando se detectan calls por primera vez y luego de nuevo
    Entonces la segunda detección proviene del caché
    Y el caché persiste entre instancias

  @caché
  Escenario: scan_dfg usa el caché en la segunda corrida
    Dado un builder del CPG con un módulo que contiene un secreto
    Cuando se escanea el dfg dos veces
    Entonces la primera corrida no tiene cache_hits
    Y la segunda corrida tiene cache_hits
    Y los taint_paths coinciden entre corridas

  @caché
  Escenario: scan_dfg async persiste el estado
    Dado un builder del CPG con un módulo bajo src
    Cuando se lanza el scan dfg en background
    Entonces el estado inicial es running
    Y el estado final es ok

  @caché
  Escenario: expand_paths filtra archivos de código
    Dado un directorio con archivos .py, .js, .md y .pyc
    Cuando se expanden los paths
    Entonces incluye los .py y .js
    Y excluye README.md y el .pyc