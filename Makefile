# ═══════════════════════════════════════════════════════════════
# Walker3 — Makefile
# ═══════════════════════════════════════════════════════════════
# Main targets:
#   make install       → Install everything: CLI deps + Python package
#   make install-deps  → Only install CLI deps (ast-grep)
#   make pip-install   → Only install the Python package
#   make check         → Verify all deps are OK
#   make doctor        → Full environment diagnostics
#   make check-stability → Gate: la suite debe recolectar estable (2 corridas)
# ═══════════════════════════════════════════════════════════════

.PHONY: install install-deps pip-install check doctor check-stability reinstall help

# ── Default: full install ─────────────────────────────────────
install: install-deps pip-install
	@echo ""
	@echo "  ✅ Walker3 fully installed."
	@echo "  → Run 'walker3 help' to get started."
	@echo ""

# ── External CLI deps (ast-grep) ──────────────────────────────
install-deps:
	@echo ""
	@echo "  ╔═══════════════════════════════════════════════╗"
	@echo "  ║   Installing CLI dependencies...              ║"
	@echo "  ╚═══════════════════════════════════════════════╝"
	@echo ""
	@bash bin/install-deps.sh
	@echo ""

# ── Dependencia específica ────────────────────────────────────
install-ast-grep:
	@bash bin/install-deps.sh ast-grep

# ── Paquete Python ────────────────────────────────────────────
pip-install:
	@echo "  → Instalando walker3 como paquete editable..."
	@pip3 install -e .
	@echo "  ✅ walker3 instalado."

# ── Verification / Doctor ────────────────────────────────────
check:
	@python3 -c "from fast_mcp.shared_kernel.dependency_checker import check_and_report; check_and_report()"

doctor:
	@echo ""
	@echo "  ╔═══════════════════════════════════════════════╗"
	@echo "  ║   Walker3 — Environment Diagnostics           ║"
	@echo "  ╚═══════════════════════════════════════════════╝"
	@echo ""
	@python3 -c "from fast_mcp.shared_kernel.dependency_checker import check_and_report; check_and_report(verbose=True)"
	@echo "  → walker3 package: $$(pip3 show walker3 2>/dev/null | grep Version || echo 'NOT INSTALLED')"
	@echo ""

# ── Gate de estabilidad de la suite de tests ──────────────────
check-stability:
	@bash bin/check-test-stability.sh

# ── Full reinstall ────────────────────────────────────────────
reinstall:
	@pip3 uninstall -y walker3 2>/dev/null || true
	@$(MAKE) install

# ── Help ──────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  Walker3 — Available commands:"
	@echo ""
	@echo "  make install         Full install (recommended)"
	@echo "  make install-deps    CLI deps only (ast-grep)"
	@echo "  make pip-install     Python package only"
	@echo "  make check           Verify dependencies"
	@echo "  make doctor          Full diagnostics"
	@echo "  make check-stability Test collection stability gate"
	@echo "  make reinstall       Reinstall from scratch"
	@echo ""
