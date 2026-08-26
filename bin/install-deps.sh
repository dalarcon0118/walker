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