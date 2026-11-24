from __future__ import annotations

import pytest

from opencode_chat_export.errors import ExportFailed
from opencode_chat_export.sources.sqlite import SqliteSource

PARENT = "ses_aaaaaaaaaaaaaaaaaaaaaaaa"
CHILD = "ses_bbbbbbbbbbbbbbbbbbbbbbbb"
OTHER = "ses_cccccccccccccccccccccccc"


def test_list_sessions_sees_all_projects_and_children(fixture_db):
    with SqliteSource(fixture_db) as source:
        refs = source.list_sessions()
    ids = [ref.id for ref in refs]
    assert ids == [OTHER, CHILD, PARENT]
    by_id = {ref.id: ref for ref in refs}
    assert by_id[CHILD].parent_id == PARENT
    assert by_id[PARENT].worktree == "/work/demo"
    assert by_id[OTHER].worktree == "/work/other"


def test_fetch_rebuilds_info_and_messages(fixture_db):
    with SqliteSource(fixture_db) as source:
        doc = source.fetch(PARENT)

    info = doc["info"]
    assert info["id"] == PARENT
    assert info["title"] == "Fix the widget pipeline"
    assert info["projectID"] == "proj_demo"
    assert info["agent"] == "build"
    assert info["cost"] == pytest.approx(0.0123)
    assert info["tokens"] == {
        "input": 100,
        "output": 50,
        "reasoning": 10,
        "cache": {"read": 20, "write": 0},
    }
    assert info["outcome"] == "succeeded"
    assert info["time"] == {
        "created": 1700000000000,
        "updated": 1700000500000,
        "busy": 1700000001000,
        "idle": 1700000500000,
    }
    assert info["location"] == {"directory": "/work/demo"}
    assert info["metadata"] == {"fixture": True}

    messages = doc["messages"]
    assert [message["type"] for message in messages] == [
        "user",
        "assistant",
        "assistant",
        "synthetic",
        "compaction",
        "idle",
    ]
    user = messages[0]
    assert user["text"].startswith("Please fix")
    assert user["files"][0]["data"] == "aGVsbG8="  # lossless JSON keeps file bytes

    assistant = messages[1]
    assert [part["type"] for part in assistant["content"]] == ["reasoning", "tool", "text"]
    tool = assistant["content"][1]
    assert tool["name"] == "read"
    assert tool["state"]["status"] == "completed"
    assert tool["state"]["output"] == "print('hi')\n"

    compaction = messages[4]
    assert compaction["status"] == "completed"
    assert "widget pipeline" in compaction["summary"]
    assert messages[5]["outcome"] == "succeeded"


def test_fetch_child_has_parent_id(fixture_db):
    with SqliteSource(fixture_db) as source:
        doc = source.fetch(CHILD)
    assert doc["info"]["parentID"] == PARENT
    assert len(doc["messages"]) == 2
    assert doc["messages"][1]["content"][0]["text"] == "It uses `widget_helper.py`."


def test_fetch_preserves_unknown_types(fixture_db):
    with SqliteSource(fixture_db) as source:
        doc = source.fetch(OTHER)
    types = [message["type"] for message in doc["messages"]]
    assert "future-event" in types
    assert doc["messages"][2]["content"][0]["type"] == "image"


def test_missing_session_raises(fixture_db):
    with SqliteSource(fixture_db) as source:
        with pytest.raises(ExportFailed):
            source.fetch("ses_does_not_exist")


def test_schema_report(fixture_db):
    with SqliteSource(fixture_db) as source:
        report = source.schema_report()
    assert report["session_table"] == "session_v2"
    assert report["message_table"] == "session_message"
    assert report["part_table"] == "session_part"
    assert report["counts"] == {"sessions": 3, "messages": 11, "parts": 6}
    assert report["child_sessions"] == 1
    assert report["missing_required"] == {}


def test_snapshot_is_a_separate_consistent_file(fixture_db):
    source = SqliteSource(fixture_db)
    try:
        snapshot = source.db_snapshot_path
        assert snapshot is not None
        assert snapshot.exists()
        assert snapshot != fixture_db
        assert source.fetch(PARENT)["info"]["id"] == PARENT
    finally:
        source.close()


def test_snapshot_can_be_disabled(fixture_db):
    with SqliteSource(fixture_db, snapshot=False) as source:
        assert source.db_snapshot_path is None
        assert source.fetch(PARENT)["info"]["id"] == PARENT
