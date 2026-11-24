"""Choose between CLI, HTTP API, and direct SQLite backends.

Design rule: use OpenCode's own export for the bytes, and use the database for
*enumeration* and as an offline fallback.  Every fetched document is validated;
if a source returns a structurally broken document, the provider tries the next
source instead of writing a corrupt transcript into the archive.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .contract import has_errors, validate_export
from .errors import BackendUnavailable, ChatExportError, ExportFailed
from .paths import Environment
from .sources.api import ApiSource
from .sources.base import ExportResult, SessionRef, Source, ref_from_export
from .sources.cli import CliSource
from .sources.sqlite import SqliteSource

VALID_BACKENDS = ("auto", "cli", "api", "sqlite")


class Provider:
    def __init__(
        self,
        env: Environment,
        *,
        backend: str = "auto",
        sanitize: bool = False,
        timeout: int = 300,
    ) -> None:
        if backend not in VALID_BACKENDS:
            raise ChatExportError(
                f"unknown backend {backend!r}; choose one of {', '.join(VALID_BACKENDS)}"
            )
        if sanitize and backend in ("sqlite", "api"):
            raise ChatExportError(
                "--sanitize is only supported by the CLI backend "
                "(the SQLite/API paths cannot guarantee OpenCode's redaction)"
            )

        self.env = env
        self.backend = backend
        self.sanitize = sanitize
        self.timeout = timeout
        self.diagnostics: List[dict] = []
        self._cache: Dict[str, ExportResult] = {}
        self._sources: Dict[str, Source] = {}

        self._build_sources()

        # Enumerating from SQLite sees every project and child session.  Fetching
        # from the CLI uses OpenCode's canonical serializer.  Both orders degrade
        # gracefully when a source is unavailable.
        self.list_order = [name for name in ("sqlite", "api", "cli") if name in self._sources]
        self.fetch_order = [name for name in ("cli", "api", "sqlite") if name in self._sources]
        if sanitize:
            self.fetch_order = [name for name in self.fetch_order if name == "cli"]

        if not self.list_order:
            raise BackendUnavailable(
                "no usable source: install OpenCode (for CLI/API) or pass --db"
            )
        if not self.fetch_order:
            raise BackendUnavailable(
                "no usable fetch source"
                + (" for --sanitize (CLI required)" if sanitize else "")
            )

    # ------------------------------------------------------------------ setup
    def _add(self, name: str, factory) -> None:
        try:
            self._sources[name] = factory()
        except (BackendUnavailable, OSError) as exc:
            self.diagnostics.append({"source": name, "event": "unavailable", "detail": str(exc)})

    def _build_sources(self) -> None:
        wanted = {"auto": ("cli", "api", "sqlite"), "cli": ("cli",), "api": ("api",), "sqlite": ("sqlite",)}[
            self.backend
        ]
        if "cli" in wanted:
            self._add("cli", lambda: CliSource(self.env.opencode_bin, timeout=self.timeout, sanitize=self.sanitize))
        if "api" in wanted:
            self._add("api", lambda: ApiSource(self.env.opencode_bin, timeout=self.timeout))
        if "sqlite" in wanted:
            if not self.env.db_path or not self.env.db_path.exists():
                self.diagnostics.append(
                    {
                        "source": "sqlite",
                        "event": "unavailable",
                        "detail": f"database not found: {self.env.db_path}",
                    }
                )
            else:
                self._add("sqlite", lambda: SqliteSource(self.env.db_path))  # type: ignore[arg-type]

    # ------------------------------------------------------------------ public
    @property
    def available_backends(self) -> List[str]:
        return [name for name in ("cli", "api", "sqlite") if name in self._sources]

    @property
    def db_snapshot_path(self):
        source = self._sources.get("sqlite")
        return source.db_snapshot_path if isinstance(source, SqliteSource) else None

    def list_sessions(self) -> List[SessionRef]:
        empty = False
        for name in self.list_order:
            source = self._sources[name]
            try:
                refs = source.list_sessions()
            except Exception as exc:  # a broken source must not kill the export
                self.diagnostics.append(
                    {"source": name, "event": "list_failed", "detail": str(exc)}
                )
                continue
            if refs:
                return refs
            empty = True
            self.diagnostics.append(
                {"source": name, "event": "list_empty", "detail": "returned zero sessions"}
            )
        if empty:
            return []
        raise BackendUnavailable(
            "no source could list sessions: "
            + "; ".join(f"{d['source']}: {d['detail']}" for d in self.diagnostics[-4:])
        )

    def fetch(self, session_id: str) -> ExportResult:
        if session_id in self._cache:
            return self._cache[session_id]

        attempts = []
        for name in self.fetch_order:
            source = self._sources[name]
            try:
                doc = source.fetch(session_id)
            except Exception as exc:
                attempts.append(f"{name}: {exc}")
                self.diagnostics.append(
                    {"source": name, "session": session_id, "event": "fetch_failed", "detail": str(exc)}
                )
                continue

            issues = validate_export(doc)
            if has_errors(issues):
                detail = "; ".join(str(issue) for issue in issues if issue.level == "error")
                attempts.append(f"{name}: invalid export ({detail})")
                self.diagnostics.append(
                    {
                        "source": name,
                        "session": session_id,
                        "event": "contract_failed",
                        "detail": detail,
                    }
                )
                continue

            ref = ref_from_export(doc, fallback_id=session_id)
            result = ExportResult(
                session_id=session_id,
                doc=doc,
                backend=name,
                ref=ref,
                issues=issues,
            )
            self._cache[session_id] = result
            return result

        raise ExportFailed(
            f"could not export {session_id} from any source: " + " | ".join(attempts)
        )

    def close(self) -> None:
        for source in self._sources.values():
            try:
                source.close()
            except Exception:
                pass

    def __enter__(self) -> "Provider":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
