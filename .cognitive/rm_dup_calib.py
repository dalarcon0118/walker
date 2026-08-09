#!/usr/bin/env python3
"""Remove duplicated calibration functions from delta_evaluator.py."""

path = "projects/cognitive-mcp/cognitive_v2/shared/delta_evaluator.py"

with open(path) as f:
    lines = f.readlines()

# Find start marker: "# ─── 4-layer calibration"
start_idx = None
for i, line in enumerate(lines):
    if "# ─── 4-layer calibration" in line:
        start_idx = i
        break

if start_idx is None:
    print("ERROR: start marker not found")
    exit(1)

# Find end marker: "def _sync_session_phase("
end_idx = None
for i in range(start_idx + 1, len(lines)):
    stripped = lines[i].strip()
    if stripped.startswith("def _sync_session_phase("):
        end_idx = i
        break

if end_idx is None:
    print("ERROR: end marker (_sync_session_phase) not found")
    exit(1)

print(f"Removing lines {start_idx + 1} to {end_idx} ({end_idx - start_idx} lines)")
print(f"Start: {lines[start_idx].strip()}")
print(f"End:   {lines[end_idx - 1].strip()}")

# Keep everything up to start_idx, then continue from end_idx
new_lines = lines[:start_idx] + lines[end_idx:]

with open(path, "w") as f:
    f.writelines(new_lines)

print(f"Done. File reduced from {len(lines)} to {len(new_lines)} lines.")
