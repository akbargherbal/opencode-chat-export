"""Command line interface."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import sys
import traceback
from pathlib import Path
from typing import List, Optional

from . import TOOL_NAME, __version__
from .archive import ArchiveOptions, build_archive
from .doctor import run_doctor
from .errors import ChatExportError
from .paths import discover_environment
from .provider import Provider
from .sources.base import SessionRef
from .util import human_bytes, ms_to_iso, parse_time_ms
from .validate import validate_path

COMMANDS = ("export", "list", "doctor", "validate", "version")


def _default_out() -> str:
    return f"opencode-chats-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", help="OpenCode data directory (default: XDG data dir)")
    parser.add_argument("--db", help="path to opencode.db (default: discovered automatically)")
    parser.add_argument("--opencode", help="path to the opencode binary")
    parser.add_argument(
        "--backend",
        choices=("auto", "cli", "api", "sqlite"),
        default="auto",
        help="source to use (default: auto: CLI fetch, DB enumeration/fallback)",
    )
    parser.add_argument("--timeout", type=int, default=300, help="per-command timeout in seconds")
    parser.add_argument("-v", "--verbose", action="store_true", help="show diagnostics and tracebacks")


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", action="append", default=[], help="session id (repeatable)")
    parser.add_argument(
        "--directory",
        action="append",
        default=[],
        help="only sessions in this project/directory (repeatable)",
    )
    parser.add_argument("--since", help="only sessions created on/after this date or ISO timestamp")
    parser.add_argument("--until", help="only sessions created on/before this date or ISO timestamp")
    parser.add_argument("--top-level-only", action="store_true", help="exclude child/subagent sessions")
    parser.add_argument("--limit", type=int, default=0, help="limit to N most recently updated sessions")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Export OpenCode chat sessions to a portable JSON zip for offline reading.",
    )
    subparsers = parser.add_subparsers(dest="command")

    export = subparsers.add_parser("export", help="create an archive zip (default command)")
    _add_common(export)
    _add_filters(export)
    export.add_argument("sessions", nargs="*", help="session id(s) (same as --session)")
    export.add_argument("-o", "--out", help="output .zip (default: opencode-chats-<timestamp>.zip)")
    export.add_argument("--sanitize", action="store_true", help="use OpenCode's redaction (CLI backend only)")
    export.add_argument("--no-transcripts", action="store_true", help="do not write Markdown transcripts")
    export.add_argument("--include-db", action="store_true", help="also store a consistent opencode.db snapshot")
    export.add_argument("--deterministic", action="store_true", help="reproducible timestamps (SOURCE_DATE_EPOCH)")
    export.add_argument("--allow-empty", action="store_true", help="create an archive even with zero sessions")
    export.add_argument("--fail-fast", action="store_true", help="stop at the first session that fails")
    export.add_argument("--force", action="store_true", help="overwrite an existing output file")
    export.add_argument("--host", default=None, help="host label recorded in the manifest")
    export.add_argument("--json", action="store_true", help="print a machine-readable summary")
    export.add_argument("-q", "--quiet", action="store_true", help="only print errors")

    listing = subparsers.add_parser("list", help="list sessions visible to the tool")
    _add_common(listing)
    _add_filters(listing)
    listing.add_argument("--json", action="store_true", help="print JSON")
    listing.add_argument("--no-header", action="store_true", help="omit the table header")

    doctor = subparsers.add_parser("doctor", help="diagnose OpenCode schema/CLI compatibility")
    _add_common(doctor)
    doctor.add_argument("--sample", type=int, default=1, help="number of sessions to export and validate (default: 1)")
    doctor.add_argument("--probe-api", action="store_true", help="also probe the HTTP API source")
    doctor.add_argument("--strict", action="store_true", help="treat warnings as errors")
    doctor.add_argument("--json", action="store_true", help="print JSON")
    doctor.add_argument("--dump-schema", help="write the full doctor report (including schema) to this file")

    validate = subparsers.add_parser("validate", help="validate an exported JSON file or archive zip")
    validate.add_argument("path", help="session JSON or archive zip")
    validate.add_argument("--strict", action="store_true", help="treat warnings as errors")
    validate.add_argument("--json", action="store_true", help="print JSON")

    version = subparsers.add_parser("version", help="print the tool version")
    version.add_argument("--json", action="store_true", help="print JSON")

    return parser


def _environment_and_provider(args):
    env = discover_environment(
        data_dir=args.data_dir,
        db_path=args.db,
        opencode_bin=args.opencode,
    )
    if not env.host or env.host == "unknown":
        env.host = socket.gethostname() or "unknown"
    provider = Provider(
        env,
        backend=args.backend,
        sanitize=getattr(args, "sanitize", False),
        timeout=args.timeout,
    )
    return env, provider


def _selected_refs(provider: Provider, args) -> List[SessionRef]:
    requested = list(getattr(args, "session", []) or []) + list(getattr(args, "sessions", []) or [])
    # Preserve order while de-duplicating.
    requested = list(dict.fromkeys(requested))
    all_refs = provider.list_sessions()
    by_id = {ref.id: ref for ref in all_refs}

    if not requested:
        return all_refs

    selected = []
    for session_id in requested:
        ref = by_id.get(session_id)
        if ref is None:
            # The ID may be a child hidden from `session list` or outside the
            # current project; fetching it gives us its metadata.
            ref = provider.fetch(session_id).ref
        selected.append(ref)
    return selected


def _is_under(path: str, target: Path) -> bool:
    if not path:
        return False
    try:
        candidate = Path(path).expanduser().resolve()
    except OSError:
        return False
    return candidate == target or target in candidate.parents


def _filter_refs(provider: Provider, refs: List[SessionRef], args, *, newest_first: bool = False) -> List[SessionRef]:
    directories = [Path(item).expanduser().resolve() for item in (args.directory or [])]
    try:
        since = parse_time_ms(args.since) if args.since else None
        until = parse_time_ms(args.until) if args.until else None
    except ValueError as exc:
        raise ChatExportError(str(exc)) from exc

    selected = []
    for ref in refs:
        # Some list APIs omit the directory; fetch the export to fill it in only
        # when a directory filter actually needs the value.
        if directories and not ref.directory:
            try:
                filled = provider.fetch(ref.id).ref
                ref.directory = ref.directory or filled.directory
                ref.worktree = ref.worktree or filled.worktree
            except ChatExportError:
                pass
        if directories and not any(
            _is_under(ref.directory, target) or _is_under(ref.worktree, target)
            for target in directories
        ):
            continue
        if args.top_level_only and ref.parent_id:
            continue
        stamp = ref.created_ms if ref.created_ms is not None else ref.updated_ms
        if since is not None and (stamp is None or stamp < since):
            continue
        if until is not None and (stamp is None or stamp > until):
            continue
        selected.append(ref)

    selected.sort(key=lambda ref: (ref.updated_ms or ref.created_ms or 0, ref.id), reverse=True)
    if args.limit and args.limit > 0:
        selected = selected[: args.limit]
    if not newest_first:
        selected.sort(key=lambda ref: (ref.created_ms or 0, ref.id))
    return selected


def cmd_export(args) -> int:
    env, provider = _environment_and_provider(args)
    try:
        refs = _selected_refs(provider, args)
        refs = _filter_refs(provider, refs, args)

        out = Path(args.out).expanduser() if args.out else Path(_default_out())
        if out.is_dir():
            out = out / _default_out()
        elif out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        if out.exists() and not args.force:
            raise ChatExportError(f"output already exists: {out} (use --force to overwrite)")

        options = ArchiveOptions(
            out=out,
            transcripts=not args.no_transcripts,
            include_db=args.include_db,
            deterministic=args.deterministic,
            host=args.host or env.host,
            source_label=env.data_dir.as_posix(),
            allow_empty=args.allow_empty,
            fail_fast=args.fail_fast,
            opencode={
                "version": env.opencode_version,
                "binary": env.opencode_bin,
                "data_dir": str(env.data_dir),
                "db_path": str(env.db_path) if env.db_path else None,
            },
        )
        result = build_archive(provider, refs, options)

        summary = {
            "archive": str(result.path),
            "bytes": result.path.stat().st_size if result.path.exists() else None,
            "sessions": result.session_count,
            "messages": result.manifest["totals"]["messages"],
            "backends": result.manifest["backends"],
            "warnings": len(result.warnings),
            "errors": len(result.errors),
            "opencode_version": env.opencode_version,
        }
        if args.json:
            print(json.dumps(summary, indent=2))
        elif not args.quiet:
            print(
                f"Exported {summary['sessions']} session(s), {summary['messages']} message(s) "
                f"-> {summary['archive']} ({human_bytes(summary['bytes'])})"
            )
            backends = ", ".join(f"{name}={count}" for name, count in summary["backends"].items())
            if backends:
                print(f"Backends: {backends}")
            if summary["warnings"]:
                print(f"Warnings: {summary['warnings']} (details in manifest.json)")
            for error in result.errors:
                print(f"error: {error}", file=sys.stderr)
        return 1 if result.errors else 0
    finally:
        provider.close()


def cmd_list(args) -> int:
    env, provider = _environment_and_provider(args)
    try:
        refs = _selected_refs(provider, args)
        refs = _filter_refs(provider, refs, args, newest_first=True)
        if args.json:
            payload = [
                {
                    **ref.to_dict(),
                    "created": ms_to_iso(ref.created_ms),
                    "updated": ms_to_iso(ref.updated_ms),
                }
                for ref in refs
            ]
            print(json.dumps(payload, indent=2))
            return 0

        if not refs:
            print("no sessions found")
            return 0
        if not args.no_header:
            print(f"{'ID':38} {'UPDATED':20} {'DIRECTORY':28} {'TITLE'}")
        for ref in refs:
            parent = "child " if ref.parent_id else ""
            updated = ms_to_iso(ref.updated_ms) or ""
            directory = ref.directory or ""
            if len(directory) > 28:
                directory = "..." + directory[-25:]
            title = ref.title or "(untitled)"
            if len(title) > 70:
                title = title[:67] + "..."
            print(f"{ref.id:38} {updated:20} {directory:28} {parent}{title}")
        return 0
    finally:
        provider.close()


def cmd_doctor(args) -> int:
    env = discover_environment(
        data_dir=args.data_dir,
        db_path=args.db,
        opencode_bin=args.opencode,
    )
    report = run_doctor(
        env,
        sample=args.sample,
        probe_api=args.probe_api,
        strict=args.strict,
    )
    if args.dump_schema:
        out = Path(args.dump_schema).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        sys.stdout.write(report.render())
    return 0 if report.ok else 1


def cmd_validate(args) -> int:
    report = validate_path(Path(args.path).expanduser(), strict=args.strict)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        sys.stdout.write(report.render())
    return 0 if report.ok else 1


def cmd_version(args) -> int:
    if args.json:
        print(json.dumps({"name": TOOL_NAME, "version": __version__}, indent=2))
    else:
        print(f"{TOOL_NAME} {__version__}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if argv and argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    if argv and argv[0] in ("-V", "--version"):
        print(f"{TOOL_NAME} {__version__}")
        return 0
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        # Allow `opencode-chat-export <session-id>` as an export shorthand.
        argv = ["export", *argv]
    elif not argv:
        argv = ["export"]

    args = parser.parse_args(argv)

    try:
        if args.command == "export":
            return cmd_export(args)
        if args.command == "list":
            return cmd_list(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "validate":
            return cmd_validate(args)
        if args.command == "version":
            return cmd_version(args)
        parser.print_help()
        return 2
    except ChatExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # unexpected: keep the CLI honest
        if getattr(args, "verbose", False):
            traceback.print_exc()
        else:
            print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
            print("rerun with --verbose for a traceback", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
