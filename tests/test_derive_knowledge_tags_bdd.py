"""tests/test_derive_knowledge_tags_bdd.py — barrido aditivo de tags del CKG.

El barrido NO reescribe `type` (un agente que filtre `type: decision` dejaría
de ver los ADR); agrega tags derivados por contenido para que el collector
encuentre `pattern`/`quality` en proyectos cuyo conocimiento está tipado como
decision/implementation_state/verification.

Regresión medida 2026-11-16: backend-microservices tenía 0 nodos de tipo
pattern/rule/convention, así que `project_patterns` llegaba vacío al prompt.
"""

from __future__ import annotations

from scripts.derive_knowledge_tags import derive_tags


def test_adr_por_prefijo_es_architecture():
    tags = derive_tags("ADR-018 (DRAFT): la frontera del agregado Play...")
    assert "architecture" in tags


def test_invariante_por_prefijo_es_quality():
    assert "quality" in derive_tags("INV-BE-015 formalizado: la jugada es un comprobante")
    assert "quality" in derive_tags("Regla: nunca se expone el PIN crudo")


def test_value_object_es_pattern():
    tags = derive_tags("capa de dominio pura en bet/domain/ — 4 Value Objects")
    assert "pattern" in tags
    assert "architecture" in tags, "menciona 'capa de dominio'"


def test_texto_neutro_no_se_etiqueta():
    assert derive_tags("Se actualizó el README con la fecha nueva") == set()


def test_no_etiqueta_por_mencion_sino_por_prefijo():
    """Mencionar un ADR en medio del texto no lo vuelve architecture.

    Buscar la subcadena "adr-" etiquetaba al 93% de los docs y el tag dejaba
    de discriminar; por eso las reglas de prefijo miran el INICIO.
    """
    tags = derive_tags("Se corrigió el test que referenciaba ADR-018 en un comentario")
    assert "architecture" not in tags
