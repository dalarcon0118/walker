"""
_walker/events.py — Event System for Workflow Navigation.

SRP: Unica responsabilidad = gestionar eventos de dominio.
SSOT: La configuracion de triggers (on: blocks) vive en workflow.json.

Arquitectura:
  EventRegistry   → Lee config on:{} de workflow.json
  EventDispatcher → Recibe DomainEvent, busca handlers registrados, ejecuta
  EventLog        → Persiste eventos en event_log.json (append-only)
  EventStore      → Event Sourcing: append_event, replay, query (via event_store.py)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .workflow_engine.domain.models import DomainEvent
from .event_store import EventRecord
from .event_store import EventStore as _EventStore


# ── EventRegistry ──────────────────────────────────────────────────────────


class EventRegistry:
    """Registro de eventos basado en la configuracion on:{} de workflow.json.

    SRP: Unica responsabilidad = leer y cachear la configuracion de triggers.
    SSOT: workflow.json es la unica fuente de verdad para los triggers.

    Los bloques on: se definen en workflow.json como:
    {
      "nodes": {
        "ANALYZE": {
          "on": {
            "data_submitted": "python3 handler.py",
            "state_transitioned": "notify.py --event state_transitioned"
          }
        }
      }
    }
    """

    def __init__(self) -> None:
        self._handlers: dict[str, dict[str, str]] = {}
        self._loaded = False

    @staticmethod
    def from_workflow_raw(raw_workflow: dict) -> EventRegistry:
        """Crea un EventRegistry desde workflow.json.

        Lee on:{} tanto del nivel raiz como de nodos individuales.
        """
        registry = EventRegistry()

        # Leer on:{} del nivel raiz (formato workflow.v3 modular)
        root_on = raw_workflow.get("on", {})
        if isinstance(root_on, dict):
            registry.register_from_dict(root_on)

        # Leer on:{} de nodos individuales (backward compat)
        for node_id, node_raw in raw_workflow.get("nodes", {}).items():
            on_config = node_raw.get("on", {})
            if isinstance(on_config, dict):
                registry.register_from_dict(on_config)

        registry._loaded = True
        return registry

    def register(self, event_type: str, handler_cfg: str | dict) -> None:
        """Registra un handler para un tipo de evento.

        Args:
            event_type: Nombre del evento.
            handler_cfg: String "run:action", "transition:gate", o dict {"run":..., "transition":...}
        """
        if isinstance(handler_cfg, str):
            if handler_cfg.startswith("run:"):
                self._handlers[event_type] = {"type": "run", "action": handler_cfg[4:]}
            elif handler_cfg.startswith("transition:"):
                self._handlers[event_type] = {
                    "type": "transition",
                    "gate": handler_cfg[11:],
                }
        elif isinstance(handler_cfg, dict):
            entry = {}
            if "run" in handler_cfg:
                entry["type"] = "run"
                entry["action"] = handler_cfg["run"]
            if "transition" in handler_cfg:
                entry["type"] = "transition"
                entry["gate"] = handler_cfg["transition"]
            if entry:
                self._handlers[event_type] = entry

    def register_from_dict(self, on_config: dict) -> None:
        """Registra handlers desde un diccionario on:{}.

        Args:
            on_config: Dict con configuracion de eventos.
        """
        for event_type, handler_cfg in on_config.items():
            self.register(event_type, handler_cfg)

    def unregister(self, event_type: str) -> None:
        self._handlers.pop(event_type, None)

    def get_handler(self, event_type: str) -> dict | None:
        """Retorna el handler para un tipo de evento.

        Returns:
            Dict con {"type": "run"|"transition", "action"/"gate": str} o None.
        """
        return self._handlers.get(event_type)

    def has_handler(self, event_type: str) -> bool:
        return event_type in self._handlers

    @property
    def registered_events(self) -> list[str]:
        return list(self._handlers.keys())

    def clear(self) -> None:
        self._handlers.clear()
        self._loaded = False


# ── EventDispatcher ────────────────────────────────────────────────────────


class EventDispatcher:
    """Dispatcher de eventos de dominio.

    SRP: Unica responsabilidad = recibir eventos y ejecutar handlers.
    Soporta:
      - run: ejecuta una accion via TaskRegistry
      - transition: aplica una transicion via OrchestrationService

    Flujo:
      1. Recibe un DomainEvent
      2. Busca handler registrado en EventRegistry
      3. Si es "run": ejecuta la accion via TaskRegistry
      4. Si es "transition": aplica el gate via OrchestrationService
      5. Retorna resultado de la ejecucion
    """

    def __init__(
        self,
        registry: EventRegistry,
        orchestrator: Any = None,
    ):
        self._registry = registry
        self._orchestrator = orchestrator
        self._dispatch_log: list[dict[str, Any]] = []

    def dispatch(
        self,
        event: DomainEvent,
        chain_dir: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispacha un evento a su handler registrado.

        Args:
            event: El evento de dominio a disparar.
            chain_dir: Directorio del sprint (para ejecucion local).
            task_id: ID de la tarea actual.

        Returns:
            Dict con resultado de la ejecucion.
        """
        handler_cfg = self._registry.get_handler(event.event_type)
        if not handler_cfg:
            result: dict[str, Any] = {
                "dispatched": False,
                "handler": None,
                "result": None,
                "error": None,
            }
            self._dispatch_log.append(
                {
                    "event": event.event_type,
                    "dispatched": False,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            return result

        handler_type = handler_cfg.get("type", "")
        try:
            if handler_type == "run":
                action = handler_cfg.get("action", "")
                if self._orchestrator and action:
                    from .workflow_engine.domain.models import ExecutionContext

                    ctx = ExecutionContext(
                        session_id=chain_dir or "",
                        chain_dir=chain_dir or "",
                        trace_id=event.event_id or event.event_type,
                        span_id="event",
                    )
                    result_data = self._orchestrator.submit(ctx, fields={})
                    dispatch_result = {
                        "dispatched": True,
                        "handler": f"run:{action}",
                        "result": result_data,
                        "error": None,
                    }
                else:
                    dispatch_result = {
                        "dispatched": False,
                        "handler": f"run:{action}",
                        "result": None,
                        "error": "No Orchestrator available",
                    }

            elif handler_type == "transition":
                gate = handler_cfg.get("gate", "")
                if self._orchestrator and gate:
                    from .workflow_engine.domain.models import ExecutionContext

                    ctx = ExecutionContext(
                        session_id=chain_dir or "",
                        chain_dir=chain_dir or "",
                        trace_id=event.event_id or event.event_type,
                        span_id="event",
                    )
                    result_data = self._orchestrator.transition(ctx, gate)
                    dispatch_result = {
                        "dispatched": True,
                        "handler": f"transition:{gate}",
                        "result": result_data,
                        "error": None,
                    }
                else:
                    dispatch_result = {
                        "dispatched": False,
                        "handler": f"transition:{gate}",
                        "result": None,
                        "error": "No Orchestrator available",
                    }
            else:
                dispatch_result = {
                    "dispatched": False,
                    "handler": str(handler_cfg),
                    "result": None,
                    "error": f"Unknown handler type: {handler_type}",
                }

            self._dispatch_log.append(
                {
                    "event": event.event_type,
                    "handler": str(handler_cfg),
                    "dispatched": dispatch_result["dispatched"],
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            return dispatch_result

        except Exception as e:
            error_entry = {
                "dispatched": False,
                "handler": str(handler_cfg),
                "result": None,
                "error": str(e),
            }
            self._dispatch_log.append(
                {
                    "event": event.event_type,
                    "handler": handler_script,
                    "error": str(e),
                    "dispatched": False,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
            return error_entry

    def dispatch_many(
        self,
        events: list[DomainEvent],
        chain_dir: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Dispacha multiples eventos secuencialmente.

        Args:
            events: Lista de eventos a disparar.
            chain_dir: Directorio del sprint.
            task_id: ID de la tarea actual.

        Returns:
            Lista de resultados, uno por evento.
        """
        return [self.dispatch(e, chain_dir, task_id) for e in events]

    @property
    def last_dispatches(self) -> list[dict[str, Any]]:
        """Ultimos N dispatches registrados."""
        return list(self._dispatch_log)

    def clear_log(self) -> None:
        self._dispatch_log.clear()


# ── EventLog ───────────────────────────────────────────────────────────────


class EventLog:
    """Persistencia append-only de eventos de dominio.

    SRP: Unica responsabilidad = escribir/leer event_log.json.
    SSOT: event_log.json es el unico almacenamiento de eventos.

    Formato:
      event_log.json es un array JSON de objetos DomainEvent.
      Cada append agrega un elemento al final (append-only).
      No se permite modificacion de eventos anteriores.
    """

    LOG_FILENAME = "event_log.json"

    def __init__(self, chain_dir: str | None = None):
        self._chain_dir = chain_dir
        self._events: list[DomainEvent] = []

    def set_chain_dir(self, chain_dir: str) -> None:
        """Establece el directorio del sprint para persistencia."""
        self._chain_dir = chain_dir

    @property
    def _log_path(self) -> str | None:
        if not self._chain_dir:
            return None
        return os.path.join(self._chain_dir, self.LOG_FILENAME)

    def append(self, event: DomainEvent) -> None:
        """Agrega un evento al log en memoria.

        No persiste automaticamente — llama a flush() para escribir a disco.
        """
        self._events.append(event)

    def append_and_flush(self, event: DomainEvent) -> None:
        """Agrega un evento y persiste inmediatamente."""
        self.append(event)
        self.flush()

    def flush(self) -> None:
        """Persiste todos los eventos en memoria al archivo event_log.json.

        Escritura atomica: escribe a .tmp y renombra.
        Si el archivo existente contiene campos adicionales como 'sequence'
        (escritos por EventStore.append_event()), se preservan.
        """
        path = self._log_path
        if not path:
            return

        # Leer eventos existentes (raw JSON para preservar campos extra como sequence)
        existing_raw = self._load_existing_raw()
        existing = self._load_existing()

        # Merge: los eventos nuevos tienen timestamp > ultimo existente
        existing_ts = {e.timestamp for e in existing if e.timestamp}
        new_events = [e for e in self._events if e.timestamp not in existing_ts]

        # Construir serialized preserving raw fields + new events
        # Usamos existing_raw para preservar campos como 'sequence'
        serialized = list(existing_raw)
        for e in new_events:
            serialized.append(
                {
                    "type": e.event_type,
                    "timestamp": e.timestamp,
                    "data": dict(e.data),
                }
            )

        # Escritura atomica
        tmp_path = path + ".tmp"
        os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
        with open(tmp_path, "w") as f:
            json.dump(serialized, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)

        # Limpiar buffer (ya persisti)
        self._events = [e for e in self._events if e.timestamp in existing_ts]

    def _load_existing_raw(self) -> list[dict]:
        """Carga eventos existentes como raw dicts (preserva campos extra).

        A diferencia de _load_existing() que crea DomainEvent objects,
        este metodo retorna los dicts originales del JSON para preservar
        campos como 'sequence' escritos por EventStore.append_event().
        """
        path = self._log_path
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path) as f:
                data = json.load(f)
            return [dict(item) for item in data if isinstance(item, dict)]
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return []

    def _load_existing(self) -> list[DomainEvent]:
        """Carga eventos existentes desde el archivo."""
        path = self._log_path
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path) as f:
                raw_list = json.load(f)
            return [
                DomainEvent(
                    event_type=item.get("type", "unknown"),
                    timestamp=item.get("timestamp", ""),
                    data=item.get("data", {}),
                )
                for item in raw_list
                if isinstance(item, dict)
            ]
        except (json.JSONDecodeError, FileNotFoundError, OSError):
            return []

    def replay(self, event_type: str | None = None) -> list[DomainEvent]:
        """Reconstruye eventos desde el log persistido.

        Args:
            event_type: Opcional — filtrar por tipo de evento.

        Returns:
            Lista de DomainEvent en orden cronologico.
        """
        events = self._load_existing()
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        return events

    def count(self, event_type: str | None = None) -> int:
        """Cuenta eventos en el log."""
        return len(self.replay(event_type))

    def to_snapshot_data(self) -> dict:
        """Retorna datos del event_log para incluir en un snapshot.

        Integracion con PersistenceService: cuando se toma un snapshot del estado,
        el event_log se incluye como parte del snapshot para garantizar
        que eventos y estado esten siempre sincronizados.

        Usa la misma logica de dedup que flush() para evitar duplicar
        eventos que ya estan persistidos en disco.

        Returns:
            Dict con:
            - event_count: int (total de eventos en el log)
            - last_event: str | None (timestamp del ultimo evento)
            - events: list[dict] (los eventos serializados)
        """
        existing = self._load_existing()
        existing_ts = {e.timestamp for e in existing if e.timestamp}
        new_events = [e for e in self._events if e.timestamp not in existing_ts]
        all_events = existing + new_events

        serialized = []
        for e in all_events:
            serialized.append(
                {
                    "type": e.event_type,
                    "timestamp": e.timestamp,
                    "data": dict(e.data),
                }
            )
        last_ts = serialized[-1]["timestamp"] if serialized else None
        return {
            "event_count": len(serialized),
            "last_event": last_ts,
            "events": serialized,
        }

    def append_to_store(self, event: DomainEvent) -> EventRecord:
        """Agrega un evento al EventStore (integracion EventLog -> EventStore).

        Convierte un DomainEvent a EventRecord y lo persiste via EventStore.
        Esto permite usar EventLog como interfaz de alto nivel mientras
        EventStore maneja el event sourcing (append-only, sequence, replay).

        Args:
            event: DomainEvent a persistir.

        Returns:
            EventRecord con sequence number asignado.
        """
        log_path = self._log_path
        if not log_path:
            # Sin chain_dir, solo guardamos en memoria
            self.append(event)
            return EventRecord(
                event_type=event.event_type,
                timestamp=event.timestamp,
                data=dict(event.data),
                sequence=len(self._events),
            )

        record = _EventStore.append_event(
            EventRecord(
                event_type=event.event_type,
                timestamp=event.timestamp or datetime.now(timezone.utc).isoformat(),
                data=dict(event.data),
            ),
            log_path,
        )
        return record

    def clear_persisted(self) -> None:
        """Limpia el archivo event_log.json (solo para testing)."""
        path = self._log_path
        if path and os.path.exists(path):
            os.remove(path)
        self._events.clear()
