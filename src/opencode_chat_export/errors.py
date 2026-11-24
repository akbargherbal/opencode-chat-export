"""Exception types used across the package."""

from __future__ import annotations


class ChatExportError(Exception):
    """Base class for expected, user-facing failures."""


class BackendUnavailable(ChatExportError):
    """A data source (CLI, HTTP API, or SQLite) cannot be used."""


class ExportFailed(ChatExportError):
    """A specific session could not be exported by any available source."""


class ContractError(ChatExportError):
    """An exported document does not match the expected OpenCode shape."""
