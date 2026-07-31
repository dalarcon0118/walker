"""
_walker/event_store.py — Event Sourcing append-only.

SRP: Unica responsabilidad = gestion de event log append-only para
     reconstruccion de estado y auditoria via replay.
SSOT: event_log.json es la unica fuente de verdad para eventos.

Arquitectura:
  EventRecord      → Value Object: un evento persistido en el log
  EventStore       → Domain Service: append_event(), replay(), query_events()

Principios:
  - Append-only: una vez escrito, nunca se modifica ni elimina
  - Inmutabilidad: los eventos son inmutables despues de escritos
  - Replay: reconstruccion de estado aplicando eventos secuencialmente
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


# ── Value Object ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EventRecord:
    """Un evento persistido en el event log.

    Inmutable: una vez creado, no se puede modificar.
    Este es el registro de auditoria fundamental del sistema.

    Attributes:
        event_type: Tipo de evento (ej: 'state_transitioned', 'task_started').
        timestamp: ISO 8601 timestamp del evento. Si es None, se asigna
                   automaticamente al crearse.
        data: Datos del evento (dict arbitrario).
        sequence: Numero de secuencia (orden en el log).
    """
    event_type: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    data: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0


# ── Domain Service ─────────────────────────────────────────────────────────

class EventStore:
    """Domain Service para Event Sourcing append-only.

    SRP: Append, replay, y query de eventos de dominio.
    SSOT: event_log.json en disco.
    Sin IO directa: recibe log_path como parametro.

    Formato del event_log.json:
    [
        {
            "type": "state_transitioned",
            "timestamp": "2026-07-23T12:00:00.000000+00:00",
            "data": { "from": "SPRINT", "to": "ANALYZE" },
            "sequence": 1
        },
        ...
    ]
    """

    LOG_FILENAME = "event_log.json"

    @staticmethod
    def append_event(event: EventRecord, log_path: str) -> EventRecord:
        """Agrega un evento al log de forma append-only e inmutable.

        Lee el log existente, asigna el siguiente sequence number,
        escribe el log completo de forma atomica (.tmp + rename).

        Args:
            event: El evento a persistir (sin sequence — se asigna automaticamente).
            log_path: Ruta al archivo event_log.json.

        Returns:
            EventRecord con el sequence number asignado.

        Raises:
            OSError: Si hay error de escritura.
            json.JSONDecodeError: Si el log existente esta corrupto.
        """
        existing = EventStore._load_log(log_path)
        next_seq = len(existing) + 1
        event_with_seq = EventRecord(
            event_type=event.event_type,
            timestamp=event.timestamp or EventStore._now(),
            data=dict(event.data),
            sequence=next_seq,
        )

        serialized = []
        for e in existing:
            serialized.append({
                "type": e.event_type,
                "timestamp": e.timestamp,
                "data": dict(e.data),
                "sequence": e.sequence,
            })
        serialized.append({
            "type": event_with_seq.event_type,
            "timestamp": event_with_seq.timestamp,
            "data": dict(event_with_seq.data),
            "sequence": event_with_seq.sequence,
        })

        # Escritura atomica
        tmp_path = log_path + ".tmp"
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serialized, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, log_path)

        return event_with_seq

    @staticmethod
    def replay(
        log_path: str,
        from_sequence: int = 1,
    ) -> list[EventRecord]:
        """Reconstruye eventos desde el log en orden secuencial.

        Args:
            log_path: Ruta al archivo event_log.json.
            from_sequence: Sequence inicial (default 1 = desde el principio).

        Returns:
            Lista de EventRecord en orden de sequence (ascendente).
        """
        all_events = EventStore._load_log(log_path)
        if from_sequence > 1:
            all_events = [e for e in all_events if e.sequence >= from_sequence]
        return sorted(all_events, key=lambda e: e.sequence)

    @staticmethod
    def replay_state(
        log_path: str,
        initial_state: dict[str, Any],
        apply_fn: Callable[[dict[str, Any], EventRecord], dict[str, Any]],
        from_sequence: int = 1,
    ) -> dict[str, Any]:
        """Reconstruye estado aplicando eventos secuencialmente.

        Es la operacion fundamental de Event Sourcing:
        estado = apply(apply(...apply(initial_state, event1), event2), eventN)

        Args:
            log_path: Ruta al archivo event_log.json.
            initial_state: Estado inicial (antes del primer evento).
            apply_fn: Funcion de reduccion: (state, event) -> new_state.
            from_sequence: Opcional — desde que sequence empezar.

        Returns:
            Estado reconstruido despues de aplicar todos los eventos.
        """
        events = EventStore.replay(log_path, from_sequence)
        state = dict(initial_state)
        for event in events:
            state = apply_fn(state, event)
        return state

    @staticmethod
    def query_events(
        log_path: str,
        event_type: str | None = None,
        from_timestamp: str | None = None,
        to_timestamp: str | None = None,
    ) -> list[EventRecord]:
        """Filtra eventos por tipo y/o rango de timestamps.

        Args:
            log_path: Ruta al archivo event_log.json.
            event_type: Opcional — filtrar por tipo de evento.
            from_timestamp: Opcional — solo eventos despues de este timestamp.
            to_timestamp: Opcional — solo eventos antes de este timestamp.

        Returns:
            Lista de EventRecord filtrados, ordenados por sequence.
        """
        all_events = EventStore._load_log(log_path)

        if event_type:
            all_events = [e for e in all_events if e.event_type == event_type]
        if from_timestamp:
            all_events = [e for e in all_events if e.timestamp >= from_timestamp]
        if to_timestamp:
            all_events = [e for e in all_events if e.timestamp <= to_timestamp]

        return sorted(all_events, key=lambda e: e.sequence)

    @staticmethod
    def count(log_path: str) -> int:
        """Cuenta el total de eventos en el log.

        Args:
            log_path: Ruta al archivo event_log.json.

        Returns:
            Numero total de eventos.
        """
        return len(EventStore._load_log(log_path))

    @staticmethod
    def log_exists(log_path: str) -> bool:
        """Verifica si existe un event log.

        Args:
            log_path: Ruta al archivo event_log.json.

        Returns:
            True si el archivo existe y no es un .tmp residual.
        """
        return os.path.isfile(log_path) and not log_path.endswith(".tmp")

    @staticmethod
    def _load_log(log_path: str) -> list[EventRecord]:
        """Carga todos los eventos desde el archivo de log.

        Args:
            log_path: Ruta al archivo event_log.json.

        Returns:
            Lista de EventRecord. Vacia si el archivo no existe.
        """
        if not os.path.isfile(log_path):
            return []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
            events = []
            for i, item in enumerate(raw_list):
                if isinstance(item, dict):
                    events.append(EventRecord(
                        event_type=item.get("type", "unknown"),
                        timestamp=item.get("timestamp", ""),
                        data=dict(item.get("data", {})),
                        sequence=item.get("sequence", i + 1),
                    ))
            return events
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return []

    @staticmethod
    def _now() -> str:
        """Timestamp ISO 8601 actual."""
        return datetime.now(timezone.utc).isoformat()
