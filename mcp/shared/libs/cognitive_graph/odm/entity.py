"""entity.py — Entity base class (ActiveRecord-style) para CognitiveGraph ODM.

Cada subclase de Entity define un indice/coleccion en CognitiveGraph:

    class Task(Entity):
        __index__ = "delta"
        __search__ = ["objective", "description"]

        task_id: str
        objective: str
        domain: str
        status: str

    # Schema se registra automaticamente al hacer Task.bind(cg)
    Task.bind(cg)

    # CRUD
    task = Task(objective="Fix auth bug", domain="security")
    task.save()                     # → cg.store("delta", id, data)
    task.delete()                   # → cg.delete("delta", id)

    # Queries
    tasks = Task.search("auth")             # full-text
    tasks = Task.find_by(domain="security") # structured
    task   = Task.get("sprintX:TASK-042")   # by ID
    tasks = Task.all()                      # todos

    # Relaciones (via edges del grafo)
    task.files          # navega edges impacts_file
    task.neighbors()    # todos los vecinos
"""

from __future__ import annotations
from typing import Any, ClassVar

from .fields import Field


class EntityMeta(type):
    """Metaclass que recolecta fields de la entidad."""

    def __new__(mcs, name: str, bases: tuple, namespace: dict):
        cls = super().__new__(mcs, name, bases, namespace)

        # Recolectar fields de la clase y sus padres
        fields: dict[str, Field] = {}
        for base in reversed(bases):
            if hasattr(base, "_fields"):
                fields.update(base._fields)
        for attr_name, attr_value in namespace.items():
            if isinstance(attr_value, Field):
                attr_value.name = attr_name
                fields[attr_name] = attr_value

        cls._fields = fields

        # G5: unificar fulltext — derivar __search__ desde fields.fulltext=True
        # y fusionarlo con el __search__ declarado explícitamente (si existe).
        # Antes coexistían dos mecanismos paralelos que se ignoraban mutuamente.
        declared = list(getattr(cls, "__search__", None) or [])
        derived = [
            fname for fname, f in fields.items() if getattr(f, "fulltext", False)
        ]
        cls.__search__ = list(dict.fromkeys(declared + derived))
        return cls


class Entity(metaclass=EntityMeta):
    """Base class para entidades ODM de CognitiveGraph.

    Atributos de clase:
        __index__: nombre del indice en CognitiveGraph (ej: "delta", "ckg", "cpg")
        __search__: lista de campos fulltext-searchables
        __id_prefix__: prefijo opcional para IDs (ej: "sprint-003:")

    Uso:
        class Task(Entity):
            __index__ = "delta"
            __search__ = ["objective"]
    """

    __index__: ClassVar[str] = "default"
    __search__: ClassVar[list[str]] = []
    __id_field__: ClassVar[str | None] = None  # campo a usar como ID (ej: "task_id")
    __id_prefix__: ClassVar[str] = ""

    # Binding a CognitiveGraph (opcional, se asigna con bind())
    _cg: ClassVar[Any] = None

    def __init__(self, **kwargs):
        self._data: dict[str, Any] = {}
        self._id: str | None = None
        self._cg_instance: Any = None  # por instancia (override del class binding)

        # Asignar defaults de fields
        for field_name, field in self._fields.items():
            if field.default is not None:
                self._data[field_name] = field.default

        # Asignar valores pasados al constructor
        for key, value in kwargs.items():
            if key == "_id":
                self._id = value
            elif key in self._fields or not self._fields:
                self._data[key] = value

    # ── Binding ──

    @classmethod
    def bind(cls, cg) -> None:
        """Vincula la entidad a un CognitiveGraph y registra el indice.

        Esto registra el schema del indice y permite usar los classmethods
        de query sin pasar cg cada vez.

        Args:
            cg: Instancia de CognitiveGraph
        """
        cls._cg = cg
        cls._register_index(cg)

    @classmethod
    def _register_index(cls, cg) -> None:
        """Registra el indice en CognitiveGraph segun la definicion de la entidad."""
        config = {
            "fields": cls._build_field_mapping(),
            "types": {cls.__index__: {"description": cls.__doc__ or ""}},
            # ADR-WALKER-063 (CA-9): SIN storage.path en el schema del ODM.
            # El backend lo decide el composition root (config), no la
            # entidad; sqlite v2 es SSOT y no hay rutas de archivo legacy.
            "description": cls.__doc__ or f"Indice {cls.__index__}",
        }

        # Configurar full-text search si hay campos marcados
        search_fields = cls.__search__
        if search_fields:
            config["search"] = {"type": "fulltext", "fields": search_fields}

        cg.register_index(cls.__index__, config)

    @classmethod
    def _build_field_mapping(cls) -> dict[str, str]:
        """Construye el mapping fields del schema desde los fields de la entidad."""
        mapping = {}
        for name, field in cls._fields.items():
            mapping[name] = name
        return mapping

    def _get_cg(self):
        """Retorna la instancia de CognitiveGraph (de clase o de instancia)."""
        return self._cg_instance or self.__class__._cg

    @classmethod
    def _get_cg_class(cls):
        return cls._cg

    # ── Propiedades ──

    @property
    def _id(self) -> str | None:
        return self.__dict__.get("__id")

    @_id.setter
    def _id(self, value: str):
        self.__dict__["__id"] = value

    @property
    def pk(self) -> str | None:
        """Primary key: el ID del nodo en el grafo."""
        return self._id

    # ── CRUD ──

    def save(self, cg=None) -> str:
        """Persiste la entidad en CognitiveGraph (create o update).

        Args:
            cg: Instancia de CognitiveGraph (opcional si se uso bind())

        Returns:
            El ID del nodo creado/actualizado
        """
        instance = cg or self._get_cg()
        if instance is None:
            raise RuntimeError(
                f"{self.__class__.__name__}.save() requiere CognitiveGraph. "
                "Usa bind(cg) o pasa cg=... en el metodo."
            )

        # Generar ID si no tiene
        if not self._id:
            # Usar __id_field__ si esta definido y disponible
            id_field = self.__class__.__id_field__
            if id_field and id_field in self._data and self._data[id_field]:
                self._id = f"{self.__class__.__id_prefix__}{self._data[id_field]}"
            else:
                self._id = self._generate_id()

        # Serializar datos (NO sobrescribir type, el indice se determina
        # por el primer argumento de store(), no por un campo interno)
        data = dict(self._data)

        # Guardar en CognitiveGraph
        instance.store(self.__index__, self._id, data)
        return self._id

    def delete(self, cg=None) -> bool:
        """Elimina la entidad de CognitiveGraph."""
        instance = cg or self._get_cg()
        if instance is None or not self._id:
            return False
        result = instance.delete(self.__index__, self._id)
        return result.get("deleted", False)

    # ── Queries de clase ──

    @classmethod
    def get(cls, doc_id: str, cg=None):
        """Obtiene una entidad por ID.

        Args:
            doc_id: ID del nodo
            cg: CognitiveGraph (opcional)

        Returns:
            Instancia de la entidad o None
        """
        instance = cg or cls._get_cg_class()
        if instance is None:
            raise RuntimeError(
                f"{cls.__name__}.get() requiere CognitiveGraph. "
                "Usa bind(cg) o pasa cg=..."
            )
        result = instance.get(cls.__index__, doc_id)
        if not result:
            return None
        return cls._from_cg_result(result)

    @classmethod
    def find_by(cls, cg=None, **filters):
        """Busca entidades por filtros estructurados.

        Args:
            cg: CognitiveGraph (opcional)
            **filters: pares campo=valor para filtrar

        Returns:
            Lista de instancias de la entidad
        """
        instance = cg or cls._get_cg_class()
        if instance is None:
            raise RuntimeError(
                f"{cls.__name__}.find_by() requiere CognitiveGraph. "
                "Usa bind(cg) o pasa cg=..."
            )
        result = instance.search(cls.__index__, filters)
        return [cls._from_cg_result(h) for h in result.get("hits", {}).get("hits", [])]

    @classmethod
    def update_or_create(cls, cg=None, **kwargs):
        """Upsert por campo de negocio: busca por filters, refina o crea.

        PRIMITIVA REUSABLE de infraestructura (GAP 1). Cualquier feature que
        extienda el grafo (delta, ckg, cpg, trace) puede hacer "match por
        campo -> update : create" sin duplicar la logica.

        La convención de uso: pasar los filtros de match en 'filters' (dict)
        y los campos a actualizar/crear en 'defaults' (dict). Ambos opcionales:

            Task.update_or_create(
                filters={"objective": obj},
                defaults={"status": "draft", "domain": "code-analysis"},
                cg=cg,
            )

        Returns:
            tuple[Entity, bool]: (instancia, created) donde created=True si
            se creo un nodo nuevo y False si se refino uno existente.
        """
        instance = cg or cls._get_cg_class()
        if instance is None:
            raise RuntimeError(
                f"{cls.__name__}.update_or_create() requiere CognitiveGraph. "
                "Usa bind(cg) o pasa cg=..."
            )
        filters = dict(kwargs.get("filters") or {})
        defaults = dict(kwargs.get("defaults") or {})
        match = cls.find_by(cg=instance, **filters)
        if match:
            obj = match[0]
            for k, v in defaults.items():
                setattr(obj, k, v)
            obj._cg_instance = instance
            obj.save()
            return obj, False
        # Crear con filters + defaults (filters son identidad del nodo)
        data = dict(filters)
        data.update(defaults)
        obj = cls(**data)
        obj._cg_instance = instance
        obj.save()
        return obj, True

    @classmethod
    def search(cls, text: str, cg=None, **filters):
        """Busqueda full-text + filtros opcionales.

        Args:
            text: Texto a buscar
            cg: CognitiveGraph (opcional)
            **filters: filtros adicionales

        Returns:
            Lista de instancias de la entidad
        """
        instance = cg or cls._get_cg_class()
        if instance is None:
            raise RuntimeError(
                f"{cls.__name__}.search() requiere CognitiveGraph. "
                "Usa bind(cg) o pasa cg=..."
            )
        query = {"text": text}
        query.update(filters)
        result = instance.search(cls.__index__, query)
        return [cls._from_cg_result(h) for h in result.get("hits", {}).get("hits", [])]

    @classmethod
    def all(cls, cg=None):
        """Retorna todas las entidades del indice."""
        instance = cg or cls._get_cg_class()
        if instance is None:
            raise RuntimeError(
                f"{cls.__name__}.all() requiere CognitiveGraph. "
                "Usa bind(cg) o pasa cg=..."
            )
        result = instance.search(cls.__index__, {})
        return [cls._from_cg_result(h) for h in result.get("hits", {}).get("hits", [])]

    @classmethod
    def count(cls, cg=None) -> int:
        """Cantidad de entidades en el indice."""
        instance = cg or cls._get_cg_class()
        if instance is None:
            return 0
        result = instance.search(cls.__index__, {})
        return result.get("hits", {}).get("total", 0)

    # ── Relaciones (via edges del grafo) ──

    def neighbors(self, edge_type: str | None = None, direction: str = "both",
                  cg=None) -> list[dict]:
        """Obtiene nodos vecinos desde los edges del grafo.

        Args:
            edge_type: Filtrar por tipo de edge (ej: "impacts_file", "depends_on")
            direction: "out" (salientes), "in" (entrantes), "both"
            cg: CognitiveGraph (opcional)

        Returns:
            Lista de dicts con {from, to, type, ...}
        """
        instance = cg or self._get_cg()
        if instance is None or not self._id:
            return []
        idx = instance._indexes.get(self.__index__)
        if not idx:
            return []
        edges = idx.get_edges(self._id, direction=direction)
        if edge_type:
            edges = [e for e in edges if e.get("type") == edge_type]
        return edges

    def related_entities(self, edge_type: str | None = None, cg=None):
        """Obtiene entidades relacionadas via edges.

        Similar a neighbors() pero retorna instancias de Entity
        en vez de dicts planos.

        Args:
            edge_type: Filtrar por tipo de edge
            cg: CognitiveGraph (opcional)

        Returns:
            Lista de entidades relacionadas
        """
        instance = cg or self._get_cg()
        neighbors = self.neighbors(edge_type=edge_type, cg=instance)
        results = []
        for n in neighbors:
            # Buscar el nodo en todos los indices
            for name, idx in instance._indexes.items():
                doc = idx.get(n["to"])
                if doc:
                    obj = Entity._from_cg_result_infer(doc, name)
                    if obj:
                        results.append(obj)
                    break
        return results

    # ── Edge management ──

    def add_edge(self, to_id: str, edge_type: str | None = None, cg=None, **attrs):
        """Agrega un edge desde esta entidad a otro nodo.

        Args:
            to_id: ID del nodo destino
            edge_type: Tipo de relacion (si no se pasa, se usa attrs["type"])
            cg: CognitiveGraph (opcional)
            **attrs: atributos adicionales del edge (type, weight, etc.)
        """
        instance = cg or self._get_cg()
        if instance is None or not self._id:
            return {"ok": False, "error": "No cg or no id"}
        # Merge edge_type y attrs (evitar duplicar type)
        edge_attrs = dict(attrs)
        if edge_type:
            edge_attrs["type"] = edge_type
        if "type" not in edge_attrs:
            edge_attrs["type"] = "related"
        return instance.add_edge(self._id, to_id, **edge_attrs)

    # ── Utilidades ──

    def to_dict(self) -> dict:
        """Serializa la entidad a dict."""
        return dict(self._data)

    def __repr__(self) -> str:
        cls_name = self.__class__.__name__
        pk = self._id or "?"
        return f"<{cls_name} id={pk}>"

    def _generate_id(self) -> str:
        """Genera un ID unico para la entidad.

        Sobrescribir en subclases si se necesita un formato especifico.
        """
        import uuid

        prefix = self.__class__.__id_prefix__ or ""
        return f"{prefix}{uuid.uuid4().hex[:12]}"

    @classmethod
    def _from_cg_result(cls, hit: dict):
        """Construye una instancia desde un resultado de CognitiveGraph."""
        source = hit.get("_source", {})
        doc_id = hit.get("_id", "")
        obj = cls(**source)
        obj._id = doc_id
        # Guardar relacion si existe
        if "_related" in hit and hit["_related"]:
            obj.__dict__["_related_cache"] = hit["_related"]
        return obj

    @staticmethod
    def _from_cg_result_infer(doc: dict, index_name: str):
        """Construye la entidad apropiada segun el indice.

        Busca una subclase de Entity cuyo __index__ coincida.
        """
        source = doc.get("_source", {})
        doc_id = doc.get("_id", "")

        # Buscar subclase de Entity con este index
        target_cls = None
        for subcls in Entity.__subclasses__():
            if getattr(subcls, "__index__", None) == index_name:
                target_cls = subcls
                break

        if target_cls is None:
            # Fallback: Entity generico
            obj = Entity(**source)
        else:
            obj = target_cls(**source)
        obj._id = doc_id
        return obj

    # ── Acceso a campos como atributos ──

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name, None)

    def __setattr__(self, name: str, value):
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self._data[name] = value
