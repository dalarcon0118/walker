"""tests.features — subpackage de archivos .feature (pytest-bdd).

Marcar este directorio como paquete regular evita que sea tratado como
namespace package `features` (PEP 420) cuando pytest inserta `tests/` en
sys.path. Los escenarios BDD referencian estos archivos por ruta relativa
(scenarios("features/x.feature")), no por import — el __init__.py solo
garantiza que nunca shadowee al paquete real `features/`.
"""
