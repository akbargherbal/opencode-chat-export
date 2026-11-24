from __future__ import annotations

import pytest

from opencode_chat_export.errors import BackendUnavailable, ExportFailed
from opencode_chat_export.sources.api import ApiSource
from opencode_chat_export.sources.cli import CliSource

PARENT = "ses_aaaaaaaaaaaaaaaaaaaaaaaa"


def test_v2_export_prefix_and_fetch(fake_opencode, clean_fake_env):
    source = CliSource(fake_opencode)
    assert source.export_prefix() == ["session", "export"]
    assert len(source.list_sessions()) == 3
    doc = source.fetch(PARENT)
    assert doc["info"]["id"] == PARENT
    assert doc["messages"]


def test_legacy_export_prefix(fake_opencode, clean_fake_env):
    clean_fake_env.setenv("FAKE_OPENCODE_MODE", "legacy")
    source = CliSource(fake_opencode)
    assert source.export_prefix() == ["export"]
    assert source.fetch(PARENT)["info"]["id"] == PARENT


def test_no_export_command_is_unavailable(fake_opencode, clean_fake_env):
    clean_fake_env.setenv("FAKE_OPENCODE_MODE", "no-export")
    source = CliSource(fake_opencode)
    with pytest.raises(BackendUnavailable):
        source.export_prefix()


def test_sanitize_flag_is_passed(fake_opencode, clean_fake_env):
    source = CliSource(fake_opencode, sanitize=True)
    doc = source.fetch(PARENT)
    assert doc["info"]["sanitized"] is True
    assert doc["messages"][0]["text"] == "[REDACTED]"


@pytest.mark.parametrize("mode", ["fail", "truncated", "help"])
def test_bad_export_output_is_rejected(fake_opencode, clean_fake_env, mode):
    clean_fake_env.setenv("FAKE_OPENCODE_EXPORT_MODE", mode)
    source = CliSource(fake_opencode)
    with pytest.raises(ExportFailed):
        source.fetch(PARENT)


def test_empty_list_is_empty(fake_opencode, clean_fake_env):
    clean_fake_env.setenv("FAKE_OPENCODE_LIST_MODE", "empty")
    assert CliSource(fake_opencode).list_sessions() == []


def test_list_failure_is_unavailable(fake_opencode, clean_fake_env):
    clean_fake_env.setenv("FAKE_OPENCODE_LIST_MODE", "fail")
    with pytest.raises(BackendUnavailable):
        CliSource(fake_opencode).list_sessions()


def test_api_source_lists_and_fetches(fake_opencode, clean_fake_env):
    source = ApiSource(fake_opencode)
    refs = source.list_sessions()
    assert len(refs) == 3
    doc = source.fetch(PARENT)
    assert doc["info"]["id"] == PARENT


def test_api_failure_is_unavailable(fake_opencode, clean_fake_env):
    clean_fake_env.setenv("FAKE_OPENCODE_API_MODE", "fail")
    with pytest.raises(BackendUnavailable):
        ApiSource(fake_opencode).list_sessions()
