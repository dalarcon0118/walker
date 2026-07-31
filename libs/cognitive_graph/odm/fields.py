"""fields.py — Descriptores de campo para entidades ODM.

Cada field define el tipo, si es fulltext-searchable, y opciones de validacion.
Se usan como type hints en las entidades:
    class Task(Entity):
        name: str = fields.String(max_length=100)
        age: int = fields.Int(min=0)
"""

from __future__ import annotations
from typing import Any


class Field:
    """Descriptor base para campos de entidad."""

    def __init__(
        self,
        field_type: str = "string",
        fulltext: bool = False,
        required: bool = False,
        default: Any = None,
        **kwargs,
    ):
        self.field_type = field_type
        self.fulltext = fulltext
        self.required = required
        self.default = default
        self.extra = kwargs
        self.name: str = ""  # se asigna en el metaclass

    def __set_name__(self, owner, name: str):
        self.name = name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance._data.get(self.name, self.default)

    def __set__(self, instance, value):
        instance._data[self.name] = value


class String(Field):
    def __init__(self, max_length: int | None = None, **kwargs):
        super().__init__(field_type="string", **kwargs)
        self.max_length = max_length


class Text(Field):
    def __init__(self, **kwargs):
        super().__init__(field_type="text", **kwargs)


class Int(Field):
    def __init__(self, min: int | None = None, max: int | None = None, **kwargs):
        super().__init__(field_type="int", **kwargs)
        self.min = min
        self.max = max


class Float(Field):
    def __init__(self, **kwargs):
        super().__init__(field_type="float", **kwargs)


class Bool(Field):
    def __init__(self, **kwargs):
        super().__init__(field_type="bool", **kwargs)


class List(Field):
    def __init__(self, item_type: str = "string", **kwargs):
        super().__init__(field_type="list", **kwargs)
        self.item_type = item_type


class Dict(Field):
    def __init__(self, **kwargs):
        super().__init__(field_type="dict", **kwargs)


class Related:
    """Define una relacion con otra entidad via edges del grafo.

    Se usa dentro de __edges__ en la Meta de la entidad:
        class Task(Entity):
            class Meta:
                edges = {
                    "files": Related(type="impacts_file", target="file", multiple=True),
                    "author": Related(type="author_of", target="user", multiple=False),
                }
    """

    def __init__(
        self,
        edge_type: str = "related",
        target_entity: str | None = None,
        multiple: bool = True,
        direction: str = "out",
    ):
        self.edge_type = edge_type
        self.target_entity = target_entity
        self.multiple = multiple
        self.direction = direction
