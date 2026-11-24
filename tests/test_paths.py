from __future__ import annotations

import os

from opencode_chat_export.paths import (
    default_config_dir,
    default_data_dir,
    discover_environment,
    parse_version,
    version_tuple,
)
from opencode_chat_export.util import ms_to_iso, parse_time_ms, slugify


def test_version_parsing():
    assert parse_version("opencode v2.4.1") == "2.4.1"
    assert parse_version("2.4.1-beta.1") == "2.4.1-beta.1"
    assert version_tuple("2.4.1") == (2, 4, 1)
    assert version_tuple("nope") is None


def test_xdg_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    assert default_data_dir() == tmp_path / "xdg-data" / "opencode"
    assert default_config_dir() == tmp_path / "xdg-config" / "opencode"


def test_discover_explicit_values(tmp_path, fixture_db, fake_opencode):
    env = discover_environment(
        data_dir=str(tmp_path / "data"),
        db_path=str(fixture_db),
        opencode_bin=fake_opencode,
    )
    assert env.data_dir == tmp_path / "data"
    assert env.db_path == fixture_db
    assert env.opencode_bin == fake_opencode
    assert env.opencode_version == "2.4.1"
    assert env.db_exists


def test_discover_missing_db_is_reported(tmp_path, fake_opencode):
    env = discover_environment(
        data_dir=str(tmp_path / "data"),
        db_path=str(tmp_path / "missing.db"),
        opencode_bin=fake_opencode,
    )
    assert not env.db_exists
    assert any("database not found" in note for note in env.notes)


def test_time_parsing():
    assert parse_time_ms("1700000000000") == 1700000000000
    assert parse_time_ms("2023-11-14") == 1699920000000
    assert parse_time_ms("2023-11-14T22:13:20Z") == 1700000000000
    assert ms_to_iso(1700000000000) == "2023-11-14T22:13:20Z"


def test_slugify():
    assert slugify("Fix the widget pipeline!") == "fix-the-widget-pipeline"
    assert slugify("   ") == "session"
    assert slugify("a" * 100).startswith("a" * 60)
