"""Read sessions directly from OpenCode's SQLite database.

This is the offline fallback and the source used to *enumerate* sessions: the
CLI's ``session list`` is project-scoped and hides child/subagent sessions, while
``select id from session_v2`` sees every session.

The live database is never opened for writing.  A consistent snapshot is taken
with ``sqlite3.Connection.backup``; if that fails (for example a read-only WAL
directory), the database and its sidecars are copied to a temporary directory
and opened there.

Schema drift is contained in this module: table/column names are discovered with
``sqlite_master``/``PRAGMA table_info`` instead of being hard-coded, and
``schema_report()`` tells ``doctor`` exactly what changed.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

from ..errors import BackendUnavailable, ExportFailed
from .base import SessionRef, Source

# Minimal columns needed to rebuild an export.  Older releases may use a
# different table or column spelling; the detector below tolerates the common
# alternatives, and anything it cannot map becomes a clear doctor error.
REQUIRED_COLUMNS = {
    "session_v2": ("id", "title", "time_created", "time_updated"),
    "session": ("id", "title", "time_created", "time_updated"),
    "session_message": ("id", "session_id", "type", "data"),
    "message": ("id", "session_id", "type", "data"),
    "session_part": ("message_id", "type", "data"),
    "part": ("message_id", "type", "data"),
}


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _missing_critical(table: str, columns: Iterable[str]) -> List[str]:
    """Columns whose absence breaks reconstruction, allowing known spellings."""
    present = {column.lower() for column in columns}
    missing: List[str] = []
    if table in ("session_v2", "session"):
        for name in ("id",):
            if name not in present:
                missing.append(name)
        return missing
    if table in ("session_message", "message"):
        for name in ("id", "type"):
            if name not in present:
                missing.append(name)
        if not any(name in present for name in ("session_id", "sessionid")):
            missing.append("session_id")
        if not any(name in present for name in ("data", "payload", "content")):
            missing.append("data")
        return missing
    if table in ("session_part", "part"):
        if not any(name in present for name in ("message_id", "messageid")):
            missing.append("message_id")
        if "type" not in present:
            missing.append("type")
        if not any(name in present for name in ("data", "payload", "content")):
            missing.append("data")
        return missing
    return missing


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SqliteSource(Source):
    name = "sqlite"
    supports_sanitize = False

    def __init__(self, db_path: Path, *, snapshot: bool = True) -> None:
        self.db_path = Path(db_path).expanduser()
        if not self.db_path.exists():
            raise BackendUnavailable(f"OpenCode database not found: {self.db_path}")
        self._tmp_dir: Optional[tempfile.TemporaryDirectory] = None
        self._snapshot_path: Optional[Path] = None
        self._con: Optional[sqlite3.Connection] = None

        if snapshot:
            self._snapshot_path = self._take_snapshot()
            open_path = self._snapshot_path
        else:
            open_path = self.db_path

        try:
            self._con = sqlite3.connect(str(open_path))
            self._con.row_factory = sqlite3.Row
            self._con.execute("PRAGMA query_only = ON")
        except sqlite3.Error as exc:
            self.close()
            raise BackendUnavailable(f"cannot open OpenCode database {open_path}: {exc}") from exc

        self._detect_schema()

    # ------------------------------------------------------------------ setup
    def _take_snapshot(self) -> Path:
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="opencode-export-db-")
        dest = Path(self._tmp_dir.name) / "opencode.db"
        try:
            uri = f"file:{quote(str(self.db_path), safe='/')}?mode=ro"
            src = sqlite3.connect(uri, uri=True)
            try:
                dst = sqlite3.connect(str(dest))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            if dest.exists() and dest.stat().st_size > 0:
                return dest
        except sqlite3.Error:
            pass

        # Fallback: copy the database and WAL/SHM sidecars.  The backup API is
        # preferred because it gives a transactionally consistent snapshot.
        shutil.copy2(self.db_path, dest)
        for suffix in ("-wal", "-shm"):
            side = Path(str(self.db_path) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(dest) + suffix))
        return dest

    def _table_names(self) -> set:
        assert self._con is not None
        return {
            row[0]
            for row in self._con.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }

    def _columns(self, table: str) -> List[str]:
        assert self._con is not None
        return [row[1] for row in self._con.execute(f"PRAGMA table_info({_q(table)})")]

    @staticmethod
    def _pick(columns: Iterable[str], *names: str) -> Optional[str]:
        by_lower = {column.lower(): column for column in columns}
        for name in names:
            if name in by_lower:
                return by_lower[name]
        return None

    def _detect_schema(self) -> None:
        tables = self._table_names()
        self.tables = sorted(tables)
        self.session_table = next((t for t in ("session_v2", "session") if t in tables), None)
        self.message_table = next((t for t in ("session_message", "message") if t in tables), None)
        self.part_table = next((t for t in ("session_part", "part") if t in tables), None)

        if not self.session_table:
            raise BackendUnavailable(
                "no OpenCode session table found (looked for session_v2, session); "
                "the database layout may have changed"
            )

        self.session_columns = self._columns(self.session_table)
        self.message_columns = self._columns(self.message_table) if self.message_table else []
        self.part_columns = self._columns(self.part_table) if self.part_table else []

        self.session_id_col = "id"
        self.project_col = self._pick(self.session_columns, "project_id", "projectID", "projectId")
        self.directory_col = self._pick(self.session_columns, "directory")
        self.title_col = self._pick(self.session_columns, "title")
        self.parent_col = self._pick(self.session_columns, "parent_id", "parentID", "parentId")
        self.created_col = self._pick(self.session_columns, "time_created", "created")
        self.updated_col = self._pick(self.session_columns, "time_updated", "updated")

        self.msg_session_col = (
            self._pick(self.message_columns, "session_id", "sessionID", "sessionId")
            if self.message_columns
            else None
        )
        self.msg_data_col = (
            self._pick(self.message_columns, "data", "payload", "content")
            if self.message_columns
            else None
        )
        self.msg_type_col = (
            self._pick(self.message_columns, "type") if self.message_columns else None
        )
        self.msg_seq_col = (
            self._pick(self.message_columns, "seq", "ordinal")
            if self.message_columns
            else None
        )
        self.msg_time_col = (
            self._pick(self.message_columns, "time_created", "created")
            if self.message_columns
            else None
        )

        self.part_message_col = (
            self._pick(self.part_columns, "message_id", "messageID", "messageId")
            if self.part_columns
            else None
        )
        self.part_ordinal_col = (
            self._pick(self.part_columns, "ordinal", "seq") if self.part_columns else None
        )
        self.part_type_col = (
            self._pick(self.part_columns, "type") if self.part_columns else None
        )
        self.part_data_col = (
            self._pick(self.part_columns, "data", "payload", "content")
            if self.part_columns
            else None
        )

    def _require_message_schema(self) -> None:
        if not self.message_table or not self.msg_session_col or not self.msg_data_col:
            raise BackendUnavailable(
                "direct SQLite reconstruction needs a message table with "
                "session_id and data columns; use the CLI/API backend instead"
            )

    # ------------------------------------------------------------------ public
    @property
    def db_snapshot_path(self) -> Optional[Path]:
        return self._snapshot_path

    def list_sessions(self) -> List[SessionRef]:
        assert self._con is not None
        aliases = {
            "id": self.session_id_col,
            "title": self.title_col,
            "directory": self.directory_col,
            "project_id": self.project_col,
            "parent_id": self.parent_col,
            "created": self.created_col,
            "updated": self.updated_col,
        }
        select_parts = [
            f"s.{_q(column)} as {_q(alias)}" if column else f"NULL as {_q(alias)}"
            for alias, column in aliases.items()
        ]

        worktree_select = "NULL as worktree"
        join = ""
        if "project" in self._table_names():
            project_columns = self._columns("project")
            project_id = self._pick(project_columns, "id")
            worktree = self._pick(project_columns, "worktree")
            if project_id and worktree and self.project_col:
                join = f" left join {_q('project')} p on p.{_q(project_id)} = s.{_q(self.project_col)}"
                worktree_select = f"p.{_q(worktree)} as worktree"

        order = f" order by s.{_q(self.created_col)} desc" if self.created_col else ""
        sql = (
            f"select {', '.join(select_parts)}, {worktree_select} "
            f"from {_q(self.session_table)} s{join}{order}"
        )
        try:
            rows = self._con.execute(sql).fetchall()
        except sqlite3.Error as exc:
            raise BackendUnavailable(f"cannot list sessions from {self.session_table}: {exc}") from exc

        refs = []
        for row in rows:
            refs.append(
                SessionRef(
                    id=str(row["id"]),
                    title=str(row["title"] or ""),
                    directory=str(row["directory"] or ""),
                    project_id=str(row["project_id"] or ""),
                    parent_id=row["parent_id"],
                    created_ms=_as_int(row["created"]),
                    updated_ms=_as_int(row["updated"]),
                    worktree=str(row["worktree"] or ""),
                )
            )
        return refs

    def fetch(self, session_id: str) -> Dict[str, Any]:
        assert self._con is not None
        try:
            row = self._con.execute(
                f"select * from {_q(self.session_table)} where {_q(self.session_id_col)} = ?",
                (session_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise ExportFailed(f"cannot read session {session_id}: {exc}") from exc
        if row is None:
            raise ExportFailed(f"session not found in database: {session_id}")

        info = self._build_info(row)
        messages = self._build_messages(session_id)
        return {"info": info, "messages": messages}

    def _build_info(self, row: sqlite3.Row) -> Dict[str, Any]:
        def get(*names: str) -> Any:
            for name in names:
                if name in row.keys():
                    return row[name]
            return None

        directory = get("directory") or ""
        info: Dict[str, Any] = {
            "id": str(get("id") or ""),
            "projectID": str(get("project_id", "projectID") or ""),
            "cost": get("cost") if get("cost") is not None else 0,
            "title": get("title") or "(untitled)",
            "location": {"directory": str(directory)} if directory else {},
        }

        agent = get("agent")
        if agent is not None:
            info["agent"] = agent

        model = _loads(get("model"))
        if model is not None:
            info["model"] = model

        token_columns = {
            "input": get("tokens_input"),
            "output": get("tokens_output"),
            "reasoning": get("tokens_reasoning"),
            "cache_read": get("tokens_cache_read"),
            "cache_write": get("tokens_cache_write"),
        }
        if any(value is not None for value in token_columns.values()):
            info["tokens"] = {
                "input": int(token_columns["input"] or 0),
                "output": int(token_columns["output"] or 0),
                "reasoning": int(token_columns["reasoning"] or 0),
                "cache": {
                    "read": int(token_columns["cache_read"] or 0),
                    "write": int(token_columns["cache_write"] or 0),
                },
            }

        time_info: Dict[str, Any] = {}
        created = _as_int(get("time_created", "created"))
        updated = _as_int(get("time_updated", "updated"))
        if created is not None:
            time_info["created"] = created
        if updated is not None:
            time_info["updated"] = updated
        for key, column in (("busy", "time_busy"), ("idle", "time_idle")):
            value = _as_int(get(column))
            if value is not None:
                time_info[key] = value
        if time_info:
            info["time"] = time_info

        outcome = get("outcome", "idle_outcome")
        if outcome is not None:
            info["outcome"] = outcome

        parent_id = get("parent_id", "parentID")
        if parent_id:
            info["parentID"] = parent_id

        metadata = _loads(get("metadata"))
        if metadata is not None:
            info["metadata"] = metadata

        permissions = _loads(get("permissions", "permission"))
        if permissions is not None:
            info["permissions"] = permissions

        revert = _loads(get("revert"))
        if revert is not None:
            info["revert"] = revert

        return info

    def _build_messages(self, session_id: str) -> List[Dict[str, Any]]:
        assert self._con is not None
        self._require_message_schema()
        order = (
            _q(self.msg_seq_col)
            if self.msg_seq_col
            else _q(self.msg_time_col)
            if self.msg_time_col
            else "rowid"
        )
        try:
            rows = self._con.execute(
                f"select * from {_q(self.message_table)} "
                f"where {_q(self.msg_session_col)} = ? order by {order}",
                (session_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ExportFailed(f"cannot read messages for {session_id}: {exc}") from exc

        messages = []
        for row in rows:
            data = _loads(row[self.msg_data_col], {})
            if not isinstance(data, dict):
                data = {}
            message = dict(data)
            message["id"] = row["id"]
            message["type"] = row[self.msg_type_col] if self.msg_type_col else data.get("type")
            if "time" not in message and self.msg_time_col:
                created = _as_int(row[self.msg_time_col])
                if created is not None:
                    message["time"] = {"created": created}

            parts = self._build_parts(str(row["id"]))
            if parts:
                message["content"] = parts
            elif message.get("type") == "assistant" and "content" not in message:
                message["content"] = []

            messages.append(message)
        return messages

    def _build_parts(self, message_id: str) -> List[Dict[str, Any]]:
        assert self._con is not None
        if not self.part_table or not self.part_message_col or not self.part_data_col:
            return []
        order = (
            _q(self.part_ordinal_col)
            if self.part_ordinal_col
            else "rowid"
        )
        try:
            rows = self._con.execute(
                f"select * from {_q(self.part_table)} "
                f"where {_q(self.part_message_col)} = ? order by {order}",
                (message_id,),
            ).fetchall()
        except sqlite3.Error:
            return []

        parts = []
        for row in rows:
            data = _loads(row[self.part_data_col], {})
            if not isinstance(data, dict):
                data = {}
            if "type" not in data and self.part_type_col:
                data["type"] = row[self.part_type_col]
            parts.append(data)
        return parts

    # ------------------------------------------------------------------ doctor
    def schema_report(self) -> Dict[str, Any]:
        assert self._con is not None
        tables = self._table_names()
        counts: Dict[str, Any] = {}
        for label, table in (
            ("sessions", self.session_table),
            ("messages", self.message_table),
            ("parts", self.part_table),
        ):
            if not table:
                counts[label] = None
                continue
            try:
                counts[label] = self._con.execute(
                    f"select count(*) from {_q(table)}"
                ).fetchone()[0]
            except sqlite3.Error:
                counts[label] = None

        child_count = None
        if self.parent_col:
            try:
                child_count = self._con.execute(
                    f"select count(*) from {_q(self.session_table)} "
                    f"where {_q(self.parent_col)} is not null"
                ).fetchone()[0]
            except sqlite3.Error:
                child_count = None

        missing: Dict[str, List[str]] = {}
        for table in (self.session_table, self.message_table, self.part_table):
            if not table:
                continue
            columns = self._columns(table)
            absent = _missing_critical(table, columns)
            if absent:
                missing[table] = absent

        return {
            "db_path": str(self.db_path),
            "snapshot_path": str(self._snapshot_path) if self._snapshot_path else None,
            "tables": sorted(tables),
            "session_table": self.session_table,
            "message_table": self.message_table,
            "part_table": self.part_table,
            "columns": {
                "session": self.session_columns,
                "message": self.message_columns,
                "part": self.part_columns,
            },
            "counts": counts,
            "child_sessions": child_count,
            "missing_required": missing,
        }

    def close(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            finally:
                self._con = None
        if self._tmp_dir is not None:
            try:
                self._tmp_dir.cleanup()
            finally:
                self._tmp_dir = None

    def __enter__(self) -> "SqliteSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
