"""`doctor`: tell the user exactly what this OpenCode build looks like.

This is the drift alarm.  It checks the binary, the CLI command spelling, the
SQLite tables/columns, and a sample export.  It exits non-zero only for problems
that would prevent a usable archive; unknown versions/types are warnings so a new
OpenCode can still be used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import TOOL_NAME, __version__
from .contract import issue_counts, validate_export
from .errors import ChatExportError
from .paths import Environment
from .provider import Provider
from .sources.cli import CliSource
from .sources.sqlite import SqliteSource
from .util import human_bytes

KNOWN_SCHEMAS = {
    "2.4": {
        "session_table": "session_v2",
        "message_table": "session_message",
        "part_table": "session_part",
        "required": {
            "session_v2": ("id", "project_id", "directory", "title", "time_created", "time_updated"),
            "session_message": ("id", "session_id", "type", "data"),
            "session_part": ("message_id", "ordinal", "type", "data"),
        },
    }
}


@dataclass
class Check:
    name: str
    status: str  # ok | warn | error
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class DoctorReport:
    checks: List[Check] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)
    schema: Optional[Dict[str, Any]] = None
    sample_issues: List[dict] = field(default_factory=list)

    @property
    def errors(self) -> List[Check]:
        return [check for check in self.checks if check.status == "error"]

    @property
    def warnings(self) -> List[Check]:
        return [check for check in self.checks if check.status == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "tool": {"name": TOOL_NAME, "version": __version__},
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "environment": self.environment,
            "schema": self.schema,
            "sample_issues": self.sample_issues,
        }

    def render(self) -> str:
        lines = [
            f"{TOOL_NAME} doctor {__version__}",
            f"python: {self.environment.get('python')}",
            "",
        ]
        for check in self.checks:
            marker = {"ok": "ok", "warn": "WARN", "error": "ERROR"}.get(check.status, check.status)
            lines.append(f"[{marker:5}] {check.name}: {check.detail}")
        lines.append("")
        lines.append("result: OK" if self.ok else "result: FAILED")
        return "\n".join(lines) + "\n"


def _check(checks: List[Check], name: str, status: str, detail: str) -> None:
    checks.append(Check(name=name, status=status, detail=detail))


def _version_key(version: Optional[str]) -> Optional[str]:
    if not version:
        return None
    parts = version.split(".")
    if len(parts) < 2:
        return None
    return ".".join(parts[:2])


def run_doctor(
    env: Environment,
    *,
    sample: int = 1,
    probe_api: bool = False,
    strict: bool = False,
) -> DoctorReport:
    import sys

    report = DoctorReport(
        environment={
            "python": sys.version.split()[0],
            "tool_version": __version__,
            **env.to_dict(),
        }
    )
    checks = report.checks

    if env.data_dir.exists():
        _check(checks, "data_dir", "ok", str(env.data_dir))
    else:
        _check(checks, "data_dir", "warn", f"missing: {env.data_dir}")

    if env.db_path and env.db_path.exists():
        _check(checks, "database", "ok", f"{env.db_path} ({human_bytes(env.db_path.stat().st_size)})")
    else:
        _check(checks, "database", "warn", f"not found: {env.db_path}")

    if env.opencode_bin:
        _check(
            checks,
            "opencode",
            "ok" if env.opencode_version else "warn",
            f"{env.opencode_version or 'unknown version'} ({env.opencode_bin})",
        )
    else:
        _check(checks, "opencode", "warn", "binary not found; CLI/API backends unavailable")

    if env.opencode_version:
        key = _version_key(env.opencode_version)
        if key in KNOWN_SCHEMAS:
            _check(checks, "version_contract", "ok", f"known schema for {key}.x")
        else:
            _check(
                checks,
                "version_contract",
                "warn",
                f"untested OpenCode version {env.opencode_version}; run validate/doctor after exporting",
            )

    sqlite_source: Optional[SqliteSource] = None
    sqlite_refs = []
    if env.db_path and env.db_path.exists():
        try:
            sqlite_source = SqliteSource(env.db_path)
            schema = sqlite_source.schema_report()
            report.schema = schema
            missing = schema.get("missing_required") or {}
            missing_tables = [
                name
                for name, key in (("message", "message_table"), ("part", "part_table"))
                if not schema.get(key)
            ]
            if missing or missing_tables:
                details = []
                if missing_tables:
                    details.append("no " + ", ".join(f"{name} table" for name in missing_tables))
                if missing:
                    details.append(
                        "; ".join(f"{table}: {', '.join(cols)}" for table, cols in missing.items())
                    )
                _check(checks, "sqlite_schema", "error", "missing required schema — " + "; ".join(details))
            else:
                counts = schema.get("counts") or {}
                _check(
                    checks,
                    "sqlite_schema",
                    "ok",
                    f"{schema.get('session_table')} / {schema.get('message_table')} / "
                    f"{schema.get('part_table')}; sessions={counts.get('sessions')}, "
                    f"messages={counts.get('messages')}, parts={counts.get('parts')}, "
                    f"children={schema.get('child_sessions')}",
                )

            expected = KNOWN_SCHEMAS.get(_version_key(env.opencode_version) or "")
            if expected:
                if schema.get("session_table") != expected["session_table"]:
                    _check(
                        checks,
                        "sqlite_table_version",
                        "warn",
                        f"expected {expected['session_table']} for OpenCode "
                        f"{env.opencode_version}, found {schema.get('session_table')}",
                    )
                else:
                    _check(
                        checks,
                        "sqlite_table_version",
                        "ok",
                        f"session table is {schema.get('session_table')}",
                    )
            try:
                sqlite_refs = sqlite_source.list_sessions()
            except Exception as exc:
                _check(checks, "sqlite_list", "warn", f"cannot enumerate sessions: {exc}")
        except Exception as exc:
            _check(checks, "sqlite_schema", "error", f"cannot read database: {exc}")

    cli_source: Optional[CliSource] = None
    cli_refs = []
    if env.opencode_bin:
        try:
            cli_source = CliSource(env.opencode_bin)
            prefix = cli_source.export_prefix()
            _check(checks, "cli_export", "ok", "command: opencode " + " ".join(prefix))
        except Exception as exc:
            _check(checks, "cli_export", "warn", f"cannot find export command: {exc}")
        try:
            cli_source = cli_source or CliSource(env.opencode_bin)
            cli_refs = cli_source.list_sessions()
            _check(checks, "cli_list", "ok", f"{len(cli_refs)} session(s) listed")
        except Exception as exc:
            _check(checks, "cli_list", "warn", f"cannot list sessions: {exc}")

    if probe_api and env.opencode_bin:
        try:
            from .sources.api import ApiSource

            api_refs = ApiSource(env.opencode_bin).list_sessions()
            _check(checks, "api", "ok", f"{len(api_refs)} session(s) listed")
        except Exception as exc:
            _check(checks, "api", "warn", f"API probe failed: {exc}")

    if sample > 0 and (sqlite_refs or cli_refs):
        refs = sqlite_refs or cli_refs
        refs = sorted(refs, key=lambda ref: ref.updated_ms or ref.created_ms or 0, reverse=True)
        try:
            provider = Provider(env, backend="auto")
        except Exception as exc:
            _check(checks, "sample_export", "error", f"no usable backend: {exc}")
        else:
            try:
                total_errors = 0
                total_warnings = 0
                for ref in refs[:sample]:
                    try:
                        result = provider.fetch(ref.id)
                    except Exception as exc:
                        total_errors += 1
                        report.sample_issues.append(
                            {"session": ref.id, "level": "error", "message": str(exc)}
                        )
                        continue
                    counts = issue_counts(result.issues)
                    total_errors += counts["error"]
                    total_warnings += counts["warning"]
                    for issue in result.issues:
                        report.sample_issues.append(
                            {"session": ref.id, **issue.to_dict()}
                        )
                if total_errors:
                    _check(
                        checks,
                        "sample_export",
                        "error",
                        f"{total_errors} error(s), {total_warnings} warning(s) in "
                        f"{min(sample, len(refs))} sample export(s)",
                    )
                else:
                    _check(
                        checks,
                        "sample_export",
                        "ok",
                        f"{min(sample, len(refs))} sample export(s) valid "
                        f"({total_warnings} warning(s))",
                    )
            finally:
                provider.close()

    if sqlite_source is not None:
        sqlite_source.close()

    if strict:
        for check in checks:
            if check.status == "warn":
                check.status = "error"
    return report
