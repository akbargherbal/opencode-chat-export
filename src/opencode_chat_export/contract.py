"""The OpenCode export contract.

OpenCode's export format is intentionally loose: it is a JSON object with
``info`` and ``messages`` where each message has an ``id``, a ``type``, and
type-specific fields.  New message/part types appear over time.  The validator
therefore distinguishes:

* **errors** — the document cannot be a usable transcript (missing info,
  missing messages, a message without an id/type);
* **warnings** — forward-compatible drift (an unknown message type or part
  type, a missing optional timestamp).

The archive still keeps documents with warnings.  A session is only rejected
when a source returns a document with errors; the provider then tries the next
source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

# Types observed in OpenCode 2.4.1, plus a few historical ones.  Unknown types
# are warnings, never fatal: a future OpenCode must still be exportable.
KNOWN_MESSAGE_TYPES = {
    "user",
    "assistant",
    "synthetic",
    "compaction",
    "idle",
    "shell",
    "skill",
    "system",
    "location-switched",
    "model-selected",
    "agent-selected",
}

KNOWN_PART_TYPES = {"text", "reasoning", "tool"}

# Fields that must exist for an export to be useful.  These are deliberately
# minimal; see validate_export for the structural checks.
REQUIRED_MESSAGE_FIELDS = ("id", "type")


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }

    def __str__(self) -> str:
        return f"{self.level}: {self.path}: {self.message}"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_export(doc: Any, *, strict: bool = False) -> List[Issue]:
    """Return issues found in an export document.

    ``strict=True`` promotes warnings to errors.  This is useful in CI (a
    fixture that suddenly has an unknown type is worth looking at) but should
    stay off for real backups.
    """
    issues: List[Issue] = []

    def add(level: str, code: str, path: str, message: str) -> None:
        issues.append(Issue(level=level, code=code, path=path, message=message))

    if not isinstance(doc, dict):
        add("error", "not_an_object", "$", "export must be a JSON object")
        return _finish(issues, strict)

    info = doc.get("info")
    messages = doc.get("messages")

    if not isinstance(info, dict):
        add("error", "missing_info", "info", "missing or non-object 'info'")
    else:
        session_id = info.get("id")
        if not isinstance(session_id, str) or not session_id:
            add("error", "missing_session_id", "info.id", "info.id must be a non-empty string")
        if "title" not in info:
            add("warning", "missing_title", "info.title", "info.title is missing")
        elif info.get("title") is not None and not isinstance(info.get("title"), str):
            add("warning", "invalid_title", "info.title", "info.title is not a string")
        if "time" not in info:
            add("warning", "missing_info_time", "info.time", "info.time is missing")
        elif not isinstance(info.get("time"), dict):
            add("warning", "invalid_info_time", "info.time", "info.time is not an object")
        if "location" in info and not isinstance(info.get("location"), dict):
            add("warning", "invalid_location", "info.location", "info.location is not an object")

    if not isinstance(messages, list):
        add("error", "missing_messages", "messages", "missing or non-array 'messages'")
        return _finish(issues, strict)

    seen_ids = set()
    for index, message in enumerate(messages):
        path = f"messages[{index}]"
        if not isinstance(message, dict):
            add("error", "message_not_object", path, "message is not an object")
            continue

        message_id = message.get("id")
        message_type = message.get("type")

        if not isinstance(message_id, str) or not message_id:
            add("error", "message_missing_id", f"{path}.id", "message id is missing")
        elif message_id in seen_ids:
            add("warning", "duplicate_message_id", f"{path}.id", f"duplicate id {message_id!r}")
        else:
            seen_ids.add(message_id)

        if not isinstance(message_type, str) or not message_type:
            add("error", "message_missing_type", f"{path}.type", "message type is missing")
        elif message_type not in KNOWN_MESSAGE_TYPES:
            add(
                "warning",
                "unknown_message_type",
                f"{path}.type",
                f"unknown message type {message_type!r} (kept as-is)",
            )

        if "time" not in message:
            add("warning", "message_missing_time", f"{path}.time", "message time is missing")
        elif not isinstance(message.get("time"), dict):
            add("warning", "message_invalid_time", f"{path}.time", "message time is not an object")

        if message_type == "assistant":
            content = message.get("content")
            if content is None:
                add("warning", "assistant_missing_content", f"{path}.content", "assistant content is missing")
            elif not isinstance(content, list):
                add("error", "assistant_invalid_content", f"{path}.content", "assistant content is not an array")
            else:
                for part_index, part in enumerate(content):
                    part_path = f"{path}.content[{part_index}]"
                    if not isinstance(part, dict):
                        add("error", "part_not_object", part_path, "content part is not an object")
                        continue
                    part_type = part.get("type")
                    if not isinstance(part_type, str) or not part_type:
                        add("error", "part_missing_type", f"{part_path}.type", "content part type is missing")
                    elif part_type not in KNOWN_PART_TYPES:
                        add(
                            "warning",
                            "unknown_part_type",
                            f"{part_path}.type",
                            f"unknown content part type {part_type!r} (kept as-is)",
                        )
                    if part_type == "tool":
                        if not part.get("name"):
                            add("warning", "tool_missing_name", f"{part_path}.name", "tool part has no name")
                        if not isinstance(part.get("state"), dict):
                            add("warning", "tool_missing_state", f"{part_path}.state", "tool part has no state object")

        if message_type == "user" and "text" in message and not isinstance(message.get("text"), str):
            add("warning", "invalid_user_text", f"{path}.text", "user text is not a string")

    if not messages:
        add("warning", "empty_session", "messages", "session has no messages")

    return _finish(issues, strict)


def _finish(issues: Iterable[Issue], strict: bool) -> List[Issue]:
    result = list(issues)
    if strict:
        for issue in result:
            issue.level = "error"
    return result


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(issue.level == "error" for issue in issues)


def issue_counts(issues: Iterable[Issue]) -> dict:
    counts = {"error": 0, "warning": 0}
    for issue in issues:
        counts[issue.level] = counts.get(issue.level, 0) + 1
    return counts


def first_error(issues: Iterable[Issue]) -> Optional[Issue]:
    for issue in issues:
        if issue.level == "error":
            return issue
    return None
