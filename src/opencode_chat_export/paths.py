"""Discover OpenCode's binary, data directory, database, and version.

The paths below are the ones OpenCode 2.4.x uses on Linux/macOS.  Every value can
be overridden explicitly (CLI flags or environment) so the tool also works with a
portable install, a copied data directory, or a test fixture.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?")


def _xdg_path(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else fallback


def default_data_dir(home: Optional[Path] = None) -> Path:
    home = home or Path.home()
    if sys.platform == "darwin" and not os.environ.get("XDG_DATA_HOME"):
        # OpenCode uses XDG paths in practice; keep the XDG fallback first and
        # only offer the macOS location as a last resort in discovery.
        return home / ".local" / "share" / "opencode"
    return _xdg_path("XDG_DATA_HOME", home / ".local" / "share") / "opencode"


def default_config_dir(home: Optional[Path] = None) -> Path:
    home = home or Path.home()
    return _xdg_path("XDG_CONFIG_HOME", home / ".config") / "opencode"


def parse_version(text: str) -> Optional[str]:
    match = _VERSION_RE.search(text or "")
    return match.group(0) if match else None


def version_tuple(version: Optional[str]) -> Optional[tuple]:
    if not version:
        return None
    match = _VERSION_RE.search(version)
    if not match:
        return None
    return tuple(int(part) for part in match.groups()[:3])


def find_opencode_binary(explicit: Optional[str] = None) -> Optional[str]:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_value = os.environ.get("OPENCODE_BIN")
    if env_value:
        candidates.append(env_value)
    found = shutil.which("opencode")
    if found:
        candidates.append(found)
    candidates.append(str(Path.home() / ".opencode" / "bin" / "opencode"))

    for candidate in candidates:
        expanded = str(Path(candidate).expanduser())
        if os.path.sep in expanded or os.path.exists(expanded):
            if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return expanded
        else:
            resolved = shutil.which(expanded)
            if resolved:
                return resolved
    return None


def _run(binary: str, args: list, timeout: int = 20) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
    except (OSError, subprocess.SubprocessError):
        return None


def probe_version(binary: Optional[str]) -> Optional[str]:
    if not binary:
        return None
    result = _run(binary, ["--version"], timeout=20)
    if result is None or result.returncode != 0:
        return None
    return parse_version(result.stdout or result.stderr)


def probe_db_path(binary: Optional[str]) -> Optional[Path]:
    """Ask OpenCode for its DB path; fall back silently when unavailable."""
    if not binary:
        return None
    result = _run(binary, ["debug", "paths", "db"], timeout=20)
    if result is None or result.returncode != 0:
        return None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        return None
    candidate = Path(lines[-1]).expanduser()
    return candidate if candidate.exists() else None


@dataclass
class Environment:
    home: Path
    data_dir: Path
    config_dir: Path
    db_path: Optional[Path]
    opencode_bin: Optional[str]
    opencode_version: Optional[str]
    host: str = field(default_factory=lambda: socket.gethostname() or "unknown")
    notes: list = field(default_factory=list)

    @property
    def db_exists(self) -> bool:
        return bool(self.db_path and self.db_path.exists())

    @property
    def version_tuple(self) -> Optional[tuple]:
        return version_tuple(self.opencode_version)

    def to_dict(self) -> dict:
        return {
            "home": str(self.home),
            "data_dir": str(self.data_dir),
            "config_dir": str(self.config_dir),
            "db_path": str(self.db_path) if self.db_path else None,
            "db_exists": self.db_exists,
            "opencode_bin": self.opencode_bin,
            "opencode_version": self.opencode_version,
            "host": self.host,
            "notes": list(self.notes),
        }


def discover_environment(
    *,
    data_dir: Optional[str] = None,
    db_path: Optional[str] = None,
    opencode_bin: Optional[str] = None,
) -> Environment:
    home = Path.home()
    notes: list = []

    resolved_data = (
        Path(data_dir).expanduser()
        if data_dir
        else Path(os.environ["OPENCODE_DATA_DIR"]).expanduser()
        if os.environ.get("OPENCODE_DATA_DIR")
        else default_data_dir(home)
    )

    resolved_bin = find_opencode_binary(opencode_bin)
    if resolved_bin is None:
        candidate = home / ".opencode" / "bin" / "opencode"
        if candidate.exists():
            resolved_bin = str(candidate)
    version = probe_version(resolved_bin)
    if resolved_bin and not version:
        notes.append("opencode binary found but --version did not return a version")

    resolved_db: Optional[Path]
    if db_path:
        resolved_db = Path(db_path).expanduser()
    elif os.environ.get("OPENCODE_DB"):
        resolved_db = Path(os.environ["OPENCODE_DB"]).expanduser()
    else:
        resolved_db = probe_db_path(resolved_bin)
        if resolved_db is None:
            resolved_db = resolved_data / "opencode.db"
            if not resolved_db.exists():
                mac_candidate = (
                    home / "Library" / "Application Support" / "opencode" / "opencode.db"
                )
                if mac_candidate.exists():
                    resolved_db = mac_candidate
                    notes.append(f"using macOS data location: {mac_candidate.parent}")

    if resolved_db and not resolved_db.exists():
        notes.append(f"database not found at {resolved_db}")

    return Environment(
        home=home,
        data_dir=resolved_data,
        config_dir=default_config_dir(home),
        db_path=resolved_db,
        opencode_bin=resolved_bin,
        opencode_version=version,
        notes=notes,
    )
