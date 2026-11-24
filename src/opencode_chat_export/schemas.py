"""Load the JSON Schemas shipped inside the package (used in every archive)."""

from __future__ import annotations

import json
from typing import Dict

from importlib import resources

SCHEMA_FILES = (
    "session-export.schema.json",
    "manifest.schema.json",
    "index.schema.json",
)


def load_schema(filename: str) -> dict:
    path = resources.files("opencode_chat_export").joinpath("schema", filename)
    return json.loads(path.read_text(encoding="utf-8"))


def all_schemas() -> Dict[str, dict]:
    return {filename: load_schema(filename) for filename in SCHEMA_FILES}
