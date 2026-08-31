"""infrastructure/adapters — Adaptadores concretos de puertos del motor.

El adaptador por defecto de IWorkflowIO es propósito-agnóstico y no depende
de legacy. En tests se puede inyectar un mock del puerto.
"""
from __future__ import annotations

from .default_workflow_io import DefaultWorkflowIO

__all__ = ["DefaultWorkflowIO"]
