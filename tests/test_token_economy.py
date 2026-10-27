"""Smaller messages must retain evidence, execution and honest measurements."""

import json
from pathlib import Path

import pytest
from test_project_memory import MEMORY
from test_quality_delivery import HOOK, WRAPPER, git


def test_context_keeps_full_evidence_and_relevant_source(tmp_path, monkeypatch):
    rows = [
        {"name": f"unrelated_{i}", "file_path": "other.py", "line_start": i}
        for i in range(60)
    ]
    rows.append(
        {"name": "repair_tracking", "file_path": "tracking.py", "line_start": 90}
    )
    raw = json.dumps(
        {
            "summary": "61 results",
            "confidence": "Dynamic callers may be missing",
            "result_count": 61,
            "results": rows,
        }
    )
    monkeypatch.setattr(MEMORY, "run", lambda *args: raw)
    status = {
        name: {
            "status": "current" if name == "crg" else "disabled",
            "paths": [],
            "error": None,
        }
        for name in ("crg", "graphify", "semantic")
    }
    value = MEMORY.context(tmp_path, "repair tracking", ["tracking.py"], status)
    assert len(value["code_context"]) == 3
    for query in value["code_context"]:
        assert "repair_tracking" in query["output"]
        assert "Dynamic callers may be missing" in query["output"]
        assert "more results" in query["output"]
        assert len(query["output"]) < len(raw) / 2
        assert (tmp_path / query["evidence"]).read_text() == raw
    report = MEMORY.usage_report(tmp_path)
    assert report["retrieval"]["requests"] == 1
    assert report["retrieval"]["raw_chars"] == len(raw) * 3
    assert report["weekly_allowance_saved"] is None
    assert report["main_agent_tokens"] is None


@pytest.mark.parametrize("raw", ["tool warning " * 200, "[]", "null"])
def test_nonstructured_context_is_bounded_without_claiming_absence(raw):
    assert MEMORY.query_brief(raw, {"task"}) == raw[:1000]


def test_context_retains_real_importer_and_inferred_test_fields():
    rows = [
        {"importer": "app::handler", "file": "app.py", "via_package": "package"},
        {
            "name": "test_handler",
            "file_path": "tests/test_app.py",
            "indirect": False,
            "inferred_by": "naming_convention",
        },
    ]
    raw = json.dumps({"results": rows, "result_count": None})
    brief = MEMORY.query_brief(raw, {"handler"})
    for row in rows:
        for key, value in row.items():
            assert json.dumps({key: value})[1:-1] in brief


def test_usage_preserves_unknowns_and_reports_corrupt_logs(tmp_path):
    (tmp_path / "AGENTS.md").write_text("instructions")
    report = MEMORY.usage_report(tmp_path)
    assert report["instruction_chars"] == {"AGENTS.md": 12}
    assert report["retrieval"] is None
    assert report["hook_events"] == {}
    cache = tmp_path / ".cache/quality-sessions"
    cache.mkdir(parents=True)
    (cache / "broken.json").write_text("not json")
    assert MEMORY.usage_report(tmp_path)["errors"]


def test_usage_does_not_count_missing_measurements_as_zero(tmp_path):
    cache = tmp_path / ".cache/quality-sessions"
    cache.mkdir(parents=True)
    (cache / "old.json").write_text(
        json.dumps({"history": [{"event": "Stop", "status": "passed"}]})
    )
    group = MEMORY.usage_report(tmp_path)["hook_events"]["Stop:passed"]
    assert group["events"] == 1
    assert group["emitted_bytes"] is None
    assert group["command_count"] is None
    assert group["bytes_per_event"] is None


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_repeated_hook_checks_execute_and_keep_complete_result(
    tmp_path, monkeypatch, provider
):
    git(tmp_path, "init", "-q")
    payload = {"session_id": "scope", "tool_input": {"file_path": "demo.py"}}
    HOOK.handle(tmp_path, provider, "SessionStart", payload)
    (tmp_path / "demo.py").write_text("broken")
    calls = []

    def check(*args, **kwargs):
        calls.append(1)
        return WRAPPER.result(
            "findings",
            "fixture",
            [f"demo.py:{i} F821 undefined name" for i in range(25)],
            diagnostic_signature="same",
            command_count=1,
        )

    monkeypatch.setattr(HOOK.CHECKER, "check_files", check)
    first = HOOK.handle(tmp_path, provider, "PostToolUse", payload)
    second = HOOK.handle(tmp_path, provider, "PostToolUse", payload)
    text = second["hookSpecificOutput"]["additionalContext"]
    assert len(json.dumps(second)) < len(json.dumps(first))
    assert "Rechecked; diagnostics unchanged" in text
    assert "demo.py:0 F821" in text
    assert "summary log (read first):" in text.lower()
    assert "explicit scope approval" in text
    stopped = HOOK.handle(tmp_path, provider, "Stop", payload)
    assert stopped["decision"] == "block"
    assert len(calls) == 3, "Reporting deduplication must not skip checks"
    records = list((tmp_path / ".cache/quality-sessions/results").glob("*.json"))
    assert len(records) == 3
    assert all(len(json.loads(path.read_text())["summary"]) == 25 for path in records)
    monkeypatch.setattr(
        HOOK.CHECKER,
        "check_files",
        lambda *args, **kwargs: WRAPPER.result(
            "passed", "fixture", ["passed"], command_count=1
        ),
    )
    assert HOOK.handle(tmp_path, provider, "Stop", payload) == {}


def test_passed_hook_presentation_is_short_and_honest():
    value = WRAPPER.result(
        "passed",
        "edited files",
        ["format: passed", "lint: passed"],
        command_count=2,
        duration_seconds=0.5,
    )
    response = HOOK.response("codex", "PostToolUse", value)
    message = response["hookSpecificOutput"]["additionalContext"]
    assert message == "[ai-quality] edited files: passed (2 commands, 0.5s)"
    assert value["summary"] == ["format: passed", "lint: passed"]


def test_mixed_deletion_pass_keeps_explicit_test_warning(tmp_path, monkeypatch, capsys):
    git(tmp_path, "init", "-q")
    (tmp_path / "old.py").write_text("old")
    HOOK.handle(tmp_path, "codex", "SessionStart", {"session_id": "mixed"})
    (tmp_path / "old.py").unlink()
    (tmp_path / "new.py").write_text("new")
    monkeypatch.setattr(
        HOOK.CHECKER,
        "check_files",
        lambda *args, **kwargs: WRAPPER.result(
            "passed", "fixture", ["lint: passed"], command_count=1
        ),
    )
    payload = {
        "session_id": "mixed",
        "tool_input": {"old_path": "old.py", "new_path": "new.py"},
    }
    result = HOOK.handle(tmp_path, "codex", "PostToolUse", payload)
    text = result["hookSpecificOutput"]["additionalContext"]
    assert "Tests were not run" in text and "test-ai" in text
    assert "summary log (read first):" in text.lower()
    assert HOOK.handle(tmp_path, "codex", "Stop", payload) == {}
    assert "Tests were not run" in capsys.readouterr().err


def test_evaluation_pack_covers_twelve_independent_outcomes():
    path = (
        Path(__file__).resolve().parents[1]
        / "templates/token-evaluation/evaluation.json"
    )
    cases = json.loads(path.read_text())["cases"]
    expected = {
        (language, kind)
        for language in ("python", "javascript", "php")
        for kind in ("mechanical", "behavior", "debug", "review")
    }
    assert {(case["language"], case["kind"]) for case in cases} == expected
    assert len({case["id"] for case in cases}) == len(cases) == 12
    assert all(
        case["source"] and case["reference"] and len(case["acceptance"]) >= 3
        for case in cases
    )
