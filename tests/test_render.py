from __future__ import annotations

from opencode_chat_export.render import render_transcript


def test_render_sample(sample_export):
    text = render_transcript(
        sample_export,
        opencode_version="2.4.1",
        backend="cli",
        exported_at="2023-11-14T22:13:20Z",
        host="test-host",
    )
    assert "# Fixture export" in text
    assert "`ses_fixture_export_0001`" in text
    assert "## User" in text
    assert "Please inspect the widget." in text
    assert "## Assistant" in text
    assert "Reasoning" in text
    assert "Check the file first." in text
    assert "Tool: `read`" in text
    assert "print('hi')" in text
    assert "aGVsbG8=" not in text
    assert "OpenCode" in text
    assert "cli" in text


def test_render_unknown_message_is_preserved(sample_export):
    sample_export["messages"].append(
        {
            "id": "msg_future",
            "type": "future-event",
            "time": {"created": 1700000004000},
            "payload": {"x": 1},
        }
    )
    text = render_transcript(sample_export)
    assert "future-event" in text
    assert '"x": 1' in text
