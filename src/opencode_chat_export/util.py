"""Small, dependency-free helpers shared by the exporters and tests."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Optional


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def now_iso() -> str:
    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def ms_to_iso(ms: Optional[int]) -> Optional[str]:
    if ms is None:
        return None
    try:
        value = dt.datetime.fromtimestamp(int(ms) / 1000.0, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time_ms(value: str) -> int:
    """Parse ``YYYY-MM-DD``, ISO-8601, or an epoch-millisecond integer."""
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    if re.fullmatch(r"\d{10,16}", text):
        return int(text)
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            f"invalid timestamp {value!r}; use YYYY-MM-DD or ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


def slugify(text: str, *, max_length: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:max_length].strip("-") or "session")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def json_dumps(value: Any, *, pretty: bool = True) -> str:
    if pretty:
        return json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def human_bytes(size: Optional[int]) -> str:
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size} B"


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_bytes(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


def first_line(text: str, limit: int = 300) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:limit]
