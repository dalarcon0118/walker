#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# bin/install-deps.sh — Install external CLI dependencies
# ═══════════════════════════════════════════════════════════════
# Installs ast-grep, required for walker's multi-language code
# analysis features (qlty fue eliminado — ADR-WALKER-054).
#
# Usage:
#   ./bin/install-deps.sh          # Install all
#   ./bin/install-deps.sh --quiet   # Minimal output
#   ./bin/install-deps.sh ast-grep  # Only ast-grep
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

QUIET=false
INSTALL_ALL=true
TARGET=""

for arg in "$@"; do
    case "$arg" in
        --quiet) QUIET=true ;;
        ast-grep) INSTALL_ALL=false; TARGET="ast-grep" ;;
        purescript) INSTALL_ALL=false; TARGET="purescript" ;;
    esac
done

log() {
    if ! $QUIET; then echo "  → $1"; fi
}

warn() {
    echo "  ⚠️  $1" >&2
}

success() {
    if ! $QUIET; then echo "  ✅ $1"; fi
}

# ── ast-grep ──────────────────────────────────────────────────
install_ast_grep() {
    if command -v ast-grep &>/dev/null; then
        success "ast-grep already installed ($(ast-grep --version 2>/dev/null || echo '?'))"
        return 0
    fi

    log "Installing ast-grep..."

    # macOS via Homebrew
    if [[ "$(uname)" == "Darwin" ]] && command -v brew &>/dev/null; then
        brew install ast-grep 2>&1 | tail -3
    # npm global
    elif command -v npm &>/dev/null; then
        npm install -g @ast-grep/cli 2>&1 | tail -3
    # cargo (fallback)
    elif command -v cargo &>/dev/null; then
        cargo install ast-grep --locked 2>&1 | tail -5
    else
        warn "Could not install ast-grep. Install manually:"
        warn "  brew install ast-grep"
        warn "  npm install -g @ast-grep/cli"
        return 1
    fi

    if command -v ast-grep &>/dev/null; then
        success "ast-grep installed ($(ast-grep --version))"
    else
        warn "ast-grep not found after install. Is it in PATH?"
        return 1
    fi
}

# ── PureScript parser (ast-grep custom language) ──────────────
install_purescript_parser() {
    local walker_root
    walker_root="$(cd "$(dirname "$0")/.." && pwd)"
    local parser_dir="$walker_root/data/parsers"
    local parser_lib="$parser_dir/purescript.dylib"
    if [[ "$(uname)" != "Darwin" ]]; then
        parser_lib="$parser_dir/purescript.so"
    fi
    if [[ -f "$parser_lib" ]]; then
        success "PureScript parser already built ($parser_lib)"
        return 0
    fi
    if ! command -v cc &>/dev/null && ! command -v gcc &>/dev/null; then
        warn "No C compiler found; cannot build PureScript parser."
        return 1
    fi
    local tmpdir
    tmpdir="$(mktemp -d)"
    log "Cloning tree-sitter-purescript..."
    if ! git clone --depth 1 https://github.com/postsolar/tree-sitter-purescript.git "$tmpdir/tps" 2>&1 | tail -2; then
        warn "Could not clone tree-sitter-purescript."
        rm -rf "$tmpdir"
        return 1
    fi
    mkdir -p "$parser_dir"
    local cc_bin="cc"
    command -v cc &>/dev/null || cc_bin="gcc"
    log "Compiling PureScript parser..."
    if [[ "$(uname)" == "Darwin" ]]; then
        $cc_bin -shared -fPIC -fno-exceptions -g -I "$tmpdir/tps/src" -o "$parser_lib" -O2 "$tmpdir/tps/src/scanner.c" -xc "$tmpdir/tps/src/parser.c" 2>&1 | tail -5
    else
        $cc_bin -shared -fPIC -fno-exceptions -g -I "$tmpdir/tps/src" -o "$parser_lib" -O2 "$tmpdir/tps/src/scanner.c" -xc "$tmpdir/tps/src/parser.c" 2>&1 | tail -5
    fi
    rm -rf "$tmpdir"
    if [[ -f "$parser_lib" ]]; then
        success "PureScript parser built ($parser_lib)"
    else
        warn "PureScript parser build failed."
        return 1
    fi
}

# ── Main ──────────────────────────────────────────────────────
echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║   Walker — CLI Dependency Installer           ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""

FAILED=0

if $INSTALL_ALL || [[ "$TARGET" == "ast-grep" ]]; then
    install_ast_grep || ((FAILED++))
fi

if $INSTALL_ALL || [[ "$TARGET" == "purescript" ]]; then
    install_purescript_parser || ((FAILED++))
fi

echo ""
if [[ "$FAILED" -eq 0 ]]; then
    echo "  ✅ All dependencies installed successfully."
    echo ""
    exit 0
else
    echo "  ⚠️  $FAILED dependency/dependencies failed. Check errors above."
    echo ""
    exit 1
fi