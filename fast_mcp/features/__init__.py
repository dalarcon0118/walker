"""fast_mcp.features — Bounded contexts del dominio (ADR-071 §3bis).

Cada feature es un context con capas (domain/application/infrastructure).
Reglas de coherencia (verificadas por tests/test_fast_mcp_architecture_bdd.py):
  1. Un feature NO importa otro feature — solo shared_kernel o su propia infra.
  2. domain/ es puro.
  3. La superficie (phases/) recibe objetos cableados por composition.py.
  4. Los registries viven dentro de su context.
"""
