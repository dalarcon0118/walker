"""use_cases/research_topic.py — ResearchTopicUseCase.

Investiga un tema en la web y lo guarda en el CKG.

Escribe via CodeKnowledgeGraph (formato canonico del indice 'ckg' de
CognitiveGraph), igual que save_to_ckg/ckg_analyze. SSOT: NUNCA usar el
KnowledgeGraph legacy (v3.0) que escribe en el mismo ckg.json.gz con
otro formato y corrompe el indice.
"""
from __future__ import annotations

import json

from returns.result import Result, safe

from features.project import ProjectService


class ResearchTopicUseCase:
    """Research a topic on web and save to CKG."""

    @staticmethod
    def execute(chain_dir: str, topic: str, query: str = "") -> Result[dict, Exception]:
        import urllib.request
        import urllib.parse
        from features.cognitive_knowledge import ckg_service

        @safe
        def _research() -> dict:
            paths = ProjectService.resolve_paths(chain_dir)
            query_final = query or f"{topic} guide tutorial"
            try:
                search_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query_final)}&format=json&no_html=1"
                try:
                    with urllib.request.urlopen(search_url, timeout=15) as resp:
                        web_data = json.loads(resp.read())
                    abstract = web_data.get("AbstractText", "")
                    source_url = web_data.get(
                        "AbstractURL", f"https://duckduckgo.com/?q={query_final}"
                    )
                except Exception:
                    abstract = f"Research on {topic}"
                    source_url = ""
                node = {
                    "label": (
                        f"{topic}: {abstract[:200] if abstract else 'research completed'}"
                    ),
                    "type": "component",
                    "subject": topic.lower(),
                    "query_hints": [topic.lower(), query_final.lower()],
                    "tags": ["external", topic.lower().replace(" ", "-")],
                    "metadata": {"source": "web", "url": source_url},
                    "author": "opencode",
                    "source": "web-research",
                }
                ckg = ckg_service(str(paths.project_root))
                result = ckg.save_nodes([node])
                node_ids = result.get("node_ids", [])
                return {
                    "topic": topic,
                    "abstract": abstract[:200],
                    "source": source_url,
                    "node_id": node_ids[0] if node_ids else "",
                    "saved": bool(result.get("ok", False)),
                }
            except Exception:
                raise

        return _research()
