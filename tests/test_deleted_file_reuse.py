"""Deletion and rename hooks leave tests to explicit verification."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "templates/vibe_scripts/quality-hook.py"


@pytest.fixture(params=["codex", "claude"])
def deletion_hook(tmp_path, monkeypatch, request):
    spec = importlib.util.spec_from_file_location("deletion_hook", SOURCE)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    hook.ensure_helpers()
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".cache/\nvibe_scripts/\n")
    (tmp_path / "deleted.py").write_text("old code")
    (tmp_path / "source.py").write_text("original")
    provider = request.param
    makefile = tmp_path / "vibe_scripts" / provider / "Makefile"
    makefile.parent.mkdir(parents=True)
    makefile.write_text("test-ai:\n\t@touch .cache/tests-started\n\t@false\n")
    calls = []

    def check(root, paths, **kwargs):
        calls.append(paths)
        return hook.QUALITY.result("passed", "edited files", [], command_count=1)

    def forbidden(*args, **kwargs):
        pytest.fail("Automatic hook attempted a test run or full test fingerprint")

    monkeypatch.setattr(hook.CHECKER, "check_files", check)
    monkeypatch.setattr(hook, "test_signature", forbidden)
    monkeypatch.setattr(hook, "test_receipt", forbidden)
    monkeypatch.setattr(hook.QUALITY, "run_quality", forbidden)
    payload = {"session_id": "deletions"}
    hook.handle(tmp_path, provider, "SessionStart", payload)
    (tmp_path / "deleted.py").unlink()
    return hook, tmp_path, payload, provider, calls


def event(fixture, name="Stop", inputs=None):
    hook, root, payload, provider, _ = fixture
    return hook.handle(root, provider, name, payload | {"tool_input": inputs or {}})


def state_path(root):
    return next((root / ".cache/quality-sessions").glob("*.json"))


def test_deletion_only_is_honestly_skipped_without_tests(deletion_hook, capsys):
    _, root, _, provider, calls = deletion_hook
    for _ in range(5):
        assert event(deletion_hook) == {}
    state = json.loads(state_path(root).read_text())
    assert state["paths"] == ["deleted.py"]
    assert state["status"] == "not_checked"
    assert state["attempts"] == 0
    assert state["history"][-1]["command_count"] == 0
    assert calls == []
    assert not (root / ".cache/tests-started").exists()
    assert f"vibe_scripts/{provider}/Makefile test-ai" in capsys.readouterr().err


def test_post_delete_does_not_check_absent_file(deletion_hook):
    *_, calls = deletion_hook
    result = event(deletion_hook, "PostToolUse", {"path": "deleted.py"})
    assert "Tests were not run" in result["hookSpecificOutput"]["additionalContext"]
    assert calls == []


@pytest.mark.parametrize("via_patch", [False, True])
def test_move_checks_destination_and_retains_old_path(deletion_hook, via_patch):
    _, root, _, _, calls = deletion_hook
    (root / "source.py").rename(root / "renamed.py")
    inputs = (
        {
            "patch": "*** Begin Patch\n*** Update File: source.py\n*** Move to: renamed.py\n*** End Patch"
        }
        if via_patch
        else {"old_path": "source.py", "new_path": "renamed.py"}
    )
    event(deletion_hook, "PostToolUse", inputs)
    assert calls == [["renamed.py"]]
    assert event(deletion_hook) == {}
    assert calls[-1] == ["renamed.py"]
    assert json.loads(state_path(root).read_text())["paths"] == [
        "deleted.py",
        "renamed.py",
        "source.py",
    ]


def test_shell_move_is_discovered_at_stop(deletion_hook):
    _, root, _, _, calls = deletion_hook
    (root / "source.py").rename(root / "renamed.py")
    assert event(deletion_hook) == {}
    assert calls == [["renamed.py"]]


@pytest.mark.parametrize("status", ["findings", "tool_failure", "not_checked"])
def test_mixed_deletion_preserves_existing_file_failure(
    deletion_hook, monkeypatch, status
):
    hook, root, _, _, _ = deletion_hook
    (root / "source.py").write_text("changed")
    monkeypatch.setattr(
        hook.CHECKER,
        "check_files",
        lambda *a, **k: hook.QUALITY.result(status, "edited files", ["real failure"]),
    )
    result = event(deletion_hook)
    assert result["decision"] == "block"
    assert "real failure" in result["reason"]


def test_deletion_does_not_hide_missing_baseline(deletion_hook):
    _, root, _, _, _ = deletion_hook
    state_path(root).unlink()
    event(deletion_hook, "PostToolUse", {"path": "deleted.py"})
    result = event(deletion_hook)
    assert result["decision"] == "block"
    assert "baseline is missing" in result["reason"]


def test_restored_file_is_checked(deletion_hook):
    _, root, _, _, calls = deletion_hook
    assert event(deletion_hook) == {}
    (root / "deleted.py").write_text("restored")
    assert event(deletion_hook) == {}
    assert calls == [["deleted.py"]]


def test_old_failed_receipt_and_session_caches_do_not_block(deletion_hook):
    _, root, _, provider, calls = deletion_hook
    path = state_path(root)
    state = json.loads(path.read_text())
    state.update(deletion_tests={"signature": "old"}, verified_inputs="old", attempts=4)
    path.write_text(json.dumps(state))
    receipt = root / ".cache/quality-tests" / (provider + ".json")
    receipt.parent.mkdir()
    receipt.write_text('{"status": "tool_failure"}')
    assert event(deletion_hook) == {}
    state = json.loads(path.read_text())
    assert "deletion_tests" not in state
    assert "verified_inputs" not in state
    assert calls == []
