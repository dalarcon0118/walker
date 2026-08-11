#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# bin/check-test-stability.sh — Gate de estabilidad de la suite
# ═══════════════════════════════════════════════════════════════
# Recolecta los tests DOS veces en procesos independientes y falla si:
#   1. El conjunto de node IDs difiere entre corridas (flake de colección,
#      ej: archivos generados en tests/ o registros duplicados de pytest-bdd).
#   2. El conteo no coincide con la línea base pineada (default: 492).
#
# Uso:
#   ./bin/check-test-stability.sh                     # check con baseline default
#   ./bin/check-test-stability.sh --baseline 500      # pin explícito (un solo uso)
#   ./bin/check-test-stability.sh --update-baseline   # fija el conteo actual en .test-collection.baseline
#   TEST_COLLECTION_BASELINE=500 ./bin/check-test-stability.sh
#   ./bin/check-test-stability.sh tests/              # directorio custom
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python3}"
BASELINE_FILE=".test-collection.baseline"
DEFAULT_BASELINE=492

TEST_DIR="tests/"
BASELINE=""
UPDATE_BASELINE=false

# ── Argumentos ──────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --baseline)
            BASELINE="$2"
            shift 2
            ;;
        --update-baseline)
            UPDATE_BASELINE=true
            shift
            ;;
        -h | --help)
            sed -n '2,20p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        -*)
            echo "  ⚠️  Opción desconocida: $1 (ver --help)" >&2
            exit 2
            ;;
        *)
            TEST_DIR="$1"
            shift
            ;;
    esac
done

# ── Línea base: flag > env > archivo > default ──────────────────
if [[ -z "$BASELINE" && -n "${TEST_COLLECTION_BASELINE:-}" ]]; then
    BASELINE="$TEST_COLLECTION_BASELINE"
fi
if [[ -z "$BASELINE" && -f "$BASELINE_FILE" ]]; then
    BASELINE="$(cat "$BASELINE_FILE")"
fi
if [[ -z "$BASELINE" ]]; then
    BASELINE="$DEFAULT_BASELINE"
fi

TMP1="$(mktemp)"
TMP2="$(mktemp)"
ERR_LOG="$(mktemp)"
trap 'rm -f "$TMP1" "$TMP2" "$ERR_LOG"' EXIT

collect() { # $1 = outfile
    if ! "$PYTHON" bin/collect_test_nodes.py "$TEST_DIR" > "$1" 2> "$ERR_LOG"; then
        echo "  ⚠️  pytest --collect-only falló:" >&2
        cat "$ERR_LOG" >&2
        exit 1
    fi
}

echo "  → Recolección #1 (proceso 1)..."
collect "$TMP1"
COUNT1="$(wc -l < "$TMP1" | tr -d ' ')"
echo "  → Recolección #2 (proceso 2)..."
collect "$TMP2"
COUNT2="$(wc -l < "$TMP2" | tr -d ' ')"

echo ""
echo "  conteo: run1=$COUNT1 run2=$COUNT2 (baseline=$BASELINE)"
echo ""

FAILED=0

# Al pinar el baseline, la comparación de conteo usa el conteo actual
# (el gate de estabilidad run1-vs-run2 sigue aplicando por separado).
if $UPDATE_BASELINE; then
    BASELINE="$COUNT1"
fi

if [[ "$COUNT1" -ne "$COUNT2" ]]; then
    echo "  ❌ RECOLECCIÓN INESTABLE: $COUNT1 vs $COUNT2 nodos entre corridas."
    FAILED=1
fi

if ! diff -q "$TMP1" "$TMP2" > /dev/null; then
    echo "  ❌ Los node IDs difieren entre corridas:"
    echo "     ── solo en run1 ──"
    comm -23 "$TMP1" "$TMP2" | sed 's/^/       + /' | head -20
    echo "     ── solo en run2 ──"
    comm -13 "$TMP1" "$TMP2" | sed 's/^/       - /' | head -20
    FAILED=1
fi

if [[ "$COUNT1" -ne "$BASELINE" ]]; then
    echo "  ❌ Conteo $COUNT1 != baseline pineada $BASELINE."
    echo "     Si el cambio de tests es intencional:"
    echo "       ./bin/check-test-stability.sh --update-baseline"
    FAILED=1
fi

if $UPDATE_BASELINE; then
    echo "$COUNT1" > "$BASELINE_FILE"
    echo "  ✅ Baseline actualizado: $BASELINE_FILE = $COUNT1"
fi

if [[ "$FAILED" -eq 0 ]]; then
    echo "  ✅ Suite estable: $COUNT1 tests, colección idéntica en 2 corridas."
    exit 0
fi
echo "  ❌ Gate de estabilidad FALLÓ. Ver arriba." >&2
exit 1
