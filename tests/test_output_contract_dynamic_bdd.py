"""test_output_contract_dynamic_bdd.py — el `output_schema` contra el handler real.

Los tests de `test_contract_fields_bdd.py` verifican que el JSON *diga* lo
correcto; éste verifica que el handler **devuelva** lo que el JSON promete.
Es la diferencia que dejó pasar `all_passed`: el schema lo declaraba required y
`verify.py` devolvía `all_satisfied`, con la suite entera en verde.

Cómo: para cada sub-estado con `action`, se toma el handler REAL del
TaskRegistry y se leen las claves que devuelve (dicts retornados, `data={...}`,
y asignaciones a `payload`/`result`/`data`/…). No se ejecuta el handler: varios
tienen efectos (pytest, indexado, cierre de sprint).

Contrato:
1. Toda clave en `output_schema.required` de un paso con action existe en el
   return del handler.
2. Las propiedades declaradas que no se pueden ver estáticamente (el handler
   devuelve un struct de un servicio) están en una lista explícita: un fantasma
   nuevo falla el test en vez de pasar como "no visible".
"""

from __future__ import annotations

import ast
import glob
import inspect
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODES = ROOT / "data" / "workflow" / "nodes"

#: Variables que se devuelven en `TaskResult.success(data=...)`.
_PAYLOAD_VARS = frozenset({
    "payload", "result", "result_data", "data", "out", "output", "body", "response",
})

#: Pasos cuyo handler devuelve un struct de un servicio: sus claves no se pueden
#: leer del fuente. Se listan con el motivo; si aparece uno nuevo sin justificar,
#: el test falla (así no entra un `status` fantasma de contrabando).
_SIN_EVIDENCIA_ESTATICA: dict[tuple[str, str], str] = {
    ("PROJECT_INIT", "DETECT"): "detect_project_context devuelve el análisis del servicio",
    ("PROJECT_INIT", "BUILD_PROJECT"): "build_project_config devuelve el resultado del build",
    ("PROJECT_INIT", "SCAN_CODE"): "sync_code_index devuelve el reporte del índice",
    ("PROJECT_INIT", "BUILD_CKG"): "initialize_knowledge_base devuelve lo escrito por el CKG",
    ("PROJECT_INIT", "FIRST_SPRINT"): "create_first_sprint devuelve el sprint creado",
    ("PROJECT_INIT", "REVIEW"): "apply_project_config devuelve la config aplicada",
    ("SPRINT", "TASK_SPEC"): "spec_task devuelve campos derivados de la spec persistida",
    ("SPRINT", "CLOSE"): "close_sprint devuelve el resultado de close_spring",
}


def _steps_with_action() -> list[tuple[str, str, str, dict]]:
    out = []
    for path in sorted(glob.glob(str(NODES / "*.node.json"))):
        node = pathlib.Path(path).name.split(".")[0].upper()
        for sub in json.loads(pathlib.Path(path).read_text())["sub_states"]:
            action = str(sub.get("action") or "")
            if action:
                out.append((node, str(sub["key"]), action, sub.get("output_schema") or {}))
    return out


def _registry():
    from fast_mcp.composition import _build_task_registry

    return _build_task_registry()


def _returned_keys(handler) -> set[str]:
    """Claves que el handler devuelve, leídas del fuente (sin ejecutarlo)."""
    tree = ast.parse(inspect.getsource(handler).lstrip())
    keys: set[str] = set()

    def _dict_keys(node: ast.Dict) -> set[str]:
        return {
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }

    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys |= _dict_keys(node.value)
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "data" and isinstance(kw.value, ast.Dict):
                    keys |= _dict_keys(kw.value)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if any(isinstance(t, ast.Name) and t.id in _PAYLOAD_VARS for t in targets):
                if isinstance(node.value, ast.Dict):
                    keys |= _dict_keys(node.value)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript):
            target = node.targets[0]
            if (
                isinstance(target.value, ast.Name)
                and target.value.id in _PAYLOAD_VARS
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                keys.add(target.slice.value)
    return keys


@pytest.mark.parametrize(
    "node,sub_state,action,output_schema",
    _steps_with_action(),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_el_handler_devuelve_los_outputs_requeridos(node, sub_state, action, output_schema):
    registry = _registry()
    assert registry.has(action), f"{node}.{sub_state}: acción '{action}' sin handler"

    required = set(output_schema.get("required") or [])
    if not required:
        pytest.skip("el paso no declara outputs requeridos")

    missing = sorted(required - _returned_keys(registry._handlers[action]))  # noqa: SLF001

    assert not missing, (
        f"{node}.{sub_state} ({action}) declara required {sorted(required)} "
        f"y el handler no devuelve {missing}"
    )


def test_los_outputs_no_visibles_estan_justificados():
    """Un fantasma nuevo no puede esconderse detrás de "no se ve en el fuente"."""
    registry = _registry()
    invisibles: dict[tuple[str, str], set[str]] = {}

    for node, sub_state, action, output_schema in _steps_with_action():
        declared = set(output_schema.get("properties") or {})
        if not declared:
            continue
        missing = declared - _returned_keys(registry._handlers[action])  # noqa: SLF001
        if missing:
            invisibles[(node, sub_state)] = missing

    sin_justificar = sorted(set(invisibles) - set(_SIN_EVIDENCIA_ESTATICA))
    assert not sin_justificar, (
        "pasos que declaran outputs que el handler no devuelve y no están "
        f"justificados: {sin_justificar}. Si el handler devuelve un struct de un "
        "servicio, agregalo a _SIN_EVIDENCIA_ESTATICA con el motivo."
    )

    ya_visibles = sorted(set(_SIN_EVIDENCIA_ESTATICA) - set(invisibles))
    assert not ya_visibles, (
        "la lista de 'sin evidencia estática' quedó vieja (ahora se ve en el "
        f"fuente): {ya_visibles}"
    )
