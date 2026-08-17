"""Temp repro: build_code_index con project_id (error unwrap)."""
from __future__ import annotations


def test_repro_build_code_index(tmp_path):
    from features.main.task_execution.tasks.initialize_project import build_code_index
    from shared.common.shared_domain import ExecutionContext

    ctx = ExecutionContext.create(session_id="repro", chain_dir="", channel="cli")
    result = build_code_index(ctx, {"project_id": "react-native-frontend"})
    print("\nRESULT ok:", result.ok)
    print("RESULT data:", result.data)
    print("RESULT error:", result.error)