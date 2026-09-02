"""cognitive_graph/connection.py — MemoryConnection (config, no path).

ADR-WALKER-061 (F1): la conexión a la capa de memorias es CONFIGURACIÓN,
no un path de filesystem. La memoria (CPG/CKG/Delta/Trace) nunca ve esto;
el composition root la resuelve desde env/secret manager y la inyecta.

Modelo empresarial: "database connection is configuration". El backend
("sqlite" hoy; "postgres" | "mongo" en el futuro) y el uri
(sqlite://./data | postgresql://…) viven aquí, no en el schema del índice
ni en CognitiveGraph.__init__.

ADR-WALKER-063 (corte directo): el default es 'sqlite' (v2, SSOT). 'file'
legacy ya NO es un valor de backend runtime — solo export/migración/tests
(FileBackend no registrado; resolver 'file' falla fail-fast).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryConnection:
    """Configuración de conexión a la capa de memorias (inmutable).

    Atributos:
        backend: motor de storage ("sqlite" hoy; "postgres"/"mongo" futuro)
        uri: ubicación lógica (sqlite://./data | postgresql://user@host/db)
        database: nombre lógico de la base de datos
        collection_prefix: prefijo opcional para nombres de índice/colección
        options: parametros extra (pool size, timeout, ssl) — sin creds en código

    Invariante (ADR-WALKER-061 AP-4/AP-8): NINGÚN path de filesystem ni
    credential hardcodeada. El `uri` se lee desde env en F6.
    """

    backend: str = "sqlite"
    uri: str = "sqlite://./data"
    database: str = "walker"
    collection_prefix: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    def index_name(self, name: str) -> str:
        """Nombre de colección/índice resuelto con el prefijo de conexión."""
        return f"{self.collection_prefix}{name}" if self.collection_prefix else name
