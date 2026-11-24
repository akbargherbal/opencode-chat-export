"""Optional HTTP-API source.

The CLI is preferred, but OpenCode also exposes the same export over its local
API.  This source is a second, independent path if the CLI spelling changes
again: ``opencode api get /api/session`` and
``opencode api get /api/experimental/session/<id>/export``.

The API is not used for ``--sanitize``: only the CLI's documented flag is
trusted to redact data.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..errors import BackendUnavailable, ExportFailed
from ..util import first_line
from .base import SessionRef, Source


class ApiSource(Source):
    name = "api"
    supports_sanitize = False

    def __init__(self, binary: Optional[str], *, timeout: int = 300) -> None:
        if not binary:
            raise BackendUnavailable("opencode binary not found")
        self.binary = binary
        self.timeout = timeout
        self._env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}

    def _get(self, path: str) -> Any:
        try:
            result = subprocess.run(
                [self.binary, "api", "get", path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=self._env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendUnavailable(f"cannot call opencode api: {exc}") from exc
        if result.returncode != 0:
            raise BackendUnavailable(
                f"opencode api failed for {path}: "
                f"{first_line(result.stderr) or 'exit ' + str(result.returncode)}"
            )
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise BackendUnavailable(f"opencode api returned non-JSON for {path}") from exc

    def list_sessions(self) -> List[SessionRef]:
        path = "/api/session?limit=100"
        refs: List[SessionRef] = []
        seen_cursors = set()
        for _ in range(1000):
            payload = self._get(path)
            data = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(data, list):
                raise BackendUnavailable("opencode api session list was not an array")
            for item in data:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                location = item.get("location") if isinstance(item.get("location"), dict) else {}
                refs.append(
                    SessionRef(
                        id=str(item["id"]),
                        title=str(item.get("title") or ""),
                        directory=str(location.get("directory") or ""),
                        project_id=str(item.get("projectID") or ""),
                        parent_id=item.get("parentID"),
                        created_ms=item.get("time", {}).get("created")
                        if isinstance(item.get("time"), dict)
                        else None,
                        updated_ms=item.get("time", {}).get("updated")
                        if isinstance(item.get("time"), dict)
                        else None,
                    )
                )
            cursor = payload.get("cursor") if isinstance(payload, dict) else None
            next_cursor = cursor.get("next") if isinstance(cursor, dict) else None
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            path = f"/api/session?limit=100&cursor={quote(str(next_cursor), safe='')}"
        return refs

    def fetch(self, session_id: str) -> Dict[str, Any]:
        payload = self._get(
            f"/api/experimental/session/{quote(session_id, safe='')}/export"
        )
        doc = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(doc, dict) or "info" not in doc or "messages" not in doc:
            raise ExportFailed(f"API export returned an unexpected shape for {session_id}")
        return doc
