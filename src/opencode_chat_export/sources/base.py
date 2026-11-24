"""Source-independent session metadata and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SessionRef:
    id: str
    title: str = ""
    directory: str = ""
    project_id: str = ""
    parent_id: Optional[str] = None
    created_ms: Optional[int] = None
    updated_ms: Optional[int] = None
    worktree: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "directory": self.directory,
            "project_id": self.project_id,
            "parent_id": self.parent_id,
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "worktree": self.worktree,
        }


@dataclass
class ExportResult:
    session_id: str
    doc: Dict[str, Any]
    backend: str
    ref: SessionRef
    issues: list


class Source:
    """A way to enumerate and fetch sessions."""

    name = "base"
    supports_sanitize = False

    def list_sessions(self) -> list:
        raise NotImplementedError

    def fetch(self, session_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ref_from_export(doc: Dict[str, Any], fallback_id: Optional[str] = None) -> SessionRef:
    info = doc.get("info") if isinstance(doc, dict) else {}
    info = info if isinstance(info, dict) else {}
    time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
    location = info.get("location") if isinstance(info.get("location"), dict) else {}
    return SessionRef(
        id=str(info.get("id") or fallback_id or ""),
        title=str(info.get("title") or ""),
        directory=str(location.get("directory") or ""),
        project_id=str(info.get("projectID") or ""),
        parent_id=info.get("parentID") if isinstance(info.get("parentID"), str) else None,
        created_ms=_as_int(time_info.get("created")),
        updated_ms=_as_int(time_info.get("updated")),
    )
