"""Migrate legacy .cognitive/projects.json into canonical data/projects.json.

Merges by repo_path. On id conflict, canonical wins with a warning.
Legacy is removed after successful merge.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _get_walker_root() -> Path:
    this_dir = Path(__file__).resolve().parent
    for _ in range(12):
        if (this_dir / "config" / "config.json").is_file():
            return this_dir
        if (this_dir / "data" / "projects.json").is_file():
            return this_dir
        parent = this_dir.parent
        if parent == this_dir:
            break
        this_dir = parent
    return Path.cwd().resolve()


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {"projects": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"projects": {}}


def migrate(dry_run: bool = False) -> dict:
    walker_root = _get_walker_root()
    canonical_path = walker_root / "data" / "projects.json"
    legacy_path = walker_root / ".cognitive" / "projects.json"

    canonical = _load_json(canonical_path)
    legacy = _load_json(legacy_path)

    canonical_projects = canonical.get("projects", {})
    legacy_projects = legacy.get("projects", {})

    merged = dict(canonical_projects)
    conflicts: list[dict] = []
    added: list[str] = []
    overwritten: list[str] = []

    for pid, info in legacy_projects.items():
        repo = info.get("repo_path", "") or ""
        if not repo:
            continue
        existing = merged.get(pid)
        if existing:
            existing_repo = existing.get("repo_path", "") or ""
            if existing_repo and existing_repo != repo:
                conflicts.append(
                    {
                        "project_id": pid,
                        "canonical_repo": existing_repo,
                        "legacy_repo": repo,
                    }
                )
                continue
            if existing_repo:
                merged[pid] = existing
                overwritten.append(pid)
                continue
        merged[pid] = info
        added.append(pid)

    canonical["projects"] = merged
    now = datetime.now(timezone.utc).isoformat()
    canonical["updated_at"] = now

    result = {
        "added": added,
        "overwritten": overwritten,
        "conflicts": conflicts,
        "canonical_count": len(canonical_projects),
        "legacy_count": len(legacy_projects),
        "merged_count": len(merged),
    }

    if dry_run:
        return result

    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.write_text(json.dumps(canonical, indent=2, ensure_ascii=False))

    if legacy_path.exists():
        shutil.move(str(legacy_path), str(legacy_path.with_suffix(".json.migrated")))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy .cognitive/projects.json to data/projects.json"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing"
    )
    args = parser.parse_args()

    result = migrate(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["conflicts"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
