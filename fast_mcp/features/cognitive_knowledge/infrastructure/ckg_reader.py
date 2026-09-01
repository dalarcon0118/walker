"""infrastructure/ckg_reader.py — CKG reader via port.

SSOT: only cognitive_knowledge reads the CKG, ALWAYS through the port
(CA-9, ADR-WALKER-063): no direct reads of `.json.gz`.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def read_ckg_via_port(projects_data_dir: str) -> dict:
    """Read CKG data via the port (backend by config).

    Returns:
        dict with "nodes" key containing the list of CKG nodes.
    """
    try:
        from fast_mcp.features.cognitive_knowledge import ckg_service

        ckg = ckg_service(projects_data_dir)
        if ckg is None:
            return {"nodes": []}
        nodes = ckg.query({"size": 100000}) or []
        return {"nodes": nodes}
    except Exception as e:
        logger.warning("ckg_reader: failed to read CKG (%s: %s)", type(e).__name__, e)
        return {"nodes": []}
