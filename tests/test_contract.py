from __future__ import annotations

import copy

from opencode_chat_export.contract import has_errors, issue_counts, validate_export


def test_sample_export_is_valid(sample_export):
    issues = validate_export(sample_export)
    assert not has_errors(issues), [str(issue) for issue in issues]


def test_missing_info_and_messages_are_errors():
    issues = validate_export({})
    assert has_errors(issues)
    codes = {issue.code for issue in issues}
    assert {"missing_info", "missing_messages"} <= codes


def test_missing_session_id_is_error(sample_export):
    doc = copy.deepcopy(sample_export)
    del doc["info"]["id"]
    issues = validate_export(doc)
    assert any(issue.code == "missing_session_id" and issue.level == "error" for issue in issues)


def test_unknown_message_and_part_types_are_warnings(sample_export):
    doc = copy.deepcopy(sample_export)
    doc["messages"][0]["type"] = "future-event"
    doc["messages"][1]["content"].append({"type": "image", "url": "x"})
    issues = validate_export(doc)
    assert not has_errors(issues)
    codes = {issue.code for issue in issues}
    assert "unknown_message_type" in codes
    assert "unknown_part_type" in codes


def test_assistant_content_must_be_an_array(sample_export):
    doc = copy.deepcopy(sample_export)
    doc["messages"][1]["content"] = {"not": "a list"}
    issues = validate_export(doc)
    assert any(issue.code == "assistant_invalid_content" and issue.level == "error" for issue in issues)


def test_duplicate_message_ids_warn(sample_export):
    doc = copy.deepcopy(sample_export)
    doc["messages"][1]["id"] = doc["messages"][0]["id"]
    issues = validate_export(doc)
    assert any(issue.code == "duplicate_message_id" and issue.level == "warning" for issue in issues)


def test_strict_promotes_warnings(sample_export):
    doc = copy.deepcopy(sample_export)
    doc["messages"][0]["type"] = "future-event"
    issues = validate_export(doc, strict=True)
    assert has_errors(issues)
    assert issue_counts(issues)["error"] >= 1


def test_empty_messages_is_a_warning(sample_export):
    doc = copy.deepcopy(sample_export)
    doc["messages"] = []
    issues = validate_export(doc)
    assert not has_errors(issues)
    assert any(issue.code == "empty_session" for issue in issues)
