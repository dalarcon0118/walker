"""test_language_imports_bdd.py — análisis de imports por lenguaje (P1.9).

Antes: `hallucination.py` resolvía imports solo como Python, omitía TODO
import relativo de JS y trataba cualquier bare specifier desconocido como npm
(los alias `@/…` eran alucinaciones falsas); `coupling.py` tenía una lista de
prefijos de proyecto hardcodeada y su Ca solo miraba `*.py`.

Ahora la decisión vive en un adapter (`lang_imports`) y lo que no se puede
verificar se reporta como `not_checked`, nunca como cero verde.
"""
from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.features.code_analysis.infrastructure import lang_imports as li


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _js_project(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "@acme/app", "dependencies": {"react": "^18"}})
    )
    return tmp_path


# ── detección y decisión local/externo ─────────────────────────────


def test_detecta_lenguaje_por_extension(tmp_path):
    assert li.detect_language(tmp_path / "a.mjs") == "javascript"
    assert li.detect_language(tmp_path / "a.go") == "go"
    assert li.detect_language(tmp_path / "a.toml") is None


def test_python_local_vs_externo(tmp_path):
    _write(tmp_path / "mipkg" / "__init__.py")
    source = _write(tmp_path / "app.py")

    assert li.is_local_import("mipkg.core", source, tmp_path) is True
    assert li.is_local_import("os", source, tmp_path) is False
    assert li.is_local_import(".hermano", source, tmp_path) is True


def test_js_primer_parte_y_alias(tmp_path):
    _js_project(tmp_path)
    source = _write(tmp_path / "src" / "a.ts")

    assert li.is_local_import("./util", source, tmp_path) is True
    assert li.is_local_import("react", source, tmp_path) is False
    assert li.is_local_import("@acme/app", source, tmp_path) is True
    # Alias de paths: no se puede resolver sin tsconfig → None (not_checked).
    assert li.is_local_import("@/components/Button", source, tmp_path) is None


def test_go_module_path(tmp_path):
    _write(tmp_path / "go.mod", "module github.com/acme/app\n\ngo 1.22\n")
    source = _write(tmp_path / "main.go")

    assert li.is_local_import("github.com/acme/app/internal/x", source, tmp_path) is True
    assert li.is_local_import("github.com/other/pkg", source, tmp_path) is False


def test_lenguaje_sin_adapter_es_none(tmp_path):
    source = _write(tmp_path / "a.php")
    assert li.is_local_import("Some\\Thing", source, tmp_path) is None


def test_declared_dependency_incluye_node_builtins(tmp_path):
    _js_project(tmp_path)
    assert li.declared_dependency("react", tmp_path, "javascript") is True
    assert li.declared_dependency("axios", tmp_path, "javascript") is False
    assert li.declared_dependency("node:fs", tmp_path, "javascript") is True


def test_resuelve_relativo_js_con_extension_e_index(tmp_path):
    _js_project(tmp_path)
    _write(tmp_path / "src" / "util.ts", "export const x = 1\n")
    _write(tmp_path / "src" / "lib" / "index.ts", "export default 1\n")
    source = _write(tmp_path / "src" / "a.ts")

    assert li.resolve_relative("./util", source) == (tmp_path / "src" / "util.ts")
    assert li.resolve_relative("./lib", source) == (tmp_path / "src" / "lib" / "index.ts")
    assert li.resolve_relative("./no-existe", source) is None


def test_extrae_specifiers_js_sin_duplicados(tmp_path):
    _js_project(tmp_path)
    source = _write(
        tmp_path / "src" / "a.ts",
        "\n".join(
            [
                "import React from 'react'",
                "import { useState } from 'react'",
                "import './estilos.css'",
                "export { x } from './re-export'",
                "const fs = require('node:fs')",
                "const lazy = import('./lazy')",
            ]
        ),
    )

    specs = [i["module"] for i in li.extract_imports(source)]
    assert specs == ["react", "./estilos.css", "./re-export", "node:fs", "./lazy"]


# ── hallucination: JS deja de ser ciega a los relativos ────────────


def test_js_relativo_inexistente_es_alucinacion(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.hallucination import scan_file

    _js_project(tmp_path)
    source = _write(tmp_path / "src" / "a.ts", "import { x } from './no-existe'\n")

    result = scan_file(source, str(tmp_path))

    assert result["status"] == "checked"
    assert result["hallucinations"] == 1
    assert result["issues"][0]["module"] == "./no-existe"


def test_js_relativo_existente_no_alucina(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.hallucination import scan_file

    _js_project(tmp_path)
    _write(tmp_path / "src" / "util.ts", "export const x = 1\n")
    source = _write(tmp_path / "src" / "a.ts", "import { x } from './util'\n")

    result = scan_file(source, str(tmp_path))

    assert result["hallucinations"] == 0 and result["not_checked"] == 0


def test_js_alias_es_not_checked_no_alucinacion(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.hallucination import scan_file

    _js_project(tmp_path)
    source = _write(
        tmp_path / "src" / "a.ts", "import { Button } from '@/components/Button'\n"
    )

    result = scan_file(source, str(tmp_path))

    assert result["hallucinations"] == 0
    assert result["not_checked"] == 1
    assert result["issues"][0]["status"] == "NOT_CHECKED"


def test_js_dependencia_declarada_sin_instalar_es_warning(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.hallucination import scan_file

    _js_project(tmp_path)  # react declarado, node_modules no existe
    source = _write(tmp_path / "src" / "a.ts", "import React from 'react'\n")

    result = scan_file(source, str(tmp_path))

    assert result["hallucinations"] == 0
    assert result["warnings"] == 1


def test_js_paquete_ni_declarado_ni_instalado_es_alucinacion(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.hallucination import scan_file

    _js_project(tmp_path)
    source = _write(tmp_path / "src" / "a.ts", "import x from 'paquete-inventado'\n")

    result = scan_file(source, str(tmp_path))

    assert result["hallucinations"] == 1


def test_go_es_not_checked_visible(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.hallucination import scan_file

    source = _write(tmp_path / "main.go", 'package main\n\nimport "fmt"\n')

    result = scan_file(source, str(tmp_path))

    assert result["status"] == "not_checked"
    assert result["checked"] is False
    assert "go" in result["reason"]
    assert result["hallucinations"] == 0


def test_adapter_agrega_no_verificados(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
        WalkerCpgAnalysisAdapter,
    )
    from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
        CognitiveCpgRepository,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    adapter = WalkerCpgAnalysisAdapter(
        repo=CognitiveCpgRepository(str(data_dir), "p-lang"),
        analyzer=None,
        event_bus=None,
        service=None,
    )
    py = _write(tmp_path / "src" / "app.py", "from modulo_inexistente import x\n")
    go = _write(tmp_path / "main.go", 'package main\n\nimport "fmt"\n')

    result = adapter.detect_imports(files=[str(py), str(go)], project_root=str(tmp_path))

    assert result["files_scanned"] == 2
    assert result["checked_files"] == 1
    assert result["not_checked_files"] == 1
    assert result["hallucinations"] == 1
    assert any("go" in reason for reason in result["not_checked_reasons"])


# ── coupling: sin lista de prefijos y con Ca por lenguaje ──────────


def test_coupling_python_no_inventa_externos(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.codereview.coupling import (
        coupling_metrics,
    )

    _write(tmp_path / "mipkg" / "__init__.py")
    source = _write(tmp_path / "app.py", "import os\nimport mipkg.core\n")

    result = coupling_metrics(source, project_root=tmp_path)

    assert result["status"] == "checked"
    assert result["ce"] == 1 and result["imports"] == ["os"]
    assert result["local_imports"] == ["mipkg.core"]


def test_coupling_js_local_y_externo(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.codereview.coupling import (
        coupling_metrics,
    )

    _js_project(tmp_path)
    source = _write(
        tmp_path / "src" / "a.ts", "import React from 'react'\nimport { x } from './util'\n"
    )

    result = coupling_metrics(source, project_root=tmp_path)

    assert result["status"] == "checked"
    assert result["ce"] == 1 and result["imports"] == ["react"]
    assert "./util" in result["local_imports"]


def test_coupling_alias_es_partial(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.codereview.coupling import (
        coupling_metrics,
    )

    _js_project(tmp_path)
    source = _write(tmp_path / "src" / "a.ts", "import x from '@/lib/x'\n")

    result = coupling_metrics(source, project_root=tmp_path)

    assert result["status"] == "partial"
    assert result["not_checked"] == ["@/lib/x"]


def test_coupling_ca_js_confirmado_por_resolucion(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.codereview.coupling import (
        coupling_metrics,
    )

    _js_project(tmp_path)
    target = _write(tmp_path / "src" / "util.ts", "export const x = 1\n")
    _write(tmp_path / "src" / "a.ts", "import { x } from './util'\n")
    _write(tmp_path / "src" / "b.ts", "// menciona util pero no lo importa\n")

    result = coupling_metrics(target, compute_ca=True, project_root=tmp_path)

    assert result["ca_status"] == "checked"
    assert result["ca"] == 1
    assert result["imported_by"][0].endswith("a.ts")


def test_coupling_lenguaje_sin_adapter_es_not_checked(tmp_path):
    from fast_mcp.features.code_analysis.infrastructure.codereview.coupling import (
        coupling_metrics,
    )

    source = _write(tmp_path / "a.toml", "x = 1\n")

    result = coupling_metrics(source, project_root=tmp_path)

    assert result["status"] == "not_checked"
    assert result["ca_status"] == "not_checked"
    assert result["ce"] == 0
