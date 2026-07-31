"""
_walker/persistence.py — Persistence layer idempotente.

SRP: Unica responsabilidad = snapshot/restore del estado del workflow.
SSOT: Los snapshots son la unica fuente de verdad para recuperacion.

Arquitectura:
  SnapshotMetadata   → Value Object inmutable con metadatos del snapshot
  PersistenceService → Domain Service con metodos estaticos puros (sin IO directa,
                       reciben paths como parametros)

Principios:
  - Atomicidad: .tmp + os.rename() (POSIX atomico)
  - Idempotencia: mismo estado = mismo checksum SHA-256
  - Integridad: checksum verificado en restore()
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

SNAPSHOT_SUFFIX = ".snapshot.json"
TMP_SUFFIX = ".tmp"


# ── Value Objects ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SnapshotMetadata:
    """Metadatos inmutables de un snapshot.

    Attributes:
        path: Ruta absoluta al archivo snapshot.
        timestamp: Timestamp ISO 8601 de cuando se tomo el snapshot.
        checksum: SHA-256 hex digest del contenido (sin el campo checksum).
        size: Tamano del archivo en bytes.
        version: Version del schema del snapshot (para migraciones futuras).
    """
    path: str
    timestamp: str
    checksum: str
    size: int
    version: str = "1.0"


# ── Domain Service ─────────────────────────────────────────────────────────

class PersistenceService:
    """Servicio de dominio para snapshot/restore del estado del workflow.

    SRP: Unica responsabilidad = gestionar snapshots de estado.
    SSOT: Los snapshots persisten en disco con checksum SHA-256.
    Sin IO directa: recibe paths como argumentos en lugar de depender
    de configuracion global.

    Formato del snapshot:
    {
        "version": "1.0",
        "timestamp": "2026-07-23T12:00:00.000000+00:00",
        "checksum": "sha256_<hex_digest>",
        "state": { ... }  # El estado real del workflow
    }
    """

    SNAPSHOT_VERSION = "1.0"

    @staticmethod
    def save_snapshot(state: dict[str, Any], path: str) -> SnapshotMetadata:
        """Persiste un snapshot del estado con escritura atomica y checksum.

        Flujo:
          1. Serializa estado a JSON
          2. Calcula SHA-256 del JSON serializado
          3. Construye snapshot final con metadatos + checksum + estado
          4. Escribe a .tmp (archivo temporal)
          5. Renombra .tmp a path destino (operacion atomica en POSIX)
          6. Retorna SnapshotMetadata

        Args:
            state: Dict con el estado a persistir.
            path: Ruta destino del archivo snapshot.

        Returns:
            SnapshotMetadata con los metadatos del snapshot creado.

        Raises:
            OSError: Si hay error de escritura en disco.
            TypeError: Si el estado no es serializable a JSON.
        """
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.gmtime())
        tmp_path = path + TMP_SUFFIX

        # Serializar estado a JSON para checksum
        state_json = json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True, default=str)
        checksum = hashlib.sha256(state_json.encode("utf-8")).hexdigest()

        # Construir snapshot final
        snapshot = {
            "version": PersistenceService.SNAPSHOT_VERSION,
            "timestamp": timestamp,
            "checksum": f"sha256_{checksum}",
            "state": state,
        }

        # Escritura atomica: .tmp + rename
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp_path, path)

        # Metadata del snapshot creado
        file_size = os.path.getsize(path)
        return SnapshotMetadata(
            path=os.path.abspath(path),
            timestamp=timestamp,
            checksum=f"sha256_{checksum}",
            size=file_size,
            version=PersistenceService.SNAPSHOT_VERSION,
        )

    @staticmethod
    def restore(path: str) -> dict[str, Any]:
        """Restaura el estado desde un snapshot.

        Flujo:
          1. Lee el archivo snapshot
          2. Verifica checksum SHA-256
          3. Si checksum valido, retorna el estado
          4. Si checksum invalido, lanza ValueError

        Args:
            path: Ruta al archivo snapshot.

        Returns:
            Dict con el estado del workflow.

        Raises:
            FileNotFoundError: Si el archivo no existe.
            ValueError: Si el checksum no coincide (datos corruptos/manipulados).
            json.JSONDecodeError: Si el archivo no es JSON valido.
        """
        with open(path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)

        stored_checksum = snapshot.get("checksum", "")
        state = snapshot.get("state", {})

        # Verificar checksum
        PersistenceService._verify_checksum(state, stored_checksum)

        return state

    @staticmethod
    def _verify_checksum(state: dict[str, Any], stored_checksum: str) -> None:
        """Verifica que el checksum del estado coincida con el almacenado.

        Args:
            state: El estado extraido del snapshot.
            stored_checksum: Checksum almacenado en el snapshot (con prefijo sha256_).

        Raises:
            ValueError: Si el checksum no coincide.
        """
        if not stored_checksum.startswith("sha256_"):
            raise ValueError(
                f"Formato de checksum invalido: '{stored_checksum}'. "
                "Esperado: 'sha256_<hex>'"
            )

        expected_hex = stored_checksum[len("sha256_"):]
        state_json = json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True, default=str)
        actual_checksum = hashlib.sha256(state_json.encode("utf-8")).hexdigest()

        if actual_checksum != expected_hex:
            raise ValueError(
                f"Checksum SHA-256 no coincide. "
                f"Esperado: {expected_hex}, "
                f"Actual: {actual_checksum}. "
                "El snapshot puede estar corrupto o haber sido manipulado."
            )

    @staticmethod
    def list_snapshots(directory: str) -> list[SnapshotMetadata]:
        """Enumera todos los snapshots en un directorio.

        Busca archivos que terminan en .snapshot.json y extrae metadatos.

        Args:
            directory: Directorio donde buscar snapshots.

        Returns:
            Lista de SnapshotMetadata ordenada por timestamp descendente
            (mas reciente primero).
        """
        if not os.path.isdir(directory):
            return []

        snapshots: list[SnapshotMetadata] = []
        for filename in os.listdir(directory):
            if not filename.endswith(SNAPSHOT_SUFFIX):
                continue

            filepath = os.path.join(directory, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                metadata = SnapshotMetadata(
                    path=os.path.abspath(filepath),
                    timestamp=data.get("timestamp", ""),
                    checksum=data.get("checksum", ""),
                    size=os.path.getsize(filepath),
                    version=data.get("version", "1.0"),
                )
                snapshots.append(metadata)
            except (json.JSONDecodeError, OSError):
                # Archivo corrupto — lo omitimos
                continue

        # Ordenar por timestamp descendente (mas reciente primero)
        snapshots.sort(key=lambda s: s.timestamp, reverse=True)
        return snapshots

    @staticmethod
    def snapshot_exists(path: str) -> bool:
        """Verifica si existe un archivo snapshot en la ruta dada.

        Args:
            path: Ruta al archivo snapshot.

        Returns:
            True si el archivo existe y no es un .tmp residual.
        """
        if not os.path.isfile(path):
            return False
        # Ignorar archivos .tmp residuales (deberian haber sido renombrados)
        if path.endswith(TMP_SUFFIX):
            return False
        return True

    @staticmethod
    def save_full_snapshot(
        state: dict[str, Any],
        path: str,
        event_log_data: dict | None = None,
    ) -> SnapshotMetadata:
        """Persiste un snapshot completo que incluye estado + event_log.

        Integracion con EventLog.to_snapshot_data(): permite guardar
        el estado y los eventos en un unico archivo snapshot,
        garantizando consistencia entre ambos.

        Args:
            state: Dict con el estado del workflow.
            path: Ruta destino del archivo snapshot.
            event_log_data: Dict opcional con datos del event_log
                (de EventLog.to_snapshot_data()).

        Returns:
            SnapshotMetadata del snapshot creado.
        """
        full_state = dict(state)
        if event_log_data:
            full_state["__event_log__"] = event_log_data
        return PersistenceService.save_snapshot(full_state, path)

    @staticmethod
    def cleanup_tmp(directory: str) -> int:
        """Limpia archivos .tmp residuales que hayan quedado de escrituras fallidas.

        Args:
            directory: Directorio donde buscar .tmp residuales.

        Returns:
            Numero de archivos .tmp eliminados.
        """
        if not os.path.isdir(directory):
            return 0

        cleaned = 0
        for filename in os.listdir(directory):
            if filename.endswith(TMP_SUFFIX):
                try:
                    os.remove(os.path.join(directory, filename))
                    cleaned += 1
                except OSError:
                    continue
        return cleaned
