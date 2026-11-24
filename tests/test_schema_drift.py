from __future__ import annotations

import dataclasses
import shutil
import sqlite3

from opencode_chat_export.doctor import run_doctor
from opencode_chat_export.provider import Provider
from opencode_chat_export.sources.sqlite import SqliteSource

PARENT = "ses_aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "ses_cccccccccccccccccccccccc"


def _copy_db(fixture_db, tmp_path):
    target = tmp_path / "drift.db"
    shutil.copy2(fixture_db, target)
    return target


def _alter(db, sql):
    con = sqlite3.connect(str(db))
    try:
        con.execute(sql)
        con.commit()
    finally:
        con.close()


def test_renamed_data_column_still_works(fixture_db, tmp_path, env):
    db = _copy_db(fixture_db, tmp_path)
    _alter(db, "alter table session_message rename column data to payload")

    with SqliteSource(db) as source:
        report = source.schema_report()
        assert report["missing_required"] == {}
        doc = source.fetch(PARENT)
    assert doc["messages"][0]["text"].startswith("Please fix")

    drifted = dataclasses.replace(env, db_path=db)
    with Provider(drifted, backend="sqlite") as provider:
        assert provider.fetch(PARENT).backend == "sqlite"


def test_missing_type_column_is_a_doctor_error(fixture_db, tmp_path, env):
    db = _copy_db(fixture_db, tmp_path)
    _alter(db, "alter table session_message rename column type to kind")

    with SqliteSource(db) as source:
        report = source.schema_report()
    assert "type" in report["missing_required"]["session_message"]

    drifted = dataclasses.replace(env, db_path=db)
    doctor = run_doctor(drifted, sample=0)
    assert not doctor.ok
    assert any(check.name == "sqlite_schema" and check.status == "error" for check in doctor.checks)


def test_missing_session_table_is_a_doctor_error(fixture_db, tmp_path, env):
    db = _copy_db(fixture_db, tmp_path)
    _alter(db, "alter table session_v2 rename to sessions_old")

    drifted = dataclasses.replace(env, db_path=db)
    doctor = run_doctor(drifted, sample=0)
    assert not doctor.ok
    assert any(check.name == "sqlite_schema" and check.status == "error" for check in doctor.checks)


def test_extra_column_is_harmless(fixture_db, tmp_path, env):
    db = _copy_db(fixture_db, tmp_path)
    _alter(db, "alter table session_message add column future_field text")

    drifted = dataclasses.replace(env, db_path=db)
    with SqliteSource(db) as source:
        assert source.schema_report()["missing_required"] == {}
        assert source.fetch(PARENT)["info"]["id"] == PARENT
    doctor = run_doctor(drifted, sample=1)
    assert doctor.ok


def test_unknown_version_warns_but_does_not_fail(env):
    drifted = dataclasses.replace(env, opencode_version="9.9.9")
    doctor = run_doctor(drifted, sample=0)
    assert doctor.ok
    assert any(
        check.name == "version_contract" and check.status == "warn" for check in doctor.checks
    )


def test_unknown_message_type_is_kept(env):
    with Provider(env, backend="sqlite") as provider:
        result = provider.fetch(OTHER)
    assert result.doc["messages"][1]["type"] == "future-event"
    assert any(issue.code == "unknown_message_type" for issue in result.issues)
