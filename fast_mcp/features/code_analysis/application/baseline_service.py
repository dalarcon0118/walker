"""baseline_service.py — foto del estado (punto A) y diff contra el presente.

El workflow se plantea como control de vuelo: dónde estoy, a qué afecta el
cambio, cómo llego. Para eso hace falta un punto A comparable, y no alcanza con
tests: son UNA dimensión y encima tardía.

Este servicio captura el estado por scope en las dimensiones que el CPG ya
conoce —estructura, relaciones, firmas, complejidad, snapshot de archivos— y
marca explícitamente las que hoy no puede computar (convenciones sin CKG
inyectado, defectos con el DSL roto) en vez de devolver un cero que parece un
dato.

Persistencia: `<data_dir>/baselines/<label>.json` (un archivo por foto, legible
y diffeable).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from fast_mcp.features.code_analysis.domain.graph import FUNCTION_TYPES

_BASELINES_DIR = "baselines"
_HOT_CYCLO = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", text.strip()).strip("-")
    return cleaned[:80] or "snapshot"


def _norm_scope(scope: str) -> str:
    return (scope or "").replace(os.sep, "/").strip("/")


class BaselineService:
    """Captura y compara el estado del CPG para un scope."""

    def __init__(self, chain_dir: str) -> None:
        self._chain_dir = chain_dir

    # ── rutas ────────────────────────────────────────────────────────────

    def _paths(self):
        from fast_mcp.shared_kernel.project_support import ProjectService

        return ProjectService.resolve_paths(self._chain_dir)

    def _dir(self) -> str:
        path = os.path.join(str(self._paths().data_dir), _BASELINES_DIR)
        os.makedirs(path, exist_ok=True)
        return path

    def _file(self, label: str) -> str:
        return os.path.join(self._dir(), f"{_slug(label)}.json")

    def list_labels(self) -> list[str]:
        return sorted(
            os.path.splitext(f)[0] for f in os.listdir(self._dir()) if f.endswith(".json")
        )

    # ── captura ──────────────────────────────────────────────────────────

    def capture(self, scope: str = "", label: str = "", light: bool = True) -> dict:
        """Foto del estado. `label` vacío = sólo se devuelve, no se persiste."""
        scope_rel = _norm_scope(scope)
        graph = self._load_graph()

        nodes = [
            n for n in graph.nodes.values()
            if not scope_rel or scope_rel in (getattr(n, "path", "") or "").replace(os.sep, "/")
        ]

        structure = self._structure(nodes)
        relations = self._relations(graph, scope_rel)
        complexity = self._complexity(nodes)
        files = self._files(scope_rel, nodes)

        snapshot = {
            "snapshot_id": f"{_slug(label) if label else 'adhoc'}-{_now()}",
            "label": label,
            "project_id": self._paths().project_id,
            "scope": scope_rel,
            "taken_at": _now(),
            "structure": structure,
            "relations": relations,
            "complexity": complexity,
            "files": files,
            "conventions": self._conventions(scope_rel),
            "defects": self._defects(scope_rel),
        }
        # La respuesta no lleva el mapa completo de archivos (536 entradas =
        # ~50KB): va al archivo persistido. Se devuelve una muestra.
        if light and isinstance(snapshot.get("files"), dict) and snapshot["files"].get("entries"):
            entries = snapshot["files"].pop("entries")
            snapshot["files"]["entries_sample"] = dict(list(sorted(entries.items()))[:5])
        snapshot["dimensions"] = {
            k: (v.get("status") if isinstance(v, dict) and v.get("status") in ("unavailable", "partial") else "ok")
            for k, v in snapshot.items()
            if k in ("structure", "relations", "complexity", "files", "conventions", "defects")
        }
        if label:
            path = self._file(label)
            persisted = dict(snapshot)
            if persisted.get("files", {}).get("entries_sample") is not None:
                persisted["files"] = {k: v for k, v in persisted["files"].items() if k != "entries_sample"}
                persisted["files"]["entries"] = entries
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(persisted, fh, ensure_ascii=False, indent=2)
            snapshot["path"] = path
        return snapshot

    def _load_graph(self):
        from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase

        paths = self._paths()
        return QueryGraphUseCase(
            chain_dir=self._chain_dir,
            project_id=paths.project_id,
            data_dir=str(paths.data_dir) if paths.data_dir else None,
        ).graph

    @staticmethod
    def _structure(nodes: list) -> dict:
        by_type: dict[str, int] = {}
        signatures = 0
        for n in nodes:
            ntype = getattr(n, "type", "") or "unknown"
            by_type[ntype] = by_type.get(ntype, 0) + 1
            if getattr(n, "signature", ""):
                signatures += 1
        return {
            "nodes": len(nodes),
            "files": by_type.get("file", 0),
            "functions": sum(c for t, c in by_type.items() if t in FUNCTION_TYPES),
            "classes": by_type.get("class", 0),
            "signatures": signatures,
            "by_type": by_type,
        }

    @staticmethod
    def _relations(graph, scope_rel: str) -> dict:
        calls = imports = 0
        for edge in getattr(graph, "edges", []):
            etype = (getattr(edge, "type", "") or "").upper()
            if etype not in ("CALLS", "IMPORTS"):
                continue
            if not scope_rel:
                calls += etype == "CALLS"
                imports += etype == "IMPORTS"
                continue
            src = graph.nodes.get(getattr(edge, "from_id", ""))
            dst = graph.nodes.get(getattr(edge, "to_id", ""))
            src_in = scope_rel in (getattr(src, "path", "") or "").replace(os.sep, "/")
            dst_in = scope_rel in (getattr(dst, "path", "") or "").replace(os.sep, "/")
            if src_in or dst_in:
                calls += etype == "CALLS"
                imports += etype == "IMPORTS"
        return {"calls": calls, "imports": imports}

    @staticmethod
    def _complexity(nodes: list) -> dict:
        callables = [n for n in nodes if getattr(n, "type", "") in FUNCTION_TYPES]
        total = len(callables)
        values = [
            getattr(n, "cyclo", None) for n in callables
        ]
        values = [v for v in values if isinstance(v, int) and v > 0]
        if not values:
            # P1.2: cobertura de extractor, no índice vacío. Un scope con
            # funciones y cero cyclo es `partial`, no `unavailable`: decir
            # "reindexá" manda al trabajo equivocado (reindexar no agrega cyclo
            # si el adapter del lenguaje no lo produce). No se nombra un
            # lenguaje: walker reporta cobertura, cada adapter la llena.
            if total:
                return {
                    "status": "partial",
                    "known": 0,
                    "total": total,
                    "languages": sorted(
                        {
                            os.path.splitext(str(getattr(n, "path", "") or ""))[1]
                            or "(sin extensión)"
                            for n in callables
                        }
                    ),
                    "reason": (
                        "el adapter del lenguaje no produce cyclo para este "
                        "scope: es cobertura del extractor, no un índice vacío"
                    ),
                }
            return {"status": "unavailable", "reason": "sin funciones en el scope"}
        top = sorted(
            (
                {"name": getattr(n, "name", ""), "cyclo": getattr(n, "cyclo"),
                 "file": getattr(n, "path", "")}
                for n in nodes
                if getattr(n, "type", "") in FUNCTION_TYPES and (getattr(n, "cyclo", None) or 0) > 0
            ),
            key=lambda x: -x["cyclo"],
        )[:10]
        return {
            "measured": len(values),
            "avg": round(sum(values) / len(values), 2),
            "max": max(values),
            "above_10": sum(1 for v in values if v > _HOT_CYCLO),
            "known": len(values),
            "total": total,
            "status": "full" if len(values) == total else "partial",
            "top": top,
        }

    def _files(self, scope_rel: str, nodes: list | None = None) -> dict:
        """Mapa archivo → size:mtime del scope.

        Base: `.file_snapshot.json` (índice incremental). Pero ese archivo NO se
        refresca en cada build —el CPG sí—, así que un archivo nuevo existía en
        el grafo y no en el mapa: el diff reportaba 0 archivos agregados aunque
        la estructura cambiara. Se unen los nodos `file` del CPG para que el
        conjunto de archivos salga del grafo, y size:mtime del snapshot cuando
        esté.
        """
        paths = self._paths()
        snap_path = os.path.join(str(paths.data_dir), ".file_snapshot.json")
        data: dict[str, str] = {}
        if os.path.isfile(snap_path):
            try:
                with open(snap_path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                data = {}

        # Rutas RELATIVAS al root: el CPG guarda absolutas y `.file_snapshot.json`
        # relativas; mezclarlas sin normalizar duplicaba cada archivo.
        root = str(getattr(paths, "project_root", "") or "")
        snap_by_rel = {k.replace(os.sep, "/"): v for k, v in data.items()}
        graph_files: set[str] = set()
        for n in nodes or []:
            if getattr(n, "type", "") != "file" or not getattr(n, "path", ""):
                continue
            raw = getattr(n, "path", "")
            if root and os.path.isabs(raw):
                try:
                    raw = os.path.relpath(raw, root)
                except ValueError:
                    pass
            graph_files.add(raw.replace(os.sep, "/"))

        names = set(graph_files)
        names.update(k for k in snap_by_rel if not scope_rel or scope_rel in k)
        entries = {name: snap_by_rel.get(name, "new") for name in sorted(names)}
        digest = hashlib.sha256(
            "\n".join(f"{k}:{v}" for k, v in sorted(entries.items())).encode("utf-8")
        ).hexdigest()[:16]
        return {"count": len(entries), "digest": digest, "entries": entries}

    def _conventions(self, scope_rel: str) -> dict:
        try:
            from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
                CodeAnalysisService,
            )

            report = CodeAnalysisService(self._chain_dir).check_conventions()
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}

        errors = report.get("errors") or []
        if errors:
            return {"status": "unavailable", "reason": "; ".join(map(str, errors))[:200]}
        violations = report.get("violations") or []
        if scope_rel:
            violations = [
                v for v in violations
                if scope_rel in str(v.get("file", "")).replace(os.sep, "/")
            ]
        return {"rules_checked": report.get("rules_checked", 0), "violations": len(violations)}

    def _defects(self, scope_rel: str) -> dict:
        try:
            from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
                CodeAnalysisService,
            )

            svc = CodeAnalysisService(self._chain_dir)
            dead = svc.detect_unused_functions(file_filter=scope_rel or "")
            taint = svc.find_bugs(bug_type="taint_paths", file_filter=scope_rel or "")
        except Exception as exc:  # noqa: BLE001
            return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
        return {
            "status": "partial",
            "dead_code": dead.get("total", 0),
            "taint_paths": taint.get("total", 0),
            "reason": "find_bugs ignora bug_type (dead_branches y missing_returns devuelven "
                      "el mismo slice del grafo) y dead_code suele dar 0; tomar como indicio",
        }

    # ── diff ─────────────────────────────────────────────────────────────

    def diff(self, label: str, scope: str = "") -> dict:
        base = self.load(label)
        if base is None:
            return {"error": f"baseline '{label}' no existe", "available": self.list_labels()}
        now = self.capture(scope=scope or base.get("scope", ""), light=False)
        return self._compare(base, now)

    def load(self, label: str) -> dict | None:
        path = self._file(label)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _compare(base: dict, now: dict) -> dict:
        changes: dict[str, Any] = {}

        def delta(section: str, keys: tuple[str, ...]) -> dict:
            b = base.get(section) or {}
            n = now.get(section) or {}
            return {
                k: {"before": b.get(k), "after": n.get(k)}
                for k in keys
                if b.get(k) != n.get(k)
            }

        changes["structure"] = delta("structure", ("nodes", "files", "functions", "classes", "signatures"))
        changes["relations"] = delta("relations", ("calls", "imports"))

        b_cx = base.get("complexity") or {}
        n_cx = now.get("complexity") or {}
        if b_cx.get("status") == "unavailable" or n_cx.get("status") == "unavailable":
            changes["complexity"] = {"status": "unavailable"}
        else:
            changes["complexity"] = {
                k: {"before": b_cx.get(k), "after": n_cx.get(k)}
                for k in ("avg", "max", "above_10")
                if b_cx.get(k) != n_cx.get(k)
            }
            changes["complexity"]["new_hotspots"] = _new_hotspots(b_cx, n_cx)

        b_files = (base.get("files") or {}).get("entries") or {}
        n_files = (now.get("files") or {}).get("entries") or {}
        added = sorted(set(n_files) - set(b_files))
        removed = sorted(set(b_files) - set(n_files))
        modified = sorted(k for k in set(b_files) & set(n_files) if b_files[k] != n_files[k])
        changes["files"] = {
            "added": added[:50],
            "removed": removed[:50],
            "modified": modified[:50],
            "counts": {"added": len(added), "removed": len(removed), "modified": len(modified)},
        }

        changes["conventions"] = delta("conventions", ("violations", "rules_checked"))
        changes["defects"] = delta("defects", ("dead_code", "taint_paths"))

        touched = (
            len(added) + len(removed) + len(modified)
            + sum(1 for v in changes["structure"].values())
            + sum(1 for v in changes["relations"].values())
        )
        return {
            "base": {"label": base.get("label"), "taken_at": base.get("taken_at"), "scope": base.get("scope")},
            "now": {"taken_at": now.get("taken_at"), "scope": now.get("scope")},
            "changes": changes,
            "summary": "sin cambios" if touched == 0 else f"{touched} cambio(s) en el scope",
        }


def _new_hotspots(base_cx: dict, now_cx: dict) -> list[dict]:
    """Puntos calientes nuevos o que empeoraron.

    Clave (nombre, archivo): hay varias funciones `main` en el proyecto y
    comparar sólo por nombre reportaba saltos falsos (12 → 80 entre dos mains
    distintas).
    """
    def key(h: dict) -> tuple[str, str]:
        return (str(h.get("name", "")), str(h.get("file", "")))

    before = {key(h): h.get("cyclo") for h in (base_cx.get("top") or [])}
    out = []
    for h in (now_cx.get("top") or []):
        prev = before.get(key(h))
        if prev is not None and (h.get("cyclo") or 0) > prev:
            out.append({"name": h.get("name"), "file": h.get("file"),
                        "before": prev, "after": h.get("cyclo")})
        elif prev is None and (h.get("cyclo") or 0) > _HOT_CYCLO:
            out.append({"name": h.get("name"), "file": h.get("file"),
                        "before": None, "after": h.get("cyclo")})
    return out
