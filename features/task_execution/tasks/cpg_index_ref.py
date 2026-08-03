"""Task: cpg_index_ref — indexa un repo externo como CPG de referencia."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput
from features.project import ProjectService


@dataclass(frozen=True)
class IndexRefInput(TaskInput):
    """Input validado de cpg_index_ref.

    url (required): repo remoto a clonar.
    name (required): nombre del índice de referencia.
    branch: rama (default main).
    depth: profundidad de clone (default 1).
    """

    url: str = ""
    name: str = ""
    branch: str = "main"
    depth: int = 1

    def _validate(self) -> str:
        if not self.url or not self.name:
            return "Requires --url y --name"
        if not isinstance(self.depth, int) or self.depth < 1:
            return "depth debe ser int >= 1"
        return ""


def cpg_index_ref(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    input_result = IndexRefInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "url/name requeridos")
    inp: IndexRefInput = input_result.data
    url = inp.url
    name = inp.name
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    branch = inp.branch
    depth = inp.depth

    paths = ProjectService.resolve_paths(chain_dir)
    data_dir = paths.data_dir
    out_path = os.path.join(data_dir, f"cpg.ref.{name}.json.gz")
    index_name = f"cpg-ref-{name}"

    tmpdir = tempfile.mkdtemp(prefix=f"cpg-ref-{name}-")
    repo_path = os.path.join(tmpdir, name)

    try:
        branches_to_try = [branch] if branch != "main" else ["main", "master"]
        cloned = False
        for b in branches_to_try:
            subprocess.run(["git", "clone", "--depth", str(depth), "--branch", b, "--single-branch", url, repo_path],
                           capture_output=True, text=True, timeout=120)
            if os.path.isdir(repo_path):
                branch = b; cloned = True; break
        if not cloned:
            return TaskResult.failure(f"Failed to clone {url} (probé ramas: {', '.join(branches_to_try)})")

        from features.code_analysis.infrastructure.ast_grep_adapter import run_outline, parse_outline
        results = run_outline([repo_path])
        if not results:
            return TaskResult.success(data={"message": "No se encontraron archivos parseables", "url": url})

        graph = parse_outline(results)
        data_out = graph.to_dict()
        stats = {"version": "2.0", "project_id": f"ref:{name}", "generated_by": f"cpg_index_ref/{url}",
                 "source_url": url, "source_branch": branch,
                 "total_nodes": len(graph.nodes), "total_edges": len(graph.edges)}
        data_out.update(stats)

        import gzip as _gzip
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        payload = json.dumps(data_out, ensure_ascii=False, separators=(",", ":"))
        open(out_path, "wb").write(_gzip.compress(payload.encode()))

        types = {}
        for n in graph.nodes.values():
            types[n.type] = types.get(n.type, 0) + 1

        return TaskResult.success(data={"url": url, "name": name, "index": index_name, "path": out_path,
                                        "nodes": len(graph.nodes), "edges": len(graph.edges),
                                        "structure": types})
    except subprocess.TimeoutExpired:
        return TaskResult.failure(f"Timeout clonando {url}")
    except Exception as e:
        return TaskResult.failure(f"Error: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def register(registry: Any) -> None:
    registry.register("cpg_index_ref", cpg_index_ref, metadata={"description": "Index external repo as reference CPG", "tags": ["cpg", "index"]})
