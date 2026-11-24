from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "v2.4.1"
for path in (str(ROOT / "src"), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from tools.make_fixture_db import build_fixture_db  # noqa: E402
from opencode_chat_export.paths import Environment  # noqa: E402


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fixture-db") / "opencode.db"
    return build_fixture_db(out)


@pytest.fixture(scope="session")
def fake_opencode() -> str:
    path = ROOT / "tests" / "fakes" / "opencode"
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def env(tmp_path, fixture_db, fake_opencode) -> Environment:
    return Environment(
        home=tmp_path,
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        db_path=fixture_db,
        opencode_bin=fake_opencode,
        opencode_version="2.4.1",
        host="test-host",
    )


@pytest.fixture
def sample_export() -> dict:
    return json.loads((FIXTURES / "export_sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def fixture_sessions() -> dict:
    return json.loads((FIXTURES / "sessions.json").read_text(encoding="utf-8"))


@pytest.fixture
def clean_fake_env(monkeypatch):
    for name in (
        "FAKE_OPENCODE_MODE",
        "FAKE_OPENCODE_EXPORT_MODE",
        "FAKE_OPENCODE_LIST_MODE",
        "FAKE_OPENCODE_API_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch
