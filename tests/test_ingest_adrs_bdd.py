"""test_ingest_adrs_bdd.py — ingesta de ADRs al CKG.

Problema medido (2026-11-17): el proyecto `walker` estaba registrado, tenía 42
ADRs en `docs/` y **0 nodos de conocimiento**. `_seed_knowledge` del harness
copiaba cero al sandbox y la sección CONOCIMIENTO DEL PROYECTO del prompt se
renderizaba vacía.

Los ids salen del filename (determinísticos), así que re-correr actualiza en
vez de duplicar; y el estado se normaliza a 6 valores porque los ADRs mezclan
español e inglés ("Aprobada", "In Progress", "Implemented").
"""

from __future__ import annotations

from pathlib import Path

from scripts.ingest_adrs import _status_tag, build_nodes, parse_adr

_ADR = """# ADR-WALKER-042: Workflow Block Resolution

> **Status:** In Progress — F1-F8 + wiring del runtime
> **Fecha:** 2026-08-07

---

## Contexto

El motor de workflow puede quedar bloqueado cuando falta un campo requerido o
falla un gate de validación, y sin estrategia de resolución el estado queda
colgado sin señal accionable para el operador.
"""


def _write(tmp_path: Path, name: str, body: str = _ADR) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_adr_extrae_titulo_estado_y_codigo(tmp_path):
    node = parse_adr(_write(tmp_path, "ADR-WALKER-042.md"))

    assert node["id"] == "adr-walker-042"
    assert node["label"].startswith("ADR-WALKER-042: Workflow Block Resolution")
    assert node["type"] == "design-decision"
    assert node["metadata"]["adr"] == "ADR-WALKER-042"
    assert node["metadata"]["date"] == "2026-08-07"
    assert "architecture" in node["tags"]
    assert "status:in-progress" in node["tags"]


def test_parse_adr_justificacion_es_prosa_no_metadata(tmp_path):
    """La descripción no debe ser la cabecera `**Status:** ...` (bug medido)."""
    node = parse_adr(_write(tmp_path, "ADR-WALKER-042.md"))

    assert "Status" not in node["description"]
    assert "motor de workflow" in node["description"]


def test_ids_distintos_para_dos_adr_con_el_mismo_numero(tmp_path):
    """Hay dos ADR-WALKER-018: el id sale del filename, no del número."""
    a_body = "# ADR-WALKER-018: Context Gates\n\n> **Status:** Accepted\n\nLos gates de contexto resuelven qué memoria inyectar antes de ejecutar cualquier herramienta del workflow.\n"
    b_body = "# ADR-WALKER-018: Event Tracing\n\n> **Status:** Accepted\n\nUna prosa distinta que explica el tracing de eventos del walker.\n"
    a = parse_adr(_write(tmp_path, "ADR-WALKER-018-context-gates.md", a_body))
    b = parse_adr(_write(tmp_path, "ADR-WALKER-018-event-tracing.md", b_body))

    assert a["id"] != b["id"]
    assert a["metadata"]["adr"] == b["metadata"]["adr"] == "ADR-WALKER-018"


def test_build_nodes_ignora_archivos_que_no_son_adr(tmp_path):
    _write(tmp_path, "ADR-WALKER-042.md")
    _write(tmp_path, "README.md", "# no es un ADR")
    _write(tmp_path, "teoria.md", "# teoría")

    nodes = build_nodes(tmp_path)
    assert [n["id"] for n in nodes] == ["adr-walker-042"]


def test_status_tag_normaliza_espanol_e_ingles():
    assert _status_tag("In Progress — F1-F8") == "status:in-progress"
    assert _status_tag("Aprobada") == "status:accepted"
    assert _status_tag("Implemented") == "status:implemented"
    assert _status_tag("Propuesta (pendiente)") == "status:proposed"


def test_status_tag_desconocido_no_etiqueta():
    """Un estado fuera del vocabulario no debe inventar un tag consultable."""
    assert _status_tag("En revisión por el PO") == ""
    assert _status_tag("") == ""
