#!/usr/bin/env python3
"""validate-schema.py — Valida un archivo JSON contra un JSON Schema.

Uso:
    python3 validate-schema.py <json_file> <schema_file>

Exit code:
    0 → validación exitosa
    1 → error de validación
    2 → error de uso (argumentos faltantes, archivo no encontrado, JSON inválido)

Ejemplo:
    python3 validate-schema.py .cognitive/delta/mi-plan/plan.json schema/plan.schema.json
"""

import json
import sys
import os

try:
    import jsonschema
except ImportError:
    print("❌ ERROR: Librería 'jsonschema' no instalada. Ejecuta: pip install jsonschema")
    sys.exit(2)


def main():
    if len(sys.argv) != 3:
        print("Uso: python3 validate-schema.py <json_file> <schema_file>")
        print("")
        print("Valida que <json_file> cumpla con el schema definido en <schema_file>.")
        print("")
        print("Ejemplo:")
        print("  python3 validate-schema.py .cognitive/delta/mi-plan/plan.json schema/plan.schema.json")
        sys.exit(2)

    json_path = sys.argv[1]
    schema_path = sys.argv[2]

    # Validar que los archivos existen
    if not os.path.exists(json_path):
        print(f"❌ ERROR: Archivo JSON no encontrado: {json_path}")
        sys.exit(2)
    if not os.path.exists(schema_path):
        print(f"❌ ERROR: Archivo de schema no encontrado: {schema_path}")
        sys.exit(2)

    # Cargar JSON
    try:
        with open(json_path) as f:
            instance = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ ERROR: JSON inválido en {json_path}: {e}")
        sys.exit(2)

    # Cargar schema
    try:
        with open(schema_path) as f:
            schema = json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ ERROR: Schema inválido en {schema_path}: {e}")
        sys.exit(2)

    # Validar
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))

    if not errors:
        print("=" * 70)
        print(f"✅ VALIDATE-SCHEMA: {os.path.basename(json_path)} — VÁLIDO")
        print("=" * 70)
        print(f"   Schema: {os.path.basename(schema_path)}")
        print()
        print("📋 DECISION PROTOCOL:")
        print()
        print("  ▶️  El archivo cumple con el schema. Puedes continuar.")
        print("=" * 70)
        sys.exit(0)

    # Reportar errores
    print("=" * 70)
    print(f"❌ VALIDATE-SCHEMA: {os.path.basename(json_path)} — INVÁLIDO ({len(errors)} error(es))")
    print("=" * 70)
    print(f"   Schema: {os.path.basename(schema_path)}")
    print()

    # Agrupar errores por mensaje (sin path numerico)
    # En vez de 120 lineas (10 tareas × 12 campos), agrupa:
    #   'campo X falta' → tareas afectadas
    from collections import defaultdict
    groups: dict[str, list[str]] = defaultdict(list)

    for error in errors:
        path = " → ".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
        # Path generico: reemplaza indices numericos por *
        generic_path = " → ".join("*" if isinstance(p, int) else str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
        key = f"{generic_path} | {error.message}"
        groups[key].append(path)

    for i, (key, paths) in enumerate(sorted(groups.items()), 1):
        generic_path, message = key.split(" | ", 1)
        count = len(paths)
        if count > 1:
            print(f"  #{i} en {generic_path} ({count} ocurrencias)")
            print(f"     Mensaje: {message}")
        else:
            print(f"  #{i} en {paths[0]}")
            print(f"     Mensaje: {message}")
        print()

    print("─" * 70)
    print("📋 RECOVERY PROTOCOL:")
    print("   1. Corrige cada error de validación listado arriba en el archivo JSON")
    print("   2. Vuelve a ejecutar la validación:")
    print(f"      python3 scripts/validate-schema.py {json_path} {schema_path}")
    print("   3. Repite hasta que todos los errores estén corregidos")
    print("─" * 70)
    print("💡 El output de este script contiene los errores específicos a corregir.")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
