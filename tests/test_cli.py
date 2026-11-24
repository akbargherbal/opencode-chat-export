from __future__ import annotations

import json

from opencode_chat_export.cli import main

PARENT = "ses_aaaaaaaaaaaaaaaaaaaaaaaa"
CHILD = "ses_bbbbbbbbbbbbbbbbbbbbbbbb"
OTHER = "ses_cccccccccccccccccccccccc"


def _args(tmp_path, fixture_db, fake_opencode):
    return [
        "--db",
        str(fixture_db),
        "--opencode",
        fake_opencode,
        "--backend",
        "sqlite",
    ]


def test_version(capsys):
    assert main(["version"]) == 0
    assert "opencode-chat-export" in capsys.readouterr().out


def test_list_json(capsys, fixture_db, fake_opencode):
    code = main(["list", "--json", *_args(None, fixture_db, fake_opencode)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert {item["id"] for item in payload} == {PARENT, CHILD, OTHER}


def test_list_table(capsys, fixture_db, fake_opencode):
    code = main(["list", *_args(None, fixture_db, fake_opencode)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Fix the widget pipeline" in out
    assert "child " in out


def test_export_creates_zip_and_summary(tmp_path, capsys, fixture_db, fake_opencode):
    out = tmp_path / "chats.zip"
    code = main(
        [
            "export",
            "--json",
            *_args(tmp_path, fixture_db, fake_opencode),
            "-o",
            str(out),
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["sessions"] == 3
    assert summary["messages"] == 11
    assert summary["archive"] == str(out)
    assert out.exists()


def test_export_existing_requires_force(tmp_path, capsys, fixture_db, fake_opencode):
    out = tmp_path / "chats.zip"
    args = ["export", *_args(tmp_path, fixture_db, fake_opencode), "-o", str(out)]
    assert main(args) == 0
    assert main(args) == 1
    assert "already exists" in capsys.readouterr().err
    assert main([*args, "--force"]) == 0


def test_export_selected_session(tmp_path, capsys, fixture_db, fake_opencode):
    out = tmp_path / "one.zip"
    code = main(
        [
            "export",
            "--json",
            "--session",
            PARENT,
            *_args(tmp_path, fixture_db, fake_opencode),
            "-o",
            str(out),
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["sessions"] == 1


def test_export_shorthand_session_id(tmp_path, fixture_db, fake_opencode):
    out = tmp_path / "shorthand.zip"
    code = main([PARENT, *_args(tmp_path, fixture_db, fake_opencode), "-o", str(out)])
    assert code == 0
    assert out.exists()


def test_export_directory_filter(tmp_path, capsys, fixture_db, fake_opencode):
    out = tmp_path / "demo.zip"
    code = main(
        [
            "export",
            "--json",
            "--directory",
            "/work/demo",
            *_args(tmp_path, fixture_db, fake_opencode),
            "-o",
            str(out),
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["sessions"] == 2


def test_export_since_filter(tmp_path, capsys, fixture_db, fake_opencode):
    out = tmp_path / "recent.zip"
    code = main(
        [
            "export",
            "--json",
            "--since",
            "2023-11-14T22:20:00Z",
            *_args(tmp_path, fixture_db, fake_opencode),
            "-o",
            str(out),
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["sessions"] == 1


def test_export_allow_empty(tmp_path, capsys, fixture_db, fake_opencode):
    out = tmp_path / "empty.zip"
    code = main(
        [
            "export",
            "--json",
            "--allow-empty",
            "--directory",
            "/does/not/exist",
            *_args(tmp_path, fixture_db, fake_opencode),
            "-o",
            str(out),
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["sessions"] == 0


def test_sanitize_with_sqlite_is_rejected(tmp_path, capsys, fixture_db, fake_opencode):
    out = tmp_path / "x.zip"
    code = main(
        [
            "export",
            "--sanitize",
            *_args(tmp_path, fixture_db, fake_opencode),
            "-o",
            str(out),
        ]
    )
    assert code == 1
    assert "sanitize" in capsys.readouterr().err.lower()


def test_doctor_json(tmp_path, capsys, fixture_db, fake_opencode):
    code = main(["doctor", "--json", *_args(tmp_path, fixture_db, fake_opencode)])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    names = {check["name"] for check in report["checks"]}
    assert {"database", "sqlite_schema", "cli_export", "sample_export"} <= names


def test_doctor_dump_schema(tmp_path, capsys, fixture_db, fake_opencode):
    dump = tmp_path / "doctor.json"
    code = main(
        [
            "doctor",
            "--dump-schema",
            str(dump),
            *_args(tmp_path, fixture_db, fake_opencode),
        ]
    )
    assert code == 0
    assert dump.exists()
    payload = json.loads(dump.read_text())
    assert payload["schema"]["session_table"] == "session_v2"


def test_validate_command(tmp_path, capsys, fixture_db, fake_opencode):
    out = tmp_path / "chats.zip"
    assert main(["export", *_args(tmp_path, fixture_db, fake_opencode), "-o", str(out)]) == 0
    capsys.readouterr()
    code = main(["validate", "--json", str(out)])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
