#!/bin/bash
echo "=== must_exist ==="
echo "--- compute_proportional_calibration in calibration_utils.py"
grep -n "def compute_proportional_calibration" projects/cognitive-mcp/cognitive_v2/shared/_shared/calibration_utils.py 2>/dev/null || echo "NOT FOUND"
echo "--- _run_calibration in _calibration_source.py"
grep -n "def _run_calibration" projects/cognitive-mcp/cognitive_v2/shared/_shared/_calibration_source.py 2>/dev/null || echo "NOT FOUND"
echo "--- _calibrate_state in _calibration_source.py"
grep -n "def _calibrate_state" projects/cognitive-mcp/cognitive_v2/shared/_shared/_calibration_source.py 2>/dev/null || echo "NOT FOUND"
echo "--- _calibrate_delta in _calibration_source.py"
grep -n "def _calibrate_delta" projects/cognitive-mcp/cognitive_v2/shared/_shared/_calibration_source.py 2>/dev/null || echo "NOT FOUND"
echo "--- _calibrate_strategy in _calibration_source.py"
grep -n "def _calibrate_strategy" projects/cognitive-mcp/cognitive_v2/shared/_shared/_calibration_source.py 2>/dev/null || echo "NOT FOUND"
echo "--- _calibrate_model in _calibration_source.py"
grep -n "def _calibrate_model" projects/cognitive-mcp/cognitive_v2/shared/_shared/_calibration_source.py 2>/dev/null || echo "NOT FOUND"
echo "--- _compute_last_known_distance in _calibration_source.py"
grep -n "def _compute_last_known_distance" projects/cognitive-mcp/cognitive_v2/shared/_shared/_calibration_source.py 2>/dev/null || echo "NOT FOUND"
echo "--- _record_calibration_event in _calibration_source.py"
grep -n "def _record_calibration_event" projects/cognitive-mcp/cognitive_v2/shared/_shared/_calibration_source.py 2>/dev/null || echo "NOT FOUND"
echo "--- _record_delta_trail in _calibration_source.py"
grep -n "def _record_delta_trail" projects/cognitive-mcp/cognitive_v2/shared/_shared/_calibration_source.py 2>/dev/null || echo "NOT FOUND"
echo "--- project_advance def in mcp_tools.py"
grep -n "def project_advance" projects/cognitive-mcp/cognitive_v2/features/project/api/mcp_tools.py 2>/dev/null | head -3
echo ""
echo "=== must_not_exist (functions STILL in delta_evaluator.py) ==="
for f in _run_calibration _calibrate_state _calibrate_delta _calibrate_strategy _calibrate_model _compute_last_known_distance _record_calibration_event; do
  count=$(grep -c "def $f" projects/cognitive-mcp/cognitive_v2/shared/delta_evaluator.py 2>/dev/null)
  echo "$f: $count definition(s) STILL in delta_evaluator.py"
done
echo ""
echo "=== barrel import patterns ==="
echo "--- delta_evaluator.py imports from barrel or direct?"
grep "from cognitive_v2.shared" projects/cognitive-mcp/cognitive_v2/shared/delta_evaluator.py 2>/dev/null | head -5
echo "--- features/ imports from _shared directly?"
grep -rn "from cognitive_v2.shared._shared import\|from cognitive_v2.shared.calibration_store import" projects/cognitive-mcp/cognitive_v2/features/ 2>/dev/null | head -5
echo "(empty = no direct _shared imports in features)"
echo ""
echo "=== invariants ==="
echo "--- compute_proportional_calibration in barrel?"
grep "compute_proportional_calibration" projects/cognitive-mcp/cognitive_v2/shared/__init__.py 2>/dev/null
echo "--- calibration_store singleton?"
grep "^calibration_store = " projects/cognitive-mcp/cognitive_v2/shared/calibration_store.py 2>/dev/null
echo "--- test files exist?"
ls projects/cognitive-mcp/tests/shared/test_calibration_utils.py 2>/dev/null || echo "tests/shared/test_calibration_utils.py NOT FOUND"
ls projects/cognitive-mcp/tests/shared/test_calibration_source.py 2>/dev/null || echo "tests/shared/test_calibration_source.py NOT FOUND"
