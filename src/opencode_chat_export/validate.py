"""Validate an exported JSON file or a complete archive zip."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from .contract import Issue, has_errors, validate_export
from .util import sha256_bytes


@dataclass
class ValidationReport:
    path: str
    kind: str
    issues: List[Issue] = field(default_factory=list)
    checks: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not has_errors(self.issues)

    def add(self, level: str, code: str, path: str, message: str) -> None:
        self.issues.append(Issue(level=level, code=code, path=path, message=message))

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "kind": self.kind,
            "ok": self.ok,
            "checks": self.checks,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def render(self) -> str:
        lines = [f"{self.path} ({self.kind}): {'OK' if self.ok else 'FAILED'}"]
        for check in self.checks:
            lines.append(f"  - {check}")
        for issue in self.issues:
            lines.append(f"  {issue}")
        return "\n".join(lines) + "\n"


def _load_json_bytes(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


def _is_zip(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def validate_path(path: Path, *, strict: bool = False) -> ValidationReport:
    path = Path(path)
    if not path.exists():
        report = ValidationReport(path=str(path), kind="missing")
        report.add("error", "file_not_found", "$", f"file does not exist: {path}")
        return report
    if path.suffix.lower() == ".zip" or _is_zip(path):
        return validate_zip(path, strict=strict)
    return validate_json_file(path, strict=strict)


def validate_json_file(path: Path, *, strict: bool = False) -> ValidationReport:
    report = ValidationReport(path=str(path), kind="json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.add("error", "invalid_json", "$", f"cannot parse JSON: {exc}")
        return report

    if isinstance(data, dict) and "info" in data and "messages" in data:
        report.checks.append("session export shape")
        for issue in validate_export(data, strict=strict):
            report.issues.append(issue)
        return report

    if isinstance(data, dict) and "sessions" in data and "schema_version" in data:
        report.checks.append("catalog/manifest shape")
        if not isinstance(data.get("sessions"), list):
            report.add("error", "invalid_sessions", "sessions", "sessions is not an array")
        if "tool" in data and not isinstance(data.get("tool"), dict):
            report.add("warning", "invalid_tool", "tool", "tool is not an object")
        return report

    report.add("error", "unknown_json_shape", "$", "not an OpenCode export or archive catalog")
    return report


def validate_zip(path: Path, *, strict: bool = False) -> ValidationReport:
    report = ValidationReport(path=str(path), kind="zip")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        report.add("error", "invalid_zip", "$", f"cannot open zip: {exc}")
        return report

    with archive:
        names = archive.namelist()
        if not names:
            report.add("error", "empty_zip", "$", "archive has no entries")
            return report

        manifest_names = sorted(
            (name for name in names if name.endswith("/manifest.json") or name == "manifest.json"),
            key=len,
        )
        if not manifest_names:
            report.add("error", "missing_manifest", "$", "archive has no manifest.json")
            return report
        manifest_name = manifest_names[0]
        root = manifest_name[: -len("manifest.json")]

        try:
            manifest = _load_json_bytes(archive.read(manifest_name))
        except (KeyError, ValueError) as exc:
            report.add("error", "invalid_manifest", manifest_name, f"cannot parse manifest: {exc}")
            return report
        if not isinstance(manifest, dict):
            report.add("error", "invalid_manifest", manifest_name, "manifest is not an object")
            return report

        if manifest.get("schema_version") != 1:
            report.add(
                "error",
                "unsupported_manifest_version",
                f"{manifest_name}.schema_version",
                f"unsupported schema_version {manifest.get('schema_version')!r}",
            )
        report.checks.append(f"manifest: {manifest_name}")

        sessions = manifest.get("sessions")
        if not isinstance(sessions, list):
            report.add("error", "invalid_manifest_sessions", f"{manifest_name}.sessions", "sessions is not an array")
            sessions = []

        seen_ids = set()
        for index, entry in enumerate(sessions):
            path_key = f"{manifest_name}.sessions[{index}]"
            if not isinstance(entry, dict):
                report.add("error", "invalid_session_entry", path_key, "entry is not an object")
                continue
            session_id = entry.get("id")
            if not isinstance(session_id, str) or not session_id:
                report.add("error", "missing_session_id", f"{path_key}.id", "session id is missing")
            elif session_id in seen_ids:
                report.add("error", "duplicate_session_id", f"{path_key}.id", f"duplicate {session_id}")
            else:
                seen_ids.add(session_id)

            rel = entry.get("file")
            if not isinstance(rel, str):
                report.add("error", "missing_session_file", f"{path_key}.file", "session file is missing")
                continue
            zip_name = root + rel
            try:
                raw = archive.read(zip_name)
            except KeyError:
                report.add("error", "missing_session_file", zip_name, "file is not in the archive")
                continue

            expected_hash = entry.get("sha256")
            if expected_hash and sha256_bytes(raw) != expected_hash:
                report.add("error", "checksum_mismatch", zip_name, "sha256 does not match manifest")
            expected_bytes = entry.get("bytes")
            if isinstance(expected_bytes, int) and len(raw) != expected_bytes:
                report.add("error", "size_mismatch", zip_name, "byte size does not match manifest")

            try:
                doc = _load_json_bytes(raw)
            except ValueError as exc:
                report.add("error", "invalid_session_json", zip_name, f"cannot parse: {exc}")
                continue
            for issue in validate_export(doc, strict=strict):
                report.issues.append(
                    Issue(
                        level=issue.level,
                        code=issue.code,
                        path=f"{zip_name}:{issue.path}",
                        message=issue.message,
                    )
                )
            info = doc.get("info") if isinstance(doc, dict) else {}
            doc_id = info.get("id") if isinstance(info, dict) else None
            if session_id and doc_id != session_id:
                report.add(
                    "error",
                    "session_id_mismatch",
                    zip_name,
                    f"manifest id {session_id!r} != document id {doc_id!r}",
                )

        report.checks.append(f"sessions: {len(sessions)}")

        files = manifest.get("files")
        if isinstance(files, list):
            for index, entry in enumerate(files):
                if not isinstance(entry, dict):
                    report.add("error", "invalid_file_entry", f"{manifest_name}.files[{index}]", "entry is not an object")
                    continue
                rel = entry.get("path")
                if not isinstance(rel, str):
                    report.add("error", "missing_file_path", f"{manifest_name}.files[{index}]", "path is missing")
                    continue
                zip_name = root + rel
                try:
                    raw = archive.read(zip_name)
                except KeyError:
                    report.add("error", "missing_file", zip_name, "file listed in manifest is missing")
                    continue
                expected_hash = entry.get("sha256")
                if expected_hash and sha256_bytes(raw) != expected_hash:
                    report.add("error", "checksum_mismatch", zip_name, "sha256 does not match manifest")
        else:
            report.add("warning", "missing_file_list", f"{manifest_name}.files", "manifest has no file checksum list")

        index_name = root + "index.json"
        try:
            index = _load_json_bytes(archive.read(index_name))
        except KeyError:
            report.add("error", "missing_index", index_name, "index.json is missing")
        except ValueError as exc:
            report.add("error", "invalid_index", index_name, f"cannot parse: {exc}")
        else:
            index_sessions = index.get("sessions") if isinstance(index, dict) else None
            if not isinstance(index_sessions, list):
                report.add("error", "invalid_index", index_name, "index.sessions is not an array")
            else:
                index_ids = {
                    item.get("id")
                    for item in index_sessions
                    if isinstance(item, dict) and item.get("id")
                }
                if index_ids != seen_ids:
                    report.add(
                        "error",
                        "index_mismatch",
                        index_name,
                        f"index ids and manifest ids differ ({len(index_ids)} vs {len(seen_ids)})",
                    )
            report.checks.append("index: present")

        readme_name = root + "README.md"
        if readme_name not in names:
            report.add("warning", "missing_readme", readme_name, "README.md is missing")
        else:
            report.checks.append("README: present")

    if strict:
        for issue in report.issues:
            issue.level = "error"
    return report
