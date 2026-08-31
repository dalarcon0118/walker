"""fast_mcp.shared_kernel — Kernel compartido entre contexts (ADR-071 §3bis).

Primitivas de instalación y contratos comunes. SIN lógica de negocio:
los use cases viven en `fast_mcp.features.*`. El kernel no importa features
(regla de coherencia 1: kernel ← features, nunca al revés).
"""

from __future__ import annotations
