"""Use OpenCode's own CLI as the authoritative exporter.

``opencode session export <id>`` is the closest thing to a supported format, so
it is the first fetch source.  Two hard-won details are preserved from
vm-continuity:

* stdout is redirected to a **file**, never a pipe.  OpenCode has produced
  truncated exports when stdout is a pipe.
* the command spelling is probed, not assumed: 2.0+ uses
  ``opencode session export`` while older releases used ``opencode export``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import BackendUnavailable, ExportFailed
from ..util import first_line
from .base import SessionRef, Source


class CliSource(Source):
    name = "cli"
    supports_sanitize = True

    def __init__(self, binary: Optional[str], *, timeout: int = 300, sanitize: bool = False) -> None:
        if not binary:
            raise BackendUnavailable("opencode binary not found")
        self.binary = binary
        self.timeout = timeout
        self.sanitize = sanitize
        self._export_prefix: Optional[List[str]] = None
        self._env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}

    # ------------------------------------------------------------------ probes
    def _run(self, args: List[str], *, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
            env=self._env,
        )

    def _probe(self, args: List[str]) -> bool:
        try:
            result = self._run([*args, "--help"], timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def export_prefix(self) -> List[str]:
        if self._export_prefix is None:
            if self._probe(["session", "export"]):
                self._export_prefix = ["session", "export"]
            elif self._probe(["export"]):
                self._export_prefix = ["export"]
            else:
                raise BackendUnavailable(
                    "cannot find an export command; tried 'opencode session export' "
                    "and 'opencode export'"
                )
        return self._export_prefix

    # ------------------------------------------------------------------ public
    def list_sessions(self) -> List[SessionRef]:
        candidates = [
            ["session", "list", "--format", "json", "-n", "100000"],
            ["session", "list", "--format", "json"],
            ["session", "list", "--format", "json", "--max-count", "100000"],
            ["list", "--format", "json"],
        ]
        last_error = ""
        for args in candidates:
            try:
                result = self._run(args)
            except (OSError, subprocess.SubprocessError) as exc:
                last_error = str(exc)
                continue
            if result.returncode != 0:
                last_error = first_line(result.stderr) or first_line(result.stdout)
                continue
            text = (result.stdout or "").strip()
            if not text:
                return []
            try:
                payload = json.loads(text)
            except ValueError:
                last_error = "session list did not return JSON"
                continue
            if isinstance(payload, dict):
                payload = payload.get("data", [])
            if not isinstance(payload, list):
                last_error = "session list JSON was not an array"
                continue
            refs = []
            for item in payload:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                location = item.get("location") if isinstance(item.get("location"), dict) else {}
                refs.append(
                    SessionRef(
                        id=str(item["id"]),
                        title=str(item.get("title") or ""),
                        directory=str(item.get("directory") or location.get("directory") or ""),
                        project_id=str(item.get("projectId") or item.get("projectID") or ""),
                        parent_id=item.get("parentID") or item.get("parent_id"),
                        created_ms=item.get("created") or item.get("time_created"),
                        updated_ms=item.get("updated") or item.get("time_updated"),
                        worktree=str(item.get("worktree") or ""),
                    )
                )
            return refs
        raise BackendUnavailable(f"cannot list sessions via CLI: {last_error or 'no working command'}")

    def fetch(self, session_id: str) -> Dict[str, Any]:
        prefix = self.export_prefix()
        args = [*prefix, session_id]
        if self.sanitize:
            args.append("--sanitize")

        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="opencode-export-", suffix=".json", delete=False
            ) as handle:
                tmp_name = handle.name
                result = subprocess.run(
                    [self.binary, *args],
                    stdout=handle,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                    env=self._env,
                )
            stderr = (result.stderr or b"").decode("utf-8", "replace")
            if result.returncode != 0:
                raise ExportFailed(
                    f"opencode export failed for {session_id}: "
                    f"{first_line(stderr) or 'exit ' + str(result.returncode)}"
                )
            raw = Path(tmp_name).read_bytes()
        except subprocess.TimeoutExpired as exc:
            raise ExportFailed(f"opencode export timed out for {session_id}") from exc
        except OSError as exc:
            raise ExportFailed(f"cannot run opencode export: {exc}") from exc
        finally:
            if tmp_name:
                try:
                    Path(tmp_name).unlink()
                except OSError:
                    pass

        if not raw.strip():
            raise ExportFailed(f"opencode export returned an empty file for {session_id}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            preview = first_line(raw.decode("utf-8", "replace"), 200)
            raise ExportFailed(
                f"opencode export returned non-JSON for {session_id}: {preview!r}"
            ) from exc

        # Be liberal if a future CLI wraps the payload in {"data": ...}.
        if isinstance(payload, dict) and "info" not in payload:
            data = payload.get("data")
            if isinstance(data, dict) and "info" in data and "messages" in data:
                payload = data
        if not isinstance(payload, dict) or "info" not in payload or "messages" not in payload:
            raise ExportFailed(f"opencode export returned an unexpected shape for {session_id}")
        return payload
