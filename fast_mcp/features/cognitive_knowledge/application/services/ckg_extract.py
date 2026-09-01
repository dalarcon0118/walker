"""application/services/ckg_extract.py — CkgExtractService.

CPG cross-feature knowledge extraction: analyzes code structure via CPG,
cross-references with existing CKG facts, and produces extraction payloads.

SRP: this service handles extraction only. Write operations are in
ckg_write.py, read in ckg_read.py.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful

from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import CkgReadService

logger = logging.getLogger(__name__)

# ── extract_knowledge questionnaire (ADR-051 G4) ──────────────────────

QUESTIONNAIRE: dict = {
    "instructions": (
        "You received the full code structure via CPG. "
        "Analyze it and answer the following questions. "
        "Your answers will be saved in the CKG (Knowledge Graph) "
        "so they are available in future sprints. "
        "Use 'walker <project>/documenting_knowledge' to save each answer. "
        "IMPORTANT: Do NOT save knowledge that already exists in CKG."
    ),
    "questions": [
        {
            "id": "architecture_patterns",
            "question": "What architecture patterns do you identify?",
            "examples": [
                "Clean Architecture / DDD / Hexagonal",
                "MVC / MVP / MVVM",
                "Microservices / Modular monolith",
                "Event-driven / CQRS",
            ],
            "ckg_type": "decision",
            "scope": "architecture",
        },
        {
            "id": "coding_conventions",
            "question": "What coding conventions do you observe?",
            "examples": [
                "Naming conventions (snake_case, camelCase, PascalCase)",
                "File structure (one file per class, per function)",
                "Use of type hints / docstrings",
                "Import style (absolute, relative)",
            ],
            "ckg_type": "convention",
            "scope": "coding-style",
        },
        {
            "id": "design_decisions",
            "question": "What design decisions are evident from the code?",
            "examples": [
                "Why X technology was used over Y",
                "How dependency injection is handled",
                "Testing strategy",
                "Error / result handling",
            ],
            "ckg_type": "decision",
            "scope": "design",
        },
        {
            "id": "key_components",
            "question": "What are the key components and their relationships?",
            "examples": [
                "Main modules and their responsibilities",
                "Data flow between components",
                "Module dependencies",
                "Extension / plugin points",
            ],
            "ckg_type": "component",
            "scope": "architecture",
        },
        {
            "id": "business_rules",
            "question": "What implicit business rules do you detect?",
            "examples": [
                "Security validations",
                "Authentication / authorization rules",
                "Business logic in services vs entities",
                "State and transition management",
            ],
            "ckg_type": "rule",
            "scope": "business",
        },
        {
            "id": "data_flow",
            "question": "How does data flow through the system?",
            "examples": [
                "Layers: API -> UseCase -> Repository -> DB",
                "Events: Producer -> Broker -> Consumer",
                "Data transformations",
                "Caching strategy",
            ],
            "ckg_type": "pattern",
            "scope": "data-flow",
        },
    ],
}


class CkgExtractService:
    """CPG cross-feature knowledge extraction."""

    @staticmethod
    def build_extract_payload(cpg: Any, scope: str, chain_dir: str) -> dict:
        """Code analysis for populating the CKG."""
        analysis = CkgExtractService._query_cpg_analysis(cpg, scope)
        existing_facts = CkgExtractService._query_existing_ckg(chain_dir, analysis)
        stats = CkgExtractService._query_project_stats(cpg, analysis, existing_facts)
        return {
            "stats": stats,
            "existing_ckg_facts_count": len(existing_facts),
            "existing_ckg_facts": existing_facts[:20],
            "cpg_analysis": analysis,
            "questionnaire": QUESTIONNAIRE,
        }

    @staticmethod
    def _query_cpg_analysis(cpg: Any, scope: str) -> dict:
        """Query CPG by type via port grep (fallback with log)."""
        from returns.result import safe

        @safe
        def _core() -> dict:
            analysis = {"functions": [], "classes": [], "files": [], "modules": []}
            if scope in ("all", "functions"):
                for m in (cpg.grep("type:function") or {}).get("matches", []):
                    analysis["functions"].append(
                        {"name": m.get("name", ""), "file": m.get("file", ""), "line": m.get("line", 0)}
                    )
                for m in (cpg.grep("type:method") or {}).get("matches", []):
                    analysis["functions"].append(
                        {"name": m.get("name", ""), "file": m.get("file", ""), "line": m.get("line", 0), "method": True}
                    )
            if scope in ("all", "classes"):
                for m in (cpg.grep("type:class") or {}).get("matches", []):
                    analysis["classes"].append(
                        {"name": m.get("name", ""), "file": m.get("file", ""), "line": m.get("line", 0)}
                    )
            if scope in ("all", "files"):
                for m in (cpg.grep("type:file") or {}).get("matches", []):
                    analysis["files"].append({"path": m.get("file", m.get("name", ""))})
                for m in (cpg.grep("type:module") or {}).get("matches", []):
                    analysis["modules"].append({"name": m.get("name", ""), "file": m.get("file", "")})
            return analysis

        result = _core()
        if is_successful(result):
            return result.value_or({"functions": [], "classes": [], "files": [], "modules": []})
        error = result.failure()
        logger.warning("extract_knowledge: CPG analysis failed (%s: %s)", type(error).__name__, error)
        return {"functions": [], "classes": [], "files": [], "modules": []}

    @staticmethod
    def _query_existing_ckg(chain_dir: str, analysis: dict) -> list[dict]:
        """Query current CKG to avoid duplicates (fallback with log)."""
        from returns.result import safe

        @safe
        def _core() -> list[dict]:
            return [
                {"type": s.get("type", ""), "statement": s.get("statement", ""), "scope": s.get("scope", "")}
                for s in CkgReadService.query_all(chain_dir)
            ]

        result = _core()
        if is_successful(result):
            return result.value_or([])
        error = result.failure()
        analysis["_error_ckg"] = str(error)
        logger.warning("extract_knowledge: CKG query failed (%s: %s)", type(error).__name__, error)
        return []

    @staticmethod
    def _query_project_stats(cpg: Any, analysis: dict, existing_facts: list) -> dict:
        """Project totals via port grep (fallback to local count)."""
        from returns.result import safe

        @safe
        def _total(ntype: str) -> int:
            return int((cpg.grep(f"type:{ntype}") or {}).get("total", 0))

        counts = {}
        for ntype in ("function", "class", "file", "module"):
            fallback = len(analysis.get(f"{ntype}s", []))
            r = _total(ntype)
            counts[ntype] = r.value_or(fallback) if is_successful(r) else fallback
        return {
            "total_functions": counts["function"],
            "total_classes": counts["class"],
            "total_files": counts["file"],
            "total_modules": counts["module"],
            "existing_ckg_facts": len(existing_facts),
        }
