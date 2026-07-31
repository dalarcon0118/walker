#!/usr/bin/env python3
"""msg-stats.py — diagnostic script for the MessageStore (V2.0).

Reads all ``kv:msg:*`` entries from engine.db and reports:
  - Total messages
  - Distribution by role (user, assistant, tool, system)
  - Distribution by message_type (input, output, tool_call, tool_result)
  - Messages with/without task_id
  - Messages with/without parent_id
  - Top domains by count
  - Session activity (messages per session)

Usage:
    python3 .cognitive/delta/scripts/msg-stats.py

Requires: the cognitive-mcp project environment (access to helpers._kv, helpers._cg_db).
Run from the project root directory.
"""

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime


MSG_KV_PREFIX = "kv:msg:"
MSG_IDX_PREFIX = "kv:msg_idx:"


def _get_db():
    """Connect to engine.db SQLite directly for prefix scans."""
    try:
        import sqlite3
        # Find engine.db relative to project root
        candidates = [
            os.path.join(os.getcwd(), "engine.db"),
            os.path.join(os.getcwd(), "projects", "cognitive-mcp", "engine.db"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return sqlite3.connect(path)
        # Try to resolve via helpers
        try:
            from helpers import _cg_db
            return _cg_db()
        except Exception:
            pass
        print("ERROR: engine.db not found")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: cannot open engine.db: {e}")
        sys.exit(1)


def _load_kv(key, db):
    """Load a KV entry from SQLite idx_store."""
    try:
        row = db.execute(
            "SELECT value_json FROM idx_store WHERE key = ?",
            (key,),
        ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        pass
    return None


def main():
    db = _get_db()

    # ── Scan all kv:msg:* entries ─────────────────────────────────
    rows = db.execute(
        "SELECT key, value_json FROM idx_store "
        "WHERE key LIKE 'kv:msg:%' AND key NOT LIKE 'kv:msg_idx:%'"
    ).fetchall()

    total = len(rows)
    if total == 0:
        print("📭 MessageStore is empty. No messages found.")
        return

    # ── Stats counters ──────────────────────────────────────────
    roles = Counter()
    msg_types = Counter()
    with_task_id = 0
    without_task_id = 0
    with_parent_id = 0
    without_parent_id = 0
    domains = Counter()
    sessions = Counter()
    projects = Counter()
    with_domain = 0
    without_domain = 0

    # Time range
    earliest = float("inf")
    latest = 0.0

    for key, val_json in rows:
        try:
            msg = json.loads(val_json)
        except (json.JSONDecodeError, TypeError):
            continue

        # Role
        role = msg.get("role", "unknown")
        roles[role] += 1

        # Message type
        mtype = msg.get("message_type", "unknown")
        msg_types[mtype] += 1

        # Task ID
        task_id = msg.get("task_id")
        if task_id:
            with_task_id += 1
        else:
            without_task_id += 1

        # Parent ID
        parent_id = msg.get("parent_id")
        if parent_id:
            with_parent_id += 1
        else:
            without_parent_id += 1

        # Domain (from metadata)
        metadata = msg.get("metadata", {})
        if isinstance(metadata, dict):
            domain = metadata.get("_domain", "")
            if domain:
                domains[domain] += 1
                with_domain += 1
            else:
                without_domain += 1
        else:
            without_domain += 1

        # Session
        session = msg.get("session_id", "unknown")
        sessions[session] += 1

        # Project
        project = msg.get("project_id", "unknown")
        projects[project] += 1

        # Time
        ts = msg.get("created_at", 0)
        if ts:
            earliest = min(earliest, ts)
            latest = max(latest, ts)

    # ── Print report ─────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  📊 MESSAGE STORE REPORT")
    print(f"{'='*50}")
    print(f"  Total messages:  {total}")
    if earliest < float("inf"):
        print(f"  Time range:      {datetime.fromtimestamp(earliest).strftime('%Y-%m-%d %H:%M')}"
              f" → {datetime.fromtimestamp(latest).strftime('%Y-%m-%d %H:%M')}")
    print()

    # Distribution by role
    print(f"  📋 By role:")
    for role, count in roles.most_common():
        pct = count / total * 100
        print(f"    {role:12s}: {count:5d} ({pct:5.1f}%)")

    print()
    print(f"  📋 By message_type:")
    for mtype, count in msg_types.most_common():
        pct = count / total * 100
        print(f"    {mtype:15s}: {count:5d} ({pct:5.1f}%)")

    print()
    print(f"  📋 Task traceability:")
    print(f"    With task_id     : {with_task_id:5d} ({with_task_id/total*100:5.1f}%)")
    print(f"    Without task_id  : {without_task_id:5d} ({without_task_id/total*100:5.1f}%)")
    print(f"    With parent_id   : {with_parent_id:5d} ({with_parent_id/total*100:5.1f}%)")
    print(f"    Without parent_id: {without_parent_id:5d} ({without_parent_id/total*100:5.1f}%)")

    print()
    print(f"  📋 Classification:")
    print(f"    With domain      : {with_domain:5d} ({with_domain/total*100:5.1f}%)")
    print(f"    Without domain   : {without_domain:5d} ({without_domain/total*100:5.1f}%)")
    if domains:
        print(f"    Top domains:")
        for domain, count in domains.most_common(5):
            print(f"      {domain:15s}: {count}")

    print()
    print(f"  📋 Sessions ({len(sessions)}):")
    for session, count in sessions.most_common(5):
        print(f"    {session[:40]:40s}: {count}")

    print()
    print(f"  📋 Projects ({len(projects)}):")
    for project, count in projects.most_common(5):
        print(f"    {project:20s}: {count}")

    # ── Health indicators ────────────────────────────────────────
    print()
    print(f"{'='*50}")
    print(f"  🏥 HEALTH CHECK")
    print(f"{'='*50}")

    user_count = roles.get("user", 0)
    assistant_count = roles.get("assistant", 0)
    tool_count = roles.get("tool", 0)

    if user_count == 0:
        print(f"  ⚠️  No user messages captured — MSG-001 may not be active")
    else:
        print(f"  ✅ User messages: {user_count}")

    if assistant_count == 0:
        print(f"  ⚠️  No assistant messages (LLM responses) — MSG-002 fallback not active")
    else:
        print(f"  ✅ Assistant messages: {assistant_count}")

    if with_task_id == 0:
        print(f"  ⚠️  No messages with task_id — tasks created before MSG-004")
    else:
        print(f"  ✅ Messages with task_id: {with_task_id}")

    if with_domain == 0:
        print(f"  ⚠️  No messages with domain — MSG-003 classifier may not be active")
    else:
        print(f"  ✅ Messages with domain: {with_domain}")

    if tool_count > 0 and user_count > 0:
        ratio = tool_count / (user_count + 1)
        if ratio > 20:
            print(f"  ⚠️  High tool_call/user ratio ({ratio:.1f}) — possible loop without user input")

    if total > 0:
        print(f"\n  📈 Summary: {total} messages, {user_count} user, {assistant_count} assistant, {tool_count} tool")

    db.close()


if __name__ == "__main__":
    main()
