from __future__ import annotations

import json
import zipfile

from opencode_chat_export.archive import ArchiveOptions, build_archive
from opencode_chat_export.provider import Provider
from opencode_chat_export.sources.base import SessionRef
from opencode_chat_export.validate import validate_json_file, validate_path

PARENT = "ses_aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "ses_cccccccccccccccccccccccc"


def _archive(tmp_path, env, refs=None):
    with Provider(env, backend="sqlite") as provider:
        selected = provider.list_sessions() if refs is None else refs
        return build_archive(provider, selected, ArchiveOptions(out=tmp_path / "chats.zip"))


def test_validate_sample_json(fixture_dir):
    report = validate_json_file(fixture_dir / "export_sample.json")
    assert report.ok, report.render()


def test_validate_unknown_json(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"hello": "world"}', encoding="utf-8")
    report = validate_path(path)
    assert not report.ok
    assert any(issue.code == "unknown_json_shape" for issue in report.issues)


def test_validate_archive(tmp_path, env):
    result = _archive(tmp_path, env)
    report = validate_path(result.path)
    assert report.ok, report.render()
    assert "index: present" in report.checks


def test_tampered_session_fails_checksum(tmp_path, env):
    result = _archive(tmp_path, env)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(result.path) as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith(PARENT + ".json"):
                data = data.replace(b"Fix the widget pipeline", b"Tampered title")
            dst.writestr(item, data)
    report = validate_path(tampered)
    assert not report.ok
    assert any(issue.code == "checksum_mismatch" for issue in report.issues)


def test_missing_session_file_fails(tmp_path, env):
    result = _archive(tmp_path, env)
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(result.path) as src, zipfile.ZipFile(broken, "w") as dst:
        for item in src.infolist():
            if item.filename.endswith(PARENT + ".json"):
                continue
            dst.writestr(item, src.read(item.filename))
    report = validate_path(broken)
    assert not report.ok
    assert any(issue.code == "missing_session_file" for issue in report.issues)


def test_zip_without_manifest_fails(tmp_path):
    path = tmp_path / "no-manifest.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("root/index.json", json.dumps({"sessions": []}))
    report = validate_path(path)
    assert not report.ok
    assert any(issue.code == "missing_manifest" for issue in report.issues)


def test_strict_unknown_types_fail(tmp_path, env):
    result = _archive(tmp_path, env, refs=[SessionRef(id=OTHER)])
    assert validate_path(result.path).ok
    strict = validate_path(result.path, strict=True)
    assert not strict.ok
    assert any(issue.code == "unknown_message_type" for issue in strict.issues)
