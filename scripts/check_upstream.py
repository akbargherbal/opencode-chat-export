#!/usr/bin/env python3
"""Dependency-free canary for OpenCode's CLI + database surface.

Checks the things this exporter depends on and exits non-zero when they break:

  * `opencode --version`
  * `opencode session export --help` (or the legacy `opencode export --help`)
  * `opencode session list --format json`
  * the live database's session/message/part tables and critical columns
  * optionally one real export's `info` + `messages` shape

Unknown versions are warnings. Missing critical schema/commands are errors.

Usage:
    python3 scripts/check_upstream.py [--json] [--strict] [--sample N]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED = {
    "version_prefix": "2.4.",
    "session_table": "session_v2",
    "message_table": "session_message",
    "part_table": "session_part",
    "session_columns": ("id", "project_id", "directory", "title", "time_created", "time_updated"),
    "message_columns": ("id", "session_id", "type", "seq", "time_created", "data"),
    "part_columns": ("message_id", "ordinal", "type", "data"),
}

CHECKS: list = []


def check(name: str, status: str, detail: str) -> None:
    CHECKS.append({"name": name, "status": status, "detail": detail})


def run(binary: str, args: list, timeout: int = 60):
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def find_binary(explicit: str | None) -> str | None:
    for candidate in (explicit, os.environ.get("OPENCODE_BIN"), shutil.which("opencode")):
        if candidate:
            resolved = shutil.which(candidate) if os.path.sep not in candidate else candidate
            if resolved and Path(resolved).exists():
                return resolved
    fallback = Path.home() / ".opencode" / "bin" / "opencode"
    return str(fallback) if fallback.exists() else None


def probe_version(binary: str) -> str | None:
    result = run(binary, ["--version"], timeout=20)
    if not result or result.returncode != 0:
        return None
    for token in (result.stdout or "").replace("v", " ").split():
        parts = token.split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts[:2]):
            return token
    return None


def probe_export(binary: str) -> list | None:
    for args in (["session", "export"], ["export"]):
        result = run(binary, [*args, "--help"], timeout=30)
        if result and result.returncode == 0:
            return args
    return None


def probe_list(binary: str) -> list | None:
    result = run(binary, ["session", "list", "--format", "json", "-n", "10"], timeout=60)
    if not result or result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "[]")
    except ValueError:
        return None
    if isinstance(payload, dict):
        payload = payload.get("data", [])
    return payload if isinstance(payload, list) else None


def find_db(binary: str | None) -> Path | None:
    if binary:
        result = run(binary, ["debug", "paths", "db"], timeout=20)
        if result and result.returncode == 0 and (result.stdout or "").strip():
            candidate = Path((result.stdout or "").strip().splitlines()[-1]).expanduser()
            if candidate.exists():
                return candidate
    candidate = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    return candidate if candidate.exists() else None


def missing(table: str, columns: list, expected: tuple) -> list:
    present = {column.lower() for column in columns}
    return [name for name in expected if name.lower() not in present]


def inspect_db(db: Path) -> dict | None:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        check("database", "error", f"cannot open {db}: {exc}")
        return None
    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'")
        }
        if EXPECTED["session_table"] in tables:
            session_table = EXPECTED["session_table"]
        elif "session" in tables:
            session_table = "session"
        else:
            check("database.sessions", "error", "no session_v2 or session table")
            return None

        message_table = (
            EXPECTED["message_table"]
            if EXPECTED["message_table"] in tables
            else "message"
            if "message" in tables
            else None
        )
        part_table = (
            EXPECTED["part_table"]
            if EXPECTED["part_table"] in tables
            else "part"
            if "part" in tables
            else None
        )

        columns = {
            table: [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
            for table in (session_table, message_table, part_table)
            if table
        }
        counts = {}
        for label, table in (
            ("sessions", session_table),
            ("messages", message_table),
            ("parts", part_table),
        ):
            counts[label] = (
                con.execute(f'select count(*) from "{table}"').fetchone()[0] if table else None
            )

        if session_table != EXPECTED["session_table"]:
            check(
                "database.session_table",
                "warn",
                f"found {session_table}, expected {EXPECTED['session_table']} for 2.4.x",
            )
        else:
            check("database.session_table", "ok", session_table)

        if not message_table:
            check("database.messages", "error", "no session_message/message table")
        else:
            absent = missing("message", columns.get(message_table, []), EXPECTED["message_columns"])
            check(
                "database.messages",
                "error" if absent else "ok",
                f"{message_table}; missing={absent}" if absent else f"{message_table} ({counts['messages']} rows)",
            )

        if not part_table:
            check("database.parts", "error", "no session_part/part table")
        else:
            absent = missing("part", columns.get(part_table, []), EXPECTED["part_columns"])
            check(
                "database.parts",
                "error" if absent else "ok",
                f"{part_table}; missing={absent}" if absent else f"{part_table} ({counts['parts']} rows)",
            )

        absent = missing("session", columns.get(session_table, []), EXPECTED["session_columns"])
        check(
            "database.session_columns",
            "error" if absent else "ok",
            f"missing={absent}" if absent else f"{counts['sessions']} session(s)",
        )
        return {"counts": counts, "columns": columns}
    finally:
        con.close()


def sample_export(binary: str, export_args: list, db: Path | None) -> None:
    session_id = None
    if db:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            tables = {
                row[0]
                for row in con.execute("select name from sqlite_master where type='table'")
            }
            table = "session_v2" if "session_v2" in tables else "session"
            row = con.execute(
                f'select id from "{table}" order by time_updated desc limit 1'
            ).fetchone()
            con.close()
            session_id = row[0] if row else None
        except sqlite3.Error:
            session_id = None
    if not session_id:
        check("export.sample", "warn", "no session id available to sample")
        return

    with tempfile.NamedTemporaryFile(prefix="opencode-canary-", suffix=".json", delete=False) as handle:
        path = handle.name
        result = subprocess.run(
            [binary, *export_args, session_id],
            stdout=handle,
            stderr=subprocess.PIPE,
            timeout=120,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
    try:
        if result.returncode != 0:
            check("export.sample", "error", f"exit {result.returncode}: {(result.stderr or b'').decode()[:200]}")
            return
        try:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError as exc:
            check("export.sample", "error", f"non-JSON export: {exc}")
            return
        ok = (
            isinstance(doc, dict)
            and isinstance(doc.get("info"), dict)
            and isinstance(doc.get("messages"), list)
        )
        check(
            "export.sample",
            "ok" if ok else "error",
            f"{session_id}: info+messages" if ok else f"{session_id}: unexpected shape",
        )
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", help="path to opencode")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    parser.add_argument("--sample", type=int, default=1, help="export and inspect N sessions (default 1)")
    args = parser.parse_args(argv)

    binary = find_binary(args.opencode)
    if not binary:
        check("binary", "error", "opencode not found (use --opencode or OPENCODE_BIN)")
    else:
        check("binary", "ok", binary)
        version = probe_version(binary)
        if not version:
            check("version", "warn", "--version did not return a version")
        elif not version.startswith(EXPECTED["version_prefix"]):
            check("version", "warn", f"{version} (canary recorded for {EXPECTED['version_prefix']}x)")
        else:
            check("version", "ok", version)

        export_args = probe_export(binary)
        if not export_args:
            check("cli.export", "error", "neither 'session export' nor 'export' is available")
        else:
            check("cli.export", "ok", "opencode " + " ".join(export_args))

        sessions = probe_list(binary)
        if sessions is None:
            check("cli.list", "warn", "session list --format json failed")
        else:
            check("cli.list", "ok", f"{len(sessions)} session(s) listed")

        db = find_db(binary)
        if not db:
            check("database", "warn", "no opencode.db found")
        else:
            check("database", "ok", str(db))
            inspect_db(db)
            if export_args and args.sample > 0:
                sample_export(binary, export_args, db)

    if args.strict:
        for item in CHECKS:
            if item["status"] == "warn":
                item["status"] = "error"

    ok = not any(item["status"] == "error" for item in CHECKS)
    if args.json:
        print(json.dumps({"ok": ok, "checks": CHECKS}, indent=2))
    else:
        for item in CHECKS:
            print(f"[{item['status'].upper():5}] {item['name']}: {item['detail']}")
        print("\nresult:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
