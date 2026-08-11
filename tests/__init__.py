"""tests — package raíz de la suite.

Al ser un paquete regular, pytest importa los test modules como `tests.test_x`
e inserta la raíz del proyecto en sys.path (NO `tests/`). Esto evita que el
directorio `bdd/features/` (que solo contiene archivos .feature) sea
importado como namespace package top-level `features` y shadowee el paquete
real `features/` en el McpServer (_SERVER) y demás módulos.
"""
