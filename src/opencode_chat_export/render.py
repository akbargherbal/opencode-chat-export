"""Render an export document to a human-readable Markdown transcript.

The JSON files are the lossless, re-importable artifact.  The Markdown file is
for reading offline in any editor; it deliberately omits embedded file bytes
(``files[].data``), which can be large base64 blobs, and keeps the tool calls and
reasoning that explain how an answer was reached.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .util import json_dumps, ms_to_iso


def _fence(text: str, language: str = "") -> str:
    runs = re.findall(r"`+", text or "")
    longest = max((len(run) for run in runs), default=0)
    marker = "`" * max(3, longest + 1)
    body = (text or "").rstrip("\n")
    return f"{marker}{language}\n{body}\n{marker}"


def _json_block(value: Any, language: str = "json") -> str:
    if isinstance(value, str):
        return _fence(value, language)
    try:
        text = json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return _fence(text, language)


def _render_files(files: Any) -> str:
    if not isinstance(files, list) or not files:
        return ""
    lines = ["", "**Files**", ""]
    for item in files:
        if not isinstance(item, dict):
            lines.append(f"- `{item}`")
            continue
        name = item.get("name") or item.get("path") or "(unnamed)"
        mime = item.get("mime") or "unknown type"
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        uri = source.get("uri") or source.get("path") or ""
        suffix = f" — `{uri}`" if uri else ""
        lines.append(f"- `{name}` ({mime}){suffix}")
    return "\n".join(lines)


def _render_agents(agents: Any) -> str:
    if not isinstance(agents, list) or not agents:
        return ""
    names = []
    for agent in agents:
        if isinstance(agent, dict):
            names.append(str(agent.get("name") or agent.get("id") or agent))
        else:
            names.append(str(agent))
    return "\n\n**Agents:** " + ", ".join(f"`{name}`" for name in names)


def _render_tool_state(state: Any) -> str:
    if not isinstance(state, dict):
        return ""
    parts = []
    status = state.get("status")
    if status:
        parts.append(f"**Status:** `{status}`")
    if "input" in state:
        parts.append("**Input**\n\n" + _json_block(state.get("input")))
    if "output" in state and state.get("output") not in (None, ""):
        output = state.get("output")
        if isinstance(output, str):
            parts.append("**Output**\n\n" + _fence(output, "text"))
        else:
            parts.append("**Output**\n\n" + _json_block(output))
    if "content" in state and state.get("content"):
        content = state.get("content")
        if isinstance(content, list):
            rendered = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    rendered.append(str(item.get("text") or ""))
                else:
                    rendered.append(_json_block(item))
            parts.append("**Content**\n\n" + "\n\n".join(rendered))
        else:
            parts.append("**Content**\n\n" + _json_block(content))
    if "error" in state and state.get("error"):
        error = state.get("error")
        if isinstance(error, dict):
            message = error.get("message") or json.dumps(error, ensure_ascii=False)
        else:
            message = str(error)
        parts.append("**Error**\n\n" + _fence(message, "text"))
    if "metadata" in state and state.get("metadata"):
        parts.append("**Metadata**\n\n" + _json_block(state.get("metadata")))
    return "\n\n".join(parts)


def _render_assistant_content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    blocks = []
    for part in content:
        if not isinstance(part, dict):
            blocks.append(_json_block(part))
            continue
        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text") or ""
            if text.strip():
                blocks.append(str(text).rstrip())
        elif part_type == "reasoning":
            text = part.get("text") or ""
            if text.strip():
                blocks.append(
                    "<details>\n<summary>Reasoning</summary>\n\n"
                    + str(text).rstrip()
                    + "\n\n</details>"
                )
        elif part_type == "tool":
            name = part.get("name") or "tool"
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            status = state.get("status") or ("executed" if part.get("executed") else "not executed")
            heading = f"### Tool: `{name}` ({status})"
            body = _render_tool_state(state)
            blocks.append(heading + ("\n\n" + body if body else ""))
        else:
            blocks.append(f"### Part: `{part_type or 'unknown'}`\n\n" + _json_block(part))
    return "\n\n".join(blocks)


def render_transcript(
    doc: Dict[str, Any],
    *,
    opencode_version: Optional[str] = None,
    backend: Optional[str] = None,
    exported_at: Optional[str] = None,
    host: Optional[str] = None,
) -> str:
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    messages = doc.get("messages") if isinstance(doc.get("messages"), list) else []

    title = info.get("title") or "(untitled session)"
    session_id = info.get("id") or ""
    time_info = info.get("time") if isinstance(info.get("time"), dict) else {}
    location = info.get("location") if isinstance(info.get("location"), dict) else {}
    created = ms_to_iso(time_info.get("created"))
    updated = ms_to_iso(time_info.get("updated"))
    directory = location.get("directory") or ""

    lines: List[str] = [f"# {title}", ""]
    lines.append(f"- **Session:** `{session_id}`")
    if created:
        lines.append(f"- **Created:** {created}")
    if updated:
        lines.append(f"- **Updated:** {updated}")
    if directory:
        lines.append(f"- **Directory:** `{directory}`")
    if info.get("parentID"):
        lines.append(f"- **Parent session:** `{info['parentID']}`")
    if info.get("agent"):
        lines.append(f"- **Agent:** `{info['agent']}`")
    model = info.get("model")
    if isinstance(model, dict):
        model_name = "/".join(
            str(part) for part in (model.get("providerID"), model.get("id")) if part
        )
        if model_name:
            lines.append(f"- **Model:** `{model_name}`")
    lines.append(f"- **Messages:** {len(messages)}")
    if opencode_version:
        lines.append(f"- **OpenCode:** {opencode_version}")
    if backend:
        lines.append(f"- **Export backend:** `{backend}`")
    if exported_at:
        lines.append(f"- **Exported:** {exported_at}")
    if host:
        lines.append(f"- **Host:** `{host}`")

    tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
    if tokens:
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        lines.append(
            "- **Tokens:** "
            f"input {tokens.get('input', 0)}, output {tokens.get('output', 0)}, "
            f"reasoning {tokens.get('reasoning', 0)}, "
            f"cache read {cache.get('read', 0)}, cache write {cache.get('write', 0)}"
        )
    if info.get("cost") is not None:
        lines.append(f"- **Cost:** {info.get('cost')}")

    lines.extend(
        [
            "",
            "> This file is rendered from the lossless `session.json` in the same "
            "archive. Embedded file bytes are intentionally omitted.",
            "",
        ]
    )

    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            lines.append(f"## Message {index + 1}\n\n" + _json_block(message))
            continue
        message_type = message.get("type") or "unknown"
        timestamp = (
            ms_to_iso(message.get("time", {}).get("created"))
            if isinstance(message.get("time"), dict)
            else None
        )

        if message_type == "user":
            heading = "## User"
            body = str(message.get("text") or "").rstrip()
            extras = _render_files(message.get("files")) + _render_agents(message.get("agents"))
            lines.append(heading + (f"  <!-- {timestamp} -->" if timestamp else ""))
            lines.append("")
            if body:
                lines.append(body)
            if extras.strip():
                lines.append(extras)
            lines.append("")
        elif message_type == "assistant":
            heading = "## Assistant"
            lines.append(heading + (f"  <!-- {timestamp} -->" if timestamp else ""))
            lines.append("")
            body = _render_assistant_content(message.get("content"))
            if body.strip():
                lines.append(body)
            else:
                lines.append("_(no text or tool output)_")
            lines.append("")
        elif message_type == "synthetic":
            lines.append("## Synthetic" + (f"  <!-- {timestamp} -->" if timestamp else ""))
            lines.append("")
            lines.append(str(message.get("text") or "").rstrip() or "_(empty)_")
            lines.append("")
        elif message_type == "compaction":
            status = message.get("status") or "unknown"
            lines.append(f"## Compaction ({status})" + (f"  <!-- {timestamp} -->" if timestamp else ""))
            lines.append("")
            if message.get("reason"):
                lines.append(f"**Reason:** {message.get('reason')}")
                lines.append("")
            summary = message.get("summary")
            if summary:
                lines.append(str(summary).rstrip())
            else:
                lines.append("_(no summary)_")
            lines.append("")
        elif message_type == "idle":
            lines.append("## Idle" + (f"  <!-- {timestamp} -->" if timestamp else ""))
            lines.append("")
            lines.append(f"Outcome: `{message.get('outcome') or 'unknown'}`")
            lines.append("")
        else:
            lines.append(f"## `{message_type}`" + (f"  <!-- {timestamp} -->" if timestamp else ""))
            lines.append("")
            lines.append(_json_block(message))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
