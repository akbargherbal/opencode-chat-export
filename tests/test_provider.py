from __future__ import annotations

import pytest

from opencode_chat_export.errors import BackendUnavailable, ChatExportError, ExportFailed
from opencode_chat_export.paths import Environment
from opencode_chat_export.provider import Provider

PARENT = "ses_aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "ses_cccccccccccccccccccccccc"


def test_auto_lists_from_sqlite_and_fetches_from_cli(env, clean_fake_env):
    with Provider(env, backend="auto") as provider:
        refs = provider.list_sessions()
        assert len(refs) == 3
        result = provider.fetch(PARENT)
    assert result.backend == "cli"
    assert result.doc["info"]["id"] == PARENT


def test_auto_falls_back_to_sqlite(env, clean_fake_env):
    clean_fake_env.setenv("FAKE_OPENCODE_EXPORT_MODE", "fail")
    clean_fake_env.setenv("FAKE_OPENCODE_API_MODE", "fail")
    with Provider(env, backend="auto") as provider:
        result = provider.fetch(PARENT)
    assert result.backend == "sqlite"
    assert result.doc["info"]["id"] == PARENT


def test_invalid_cli_output_falls_back(env, clean_fake_env):
    clean_fake_env.setenv("FAKE_OPENCODE_EXPORT_MODE", "truncated")
    clean_fake_env.setenv("FAKE_OPENCODE_API_MODE", "fail")
    with Provider(env, backend="auto") as provider:
        result = provider.fetch(PARENT)
    assert result.backend == "sqlite"


def test_explicit_sqlite_backend(env, clean_fake_env):
    with Provider(env, backend="sqlite") as provider:
        assert provider.list_sessions()
        result = provider.fetch(PARENT)
    assert result.backend == "sqlite"


def test_sanitize_uses_cli(env, clean_fake_env):
    with Provider(env, sanitize=True) as provider:
        result = provider.fetch(PARENT)
    assert result.backend == "cli"
    assert result.doc["info"]["sanitized"] is True


def test_sanitize_with_sqlite_is_rejected(env):
    with pytest.raises(ChatExportError):
        Provider(env, backend="sqlite", sanitize=True)


def test_missing_db_uses_cli(tmp_path, fake_opencode, clean_fake_env):
    env = Environment(
        home=tmp_path,
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        db_path=tmp_path / "missing.db",
        opencode_bin=fake_opencode,
        opencode_version="2.4.1",
        host="test-host",
    )
    with Provider(env, backend="auto") as provider:
        assert provider.available_backends == ["cli", "api"]
        assert len(provider.list_sessions()) == 3
        assert provider.fetch(PARENT).backend == "cli"


def test_all_fetch_sources_fail(tmp_path, fake_opencode, clean_fake_env):
    clean_fake_env.setenv("FAKE_OPENCODE_EXPORT_MODE", "fail")
    clean_fake_env.setenv("FAKE_OPENCODE_API_MODE", "fail")
    env = Environment(
        home=tmp_path,
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        db_path=tmp_path / "missing.db",
        opencode_bin=fake_opencode,
        opencode_version="2.4.1",
        host="test-host",
    )
    with Provider(env, backend="auto") as provider:
        with pytest.raises(ExportFailed):
            provider.fetch(PARENT)


def test_no_sources_is_unavailable(tmp_path):
    env = Environment(
        home=tmp_path,
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        db_path=tmp_path / "missing.db",
        opencode_bin=None,
        opencode_version=None,
        host="test-host",
    )
    with pytest.raises(BackendUnavailable):
        Provider(env, backend="auto")


def test_unknown_types_are_kept(env, clean_fake_env):
    with Provider(env, backend="sqlite") as provider:
        result = provider.fetch(OTHER)
    assert any(issue.code == "unknown_message_type" for issue in result.issues)
    assert result.doc["messages"][1]["type"] == "future-event"
