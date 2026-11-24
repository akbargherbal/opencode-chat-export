"""Build a static export document for the fake OpenCode CLI.

Deliberately independent of the production SQLite reconstruction: the fake CLI
returns the hand-written ``export_sample.json`` with the requested session id.
That keeps CLI-source tests honest (they exercise command construction and file
handling, not the DB reader).
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "v2.4.1"


def load_sessions() -> dict:
    return json.loads((FIXTURE_DIR / "sessions.json").read_text(encoding="utf-8"))


def load_sample() -> dict:
    return json.loads((FIXTURE_DIR / "export_sample.json").read_text(encoding="utf-8"))


def doc_for(session_id: str) -> dict:
    sessions = {item["id"]: item for item in load_sessions()["sessions"]}
    session = sessions.get(session_id, {})
    doc = load_sample()
    doc["info"]["id"] = session_id
    doc["info"]["title"] = session.get("title") or f"Fixture {session_id}"
    doc["info"]["location"] = {"directory": session.get("directory", "/work/demo")}
    doc["info"]["projectID"] = session.get("project_id", "proj_demo")
    if session.get("parent_id"):
        doc["info"]["parentID"] = session["parent_id"]
    doc["info"]["time"]["created"] = session.get("time_created", 1700000000000)
    doc["info"]["time"]["updated"] = session.get("time_updated", 1700000500000)
    return doc


def list_sessions() -> list:
    return [
        {
            "id": session["id"],
            "title": session.get("title", ""),
            "created": session.get("time_created"),
            "updated": session.get("time_updated"),
            "projectId": session.get("project_id"),
            "directory": session.get("directory"),
        }
        for session in load_sessions()["sessions"]
    ]
