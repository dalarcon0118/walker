# language: es
# tests/features/dependency_checker.feature
#
# Feature: Dependency Checker — verificación de CLI tools
#
# Como sistema
# Quiero verificar herramientas CLI requeridas
# Para asegurar que el análisis de código funcione

Característica: Dependency Checker — verificación de CLI tools
  Como sistema
  Quiero verificar herramientas CLI requeridas
  Para asegurar que el análisis de código funcione

  Escenario: is_available cachea el resultado
    Dado que limpio el cache
    Cuando consulto is_available python3
    Entonces el resultado es True

  Escenario: ensure_tools devuelve vacío si todo está instalado
    Dado que el cache tiene python3 disponible
    Cuando ejecuto ensure_tools con python3
    Entonces el resultado es vacío

  Escenario: ensure_tools detecta faltantes
    Dado que el cache tiene missing-tool-xyz no disponible
    Cuando ejecuto ensure_tools con missing-tool-xyz
    Entonces el resultado contiene missing-tool-xyz

  Escenario: ensure_ast_grep devuelve True si está disponible
    Dado que el cache tiene ast-grep disponible
    Cuando ejecuto ensure_ast_grep
    Entonces el resultado es True

  Escenario: ensure_qlty devuelve True si está disponible
    Dado que el cache tiene qlty disponible
    Cuando ejecuto ensure_qlty
    Entonces el resultado es True

  Escenario: reset_cache limpia el cache
    Dado que el cache tiene python3 disponible
    Cuando ejecuto reset_cache
    Entonces el cache está vacío
