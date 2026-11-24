"""Build the portable zip archive.

Layout (root folder = output filename stem)::

    <root>/
      README.md
      manifest.json
      index.json
      index.md
      schema/*.json
      sessions/<date>-<slug>-<session-id>.json
      sessions/<date>-<slug>-<session-id>.md
      db/opencode.db                 # only with --include-db
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import TOOL_NAME, __version__
from .errors import ChatExportError
from .provider import Provider
from .render import render_transcript
from .schemas import all_schemas
from .sources.base import SessionRef
from .util import (
    human_bytes,
    json_dumps,
    ms_to_iso,
    now_iso,
    sha256_bytes,
    sha256_file,
    slugify,
)
INDEX_SCHEMA_VERSION = 1


@dataclass
class ArchiveOptions:
    out: Path
    transcripts: bool = True
    include_db: bool = False
    deterministic: bool = False
    host: str = ""
    source_label: str = ""
    allow_empty: bool = False
    fail_fast: bool = False
    opencode: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArchiveResult:
    path: Path
    manifest: Dict[str, Any]
    sessions: List[Dict[str, Any]]
    errors: List[str]
    warnings: List[str]

    @property
    def session_count(self) -> int:
        return len(self.sessions)


def _generated_at(deterministic: bool) -> str:
    if not deterministic:
        return now_iso()
    try:
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    except ValueError:
        epoch = 0
    return (
        dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _date_from_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "00000000"
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000.0, tz=dt.timezone.utc).strftime("%Y%m%d")
    except (OverflowError, OSError, ValueError):
        return "00000000"


def _safe_id(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id or "session").strip("-")
    return cleaned[:120] or "session"


def _merge_ref(primary: SessionRef, fallback: SessionRef) -> SessionRef:
    return SessionRef(
        id=primary.id or fallback.id,
        title=primary.title or fallback.title,
        directory=primary.directory or fallback.directory,
        project_id=primary.project_id or fallback.project_id,
        parent_id=primary.parent_id or fallback.parent_id,
        created_ms=primary.created_ms if primary.created_ms is not None else fallback.created_ms,
        updated_ms=primary.updated_ms if primary.updated_ms is not None else fallback.updated_ms,
        worktree=primary.worktree or fallback.worktree,
    )


def _session_filename(ref: SessionRef, used: set) -> str:
    stem = f"{_date_from_ms(ref.created_ms)}-{slugify(ref.title)}-{_safe_id(ref.id)}"
    if len(stem) > 220:
        stem = stem[:220].rstrip("-")
    candidate = f"{stem}.json"
    counter = 2
    while candidate in used:
        candidate = f"{stem}-{counter}.json"
        counter += 1
    used.add(candidate)
    return candidate


def _write_zip(root_dir: Path, out: Path, deterministic: bool) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        with zipfile.ZipFile(
            tmp,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for path in sorted(root_dir.rglob("*")):
                if not path.is_file():
                    continue
                arcname = Path(root_dir.name) / path.relative_to(root_dir)
                if deterministic:
                    info = zipfile.ZipInfo(str(arcname), date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    archive.writestr(info, path.read_bytes())
                else:
                    archive.write(path, str(arcname))
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _index_markdown(entries: List[Dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# OpenCode chat archive",
        "",
        f"Generated: {generated_at}",
        "",
        f"{len(entries)} session(s).",
        "",
        "| Date | Title | Directory | Msgs | JSON | Transcript |",
        "|---|---|---|---:|---|---|",
    ]
    for entry in entries:
        title = (entry.get("title") or "(untitled)").replace("|", "\\|")
        directory = (entry.get("directory") or "").replace("|", "\\|")
        created = entry.get("created") or ""
        date = created[:10] if created else ""
        parent = " ↳" if entry.get("parent_id") else ""
        json_link = f"[json]({entry['file']})"
        transcript = entry.get("transcript")
        md_link = f"[md]({transcript})" if transcript else ""
        lines.append(
            f"| {date} | {parent}{title} | `{directory}` | {entry.get('messages', 0)} "
            f"| {json_link} | {md_link} |"
        )
    return "\n".join(lines) + "\n"


def _readme(
    *,
    generated_at: str,
    opencode_version: Optional[str],
    host: str,
    root_name: str,
    totals: Dict[str, Any],
) -> str:
    return f"""# OpenCode chat archive

Generated by **{TOOL_NAME} {__version__}** on {generated_at}.
Host: `{host or 'unknown'}`.
OpenCode version: `{opencode_version or 'unknown'}`.

This archive contains {totals['sessions']} session(s), {totals['messages']} message(s),
and {human_bytes(totals['session_bytes'])} of JSON.

## Layout

```
{root_name}/
  README.md                 this file
  manifest.json             provenance, checksums, validation issues
  index.json                machine-readable catalog
  index.md                  human-readable catalog
  schema/                   JSON Schemas for the formats below
  sessions/*.json           one OpenCode session export per file
  sessions/*.md             matching human-readable transcript
  db/opencode.db            optional consistent DB snapshot (--include-db)
```

`index.json` is the easiest entry point:

```python
import json, zipfile

with zipfile.ZipFile("archive.zip") as z:
    root = z.namelist()[0].split("/")[0]
    index = json.load(z.open(f"{{root}}/index.json"))
    for session in index["sessions"]:
        doc = json.load(z.open(f"{{root}}/" + session["file"]))
        print(session["created"], session["title"], len(doc["messages"]))
```

## Formats

* `sessions/*.json` is the same shape as `opencode session export <id>`:
  `{{"info": ..., "messages": [...]}}`. It is lossless and can be re-imported.
* `sessions/*.md` is a reading copy rendered from the JSON. Embedded file bytes
  (`files[].data`, often base64) are intentionally omitted from Markdown.
* `manifest.json` records the OpenCode version, backend used per session, byte
  sizes, SHA-256 checksums, and any contract warnings.
* `schema/` contains JSON Schemas for the session export, manifest, and index.

## Re-import a session

```bash
opencode session import <archive>/sessions/<file>.json --directory <project-dir>
opencode -s <session-id>
```

Import parent sessions before child/subagent sessions. The session ID is preserved.

## Notes

* Child/subagent sessions are included. Parent links are visible in `index.json`
  (`parent_id`) and in each transcript header.
* `--sanitize` uses OpenCode's own redaction and is only available through the
  CLI backend. A raw database/API export is never silently substituted.
* The archive is **not encrypted**. It can contain prompts, source code, tool
  output, and other sensitive material. Store and transfer it accordingly.
* `manifest.json` deliberately does not checksum itself; every other file is
  listed with its SHA-256.

## Validation

```bash
opencode-chat-export validate archive.zip
```

The validator checks the JSON shape, duplicate session IDs, manifest checksums,
and index consistency.
"""


def build_archive(
    provider: Provider,
    refs: List[SessionRef],
    options: ArchiveOptions,
) -> ArchiveResult:
    generated_at = _generated_at(options.deterministic)
    out = options.out
    root_name = out.stem
    errors: List[str] = []
    warnings: List[str] = []
    entries: List[Dict[str, Any]] = []
    used_names: set = set()

    with tempfile.TemporaryDirectory(prefix="opencode-chat-export-") as tmp:
        root_dir = Path(tmp) / root_name
        sessions_dir = root_dir / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)

        for ref in refs:
            try:
                result = provider.fetch(ref.id)
            except Exception as exc:
                message = f"{ref.id}: {exc}"
                errors.append(message)
                if options.fail_fast:
                    raise ChatExportError(message) from exc
                continue

            merged = _merge_ref(result.ref, ref)
            filename = _session_filename(merged, used_names)
            json_text = json_dumps(result.doc)
            json_bytes = json_text.encode("utf-8")
            json_path = sessions_dir / filename
            json_path.write_bytes(json_bytes)

            transcript_rel = None
            if options.transcripts:
                transcript_name = Path(filename).with_suffix(".md").name
                transcript_rel = f"sessions/{transcript_name}"
                transcript_text = render_transcript(
                    result.doc,
                    opencode_version=options.opencode.get("version"),
                    backend=result.backend,
                    exported_at=generated_at,
                    host=options.host,
                )
                (sessions_dir / transcript_name).write_text(
                    transcript_text, encoding="utf-8", newline="\n"
                )

            issues = [issue.to_dict() for issue in result.issues]
            for issue in result.issues:
                if issue.level == "warning":
                    warnings.append(f"{merged.id}: {issue}")

            entries.append(
                {
                    "id": merged.id,
                    "title": merged.title or "(untitled)",
                    "created": ms_to_iso(merged.created_ms),
                    "updated": ms_to_iso(merged.updated_ms),
                    "directory": merged.directory,
                    "project_id": merged.project_id,
                    "parent_id": merged.parent_id,
                    "messages": len(result.doc.get("messages") or []),
                    "file": f"sessions/{filename}",
                    "transcript": transcript_rel,
                    "bytes": len(json_bytes),
                    "sha256": sha256_bytes(json_bytes),
                    "backend": result.backend,
                    "issues": issues,
                }
            )

        if not entries and not options.allow_empty:
            raise ChatExportError(
                "no sessions were exported; pass --allow-empty to create an empty archive"
            )

        entries.sort(key=lambda item: (item.get("created") or "", item.get("id") or ""))
        index = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "generated_at": generated_at,
            "opencode_version": options.opencode.get("version"),
            "host": options.host,
            "sessions": entries,
        }
        (root_dir / "index.json").write_text(json_dumps(index), encoding="utf-8", newline="\n")
        (root_dir / "index.md").write_text(
            _index_markdown(entries, generated_at), encoding="utf-8", newline="\n"
        )

        schema_dir = root_dir / "schema"
        schema_dir.mkdir(parents=True, exist_ok=True)
        for name, schema in all_schemas().items():
            (schema_dir / name).write_text(json_dumps(schema), encoding="utf-8", newline="\n")

        totals = {
            "sessions": len(entries),
            "messages": sum(entry["messages"] for entry in entries),
            "session_bytes": sum(entry["bytes"] for entry in entries),
            "warnings": len(warnings),
            "errors": len(errors),
        }

        if options.include_db:
            snapshot = provider.db_snapshot_path
            if snapshot and Path(snapshot).exists():
                db_dir = root_dir / "db"
                db_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot, db_dir / "opencode.db")
            else:
                warnings.append(
                    "include-db requested but no consistent database snapshot was available"
                )

        (root_dir / "README.md").write_text(
            _readme(
                generated_at=generated_at,
                opencode_version=options.opencode.get("version"),
                host=options.host,
                root_name=root_name,
                totals=totals,
            ),
            encoding="utf-8",
            newline="\n",
        )

        files = []
        for path in sorted(root_dir.rglob("*")):
            if not path.is_file() or path.name == "manifest.json":
                continue
            files.append(
                {
                    "path": str(path.relative_to(root_dir)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )

        backends: Dict[str, int] = {}
        for entry in entries:
            backends[entry["backend"]] = backends.get(entry["backend"], 0) + 1

        manifest = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "tool": {"name": TOOL_NAME, "version": __version__},
            "generated_at": generated_at,
            "opencode": options.opencode,
            "host": options.host,
            "source_label": options.source_label,
            "options": {
                "transcripts": options.transcripts,
                "include_db": options.include_db,
                "deterministic": options.deterministic,
            },
            "totals": totals,
            "backends": backends,
            "sessions": entries,
            "files": files,
            "issues": [
                {"session": entry["id"], **issue}
                for entry in entries
                for issue in entry.get("issues", [])
            ],
            "errors": errors,
            "diagnostics": provider.diagnostics,
        }
        (root_dir / "manifest.json").write_text(
            json_dumps(manifest), encoding="utf-8", newline="\n"
        )

        _write_zip(root_dir, out, options.deterministic)

    return ArchiveResult(
        path=out,
        manifest=manifest,
        sessions=entries,
        errors=errors,
        warnings=warnings,
    )
