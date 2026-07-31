"""Tesorería de errores del dominio CKG."""
from __future__ import annotations


class CkgError(Exception):
    """Base de todos los errores del CKG."""
    pass


class NodeNotFound(CkgError):
    def __init__(self, node_id: str) -> None:
        super().__init__(f"Nodo no encontrado: {node_id}")
        self.node_id = node_id


class EdgeNotFound(CkgError):
    def __init__(self, from_id: str, to_id: str) -> None:
        super().__init__(f"Edge no encontrado: {from_id} → {to_id}")
        self.from_id = from_id
        self.to_id = to_id


class ValidationError(CkgError):
    def __init__(self, details: str) -> None:
        super().__init__(f"Error de validación: {details}")
        self.details = details


class SchemaViolation(CkgError):
    def __init__(self, path: str, expected: str, actual: str) -> None:
        super().__init__(f"Violación de schema en {path}: esperaba {expected}, recibió {actual}")
        self.path = path
        self.expected = expected
        self.actual = actual


class DuplicateNode(CkgError):
    def __init__(self, node_id: str) -> None:
        super().__init__(f"Nodo duplicado: {node_id}")
        self.node_id = node_id


class RepositoryError(CkgError):
    """Error en operaciones de repositorio (IO, formato)."""
    pass


class SchemaValidationError(CkgError):
    """Error de validación contra schema JSON."""
    pass
