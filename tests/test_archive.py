from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from opencode_chat_export.archive import ArchiveOptions, build_archive
from opencode_chat_export.errors import ChatExportError
from opencode_chat_export.provider import Provider
from opencode_chat_export.sources.base import SessionRef
from opencode_chat_export.validate import validate_path

PARENT = "ses_aaaaaaaaaaaaaaaaaaaaaaaa"
CHILD = "ses_bbbbbbbbbbbbbbbbbbbbbbbb"
OTHER = "ses_cccccccccccccccccccccccc"


def _build(tmp_path, env, *, refs=None, **kwargs):
    with Provider(env, backend="sqlite") as provider:
        selected = provider.list_sessions() if refs is None else refs
        options = ArchiveOptions(
            out=tmp_path / "chats.zip",
            opencode={"version": env.opencode_version},
            **kwargs,
        )
        return build_archive(provider, selected, options)


def _read_json(zf, name):
    return json.loads(zf.read(name).decode("utf-8"))


def test_archive_layout_and_index(tmp_path, env):
    result = _build(tmp_path, env)
    assert result.session_count == 3
    assert result.path.exists()

    with zipfile.ZipFile(result.path) as zf:
        names = set(zf.namelist())
        assert "chats/README.md" in names
        assert "chats/manifest.json" in names
        assert "chats/index.json" in names
        assert "chats/index.md" in names
        assert "chats/schema/session-export.schema.json" in names
        assert len([name for name in names if name.startswith("chats/sessions/") and name.endswith(".json")]) == 3
        assert len([name for name in names if name.startswith("chats/sessions/") and name.endswith(".md")]) == 3

        index = _read_json(zf, "chats/index.json")
        manifest = _read_json(zf, "chats/manifest.json")
        assert index["schema_version"] == 1
        assert len(index["sessions"]) == 3
        assert {item["id"] for item in index["sessions"]} == {PARENT, CHILD, OTHER}
        assert manifest["totals"]["sessions"] == 3
        assert manifest["backends"] == {"sqlite": 3}
        assert manifest["opencode"]["version"] == "2.4.1"

        child = next(item for item in index["sessions"] if item["id"] == CHILD)
        assert child["parent_id"] == PARENT

        for entry in manifest["files"]:
            raw = zf.read("chats/" + entry["path"])
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
            assert len(raw) == entry["bytes"]

        for entry in manifest["sessions"]:
            doc = _read_json(zf, "chats/" + entry["file"])
            assert doc["info"]["id"] == entry["id"]
            assert len(doc["messages"]) == entry["messages"]

    report = validate_path(result.path)
    assert report.ok, report.render()


def test_transcript_is_readable_and_omits_embedded_bytes(tmp_path, env):
    result = _build(tmp_path, env)
    with zipfile.ZipFile(result.path) as zf:
        index = _read_json(zf, "chats/index.json")
        parent = next(item for item in index["sessions"] if item["id"] == PARENT)
        text = zf.read("chats/" + parent["transcript"]).decode("utf-8")
    assert "# Fix the widget pipeline" in text
    assert "Please fix the widget pipeline." in text
    assert "Tool: `read`" in text
    assert "print('hi')" in text
    assert "aGVsbG8=" not in text  # base64 file data is not copied into Markdown
    assert "It uses `widget_helper.py`" not in text


def test_no_transcripts(tmp_path, env):
    result = _build(tmp_path, env, transcripts=False)
    with zipfile.ZipFile(result.path) as zf:
        names = zf.namelist()
        index = _read_json(zf, "chats/index.json")
    assert not any(name.endswith(".md") and "/sessions/" in name for name in names)
    assert all(item["transcript"] is None for item in index["sessions"])


def test_include_db_snapshot(tmp_path, env):
    result = _build(tmp_path, env, include_db=True)
    with zipfile.ZipFile(result.path) as zf:
        assert "chats/db/opencode.db" in zf.namelist()
        manifest = _read_json(zf, "chats/manifest.json")
    assert any(entry["path"] == "db/opencode.db" for entry in manifest["files"])


def test_deterministic_archive_bytes(tmp_path, env, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    first = _build(tmp_path, env, deterministic=True)
    first_bytes = first.path.read_bytes()
    second = _build(tmp_path, env, deterministic=True)
    assert first_bytes == second.path.read_bytes()


def test_empty_archive_requires_opt_in(tmp_path, env):
    with Provider(env, backend="sqlite") as provider:
        with pytest.raises(ChatExportError):
            build_archive(provider, [], ArchiveOptions(out=tmp_path / "empty.zip"))
        result = build_archive(
            provider,
            [],
            ArchiveOptions(out=tmp_path / "empty.zip", allow_empty=True),
        )
    assert result.session_count == 0
    with zipfile.ZipFile(result.path) as zf:
        index = _read_json(zf, "chats/index.json") if "chats/index.json" in zf.namelist() else _read_json(zf, "empty/index.json")
    assert index["sessions"] == []


def test_missing_session_is_collected(tmp_path, env):
    refs = [
        SessionRef(id=PARENT),
        SessionRef(id="ses_missing"),
    ]
    result = _build(tmp_path, env, refs=refs)
    assert result.session_count == 1
    assert len(result.errors) == 1
    assert "ses_missing" in result.errors[0]


def test_fail_fast_raises(tmp_path, env):
    with Provider(env, backend="sqlite") as provider:
        with pytest.raises(ChatExportError):
            build_archive(
                provider,
                [SessionRef(id="ses_missing")],
                ArchiveOptions(out=tmp_path / "x.zip", fail_fast=True),
            )


def test_unknown_types_are_warnings_in_manifest(tmp_path, env):
    result = _build(tmp_path, env, refs=[SessionRef(id=OTHER)])
    assert result.session_count == 1
    assert result.warnings
    with zipfile.ZipFile(result.path) as zf:
        manifest = _read_json(zf, "chats/manifest.json")
    assert any(issue["code"] == "unknown_message_type" for issue in manifest["issues"])
    assert validate_path(result.path).ok
    assert not validate_path(result.path, strict=True).ok
