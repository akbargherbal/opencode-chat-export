#!/usr/bin/env python3
"""Build the SQLite fixture used by the tests.

The fixture is generated from the checked-in schema + synthetic sessions instead
of committing a binary database.  That keeps the test data reviewable and makes
it obvious when a future OpenCode schema needs a new fixture.

Usage:
    python tools/make_fixture_db.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "v2.4.1"


def build_fixture_db(out: Path, fixture_dir: Path = FIXTURE_DIR) -> Path:
    schema = (fixture_dir / "schema.sql").read_text(encoding="utf-8")
    data = json.loads((fixture_dir / "sessions.json").read_text(encoding="utf-8"))

    out = Path(out)
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(out))
    try:
        con.executescript(schema)

        for project in data.get("projects", []):
            con.execute(
                "insert into project (id, worktree, vcs, name, time_created, time_updated, "
                "time_initialized, time_active, time_archived, sandboxes, commands) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project["id"],
                    project["worktree"],
                    project.get("vcs"),
                    project.get("name"),
                    project["time_created"],
                    project["time_updated"],
                    project.get("time_initialized"),
                    0,
                    project.get("time_archived"),
                    project.get("sandboxes", "[]"),
                    project.get("commands"),
                ),
            )

        for session in data.get("sessions", []):
            tokens = session.get("tokens", {})
            con.execute(
                "insert into session_v2 (id, project_id, parent_id, slug, directory, title, "
                "version, metadata, cost, tokens_input, tokens_output, tokens_reasoning, "
                "tokens_cache_read, tokens_cache_write, agent, model, time_created, "
                "time_updated, time_busy, time_idle, idle_outcome, resume_attempts) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (
                    session["id"],
                    session["project_id"],
                    session.get("parent_id"),
                    session.get("slug", "fixture"),
                    session["directory"],
                    session.get("title"),
                    session.get("version", "2.4.1"),
                    json.dumps(session["metadata"]) if session.get("metadata") else None,
                    session.get("cost", 0),
                    tokens.get("input", 0),
                    tokens.get("output", 0),
                    tokens.get("reasoning", 0),
                    tokens.get("cache_read", 0),
                    tokens.get("cache_write", 0),
                    session.get("agent"),
                    json.dumps(session["model"]) if session.get("model") else None,
                    session["time_created"],
                    session["time_updated"],
                    session.get("time_busy"),
                    session.get("time_idle"),
                    session.get("idle_outcome"),
                ),
            )

            for message in session.get("messages", []):
                con.execute(
                    "insert into session_message (id, session_id, type, seq, time_created, "
                    "time_updated, data) values (?, ?, ?, ?, ?, ?, ?)",
                    (
                        message["id"],
                        session["id"],
                        message["type"],
                        message["seq"],
                        message["time_created"],
                        message.get("time_updated", message["time_created"]),
                        json.dumps(message["data"]),
                    ),
                )
                for part in message.get("parts", []):
                    con.execute(
                        "insert into session_part (message_id, session_id, ordinal, type, "
                        "call_id, time_created, time_updated, data) values (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            message["id"],
                            session["id"],
                            part["ordinal"],
                            part["type"],
                            part.get("call_id"),
                            part.get("time_created", message["time_created"]),
                            part.get("time_updated", message["time_created"]),
                            json.dumps(part["data"]),
                        ),
                    )

        con.commit()
    finally:
        con.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(FIXTURE_DIR / "opencode.db"),
        help="output database path (default: tests/fixtures/v2.4.1/opencode.db)",
    )
    args = parser.parse_args()
    path = build_fixture_db(Path(args.out))
    print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
