"""Fixtures globales de la suite de tests.

Tras la eliminación de mcp/ (ADR-WALKER-071 Fase 4), todo el código legacy
(session_store, AppContainer, tool_catalog, etc.) fue removido. Este conftest
está vacío — los fixtures viven en cada archivo de test o en conftest.py de
cada directorio.
"""
from __future__ import annotations
