"""Shared diagnostic, hook delivery and controlled targeted-route regression tests."""

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "vibe_scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), TEMPLATES / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WRAPPER = load("ai-quality-wrapper")
CHECKER = load("agent-check-edited")
HOOK = load("quality-hook")
HOOK.ensure_helpers()


def command(tmp_path, source, **kwargs):
    return WRAPPER.run_quality(
        [sys.executable, "-c", source], label="fixture", cwd=tmp_path, **kwargs
    )


def test_structured_pass_and_metrics(tmp_path):
    value = command(tmp_path, "print('finished')")
    assert value["status"] == "passed"
    assert value["command_count"] == 1
    assert value["duration_seconds"] > 0
    assert value["output_bytes"] == len("finished\n")
    assert "finished" in Path(value["log"]).read_text()


def test_native_findings_and_bounded_diagnostics(tmp_path):
    report = {
        "comments": [
            {
                "file": "demo.sh",
                "line": i,
                "column": 2,
                "level": "error",
                "code": 2086,
                "message": "Quote the variable",
            }
            for i in range(1, 40)
        ]
    }
    value = command(
        tmp_path,
        f"print({json.dumps(report)!r}); raise SystemExit(1)",
        adapter="native:shellcheck-json1",
        max_lines=8,
    )
    assert value["status"] == "findings"
    assert len(value["summary"]) <= 8
    assert any("demo.sh:1:2" in line and "SC2086" in line for line in value["summary"])
    assert any("omitted" in line for line in value["summary"])


@pytest.mark.parametrize("source", ["print('{malformed')", "print('{}')"])
def test_native_parser_failure_is_not_pass(tmp_path, source):
    value = command(tmp_path, source, adapter="native:phpstan-json")
    assert value["status"] == "tool_failure"
    assert "Diagnostic adapter:" in value["summary"][0]


def test_missing_tool_and_logging_failure(tmp_path):
    value = WRAPPER.run_quality(
        [str(tmp_path / "missing")], label="missing", cwd=tmp_path
    )
    assert value["status"] == "tool_failure"
    assert "No such file" in value["summary"][0]
    (tmp_path / "not-a-directory").write_text("file")
    value = command(tmp_path, "print('never runs')", log_dir="not-a-directory/logs")
    assert value["status"] == "tool_failure"
    assert value["command_count"] == 0
    assert "permissions" in value["summary"][0]


def test_timeout_retains_partial_output_and_kills_children(tmp_path):
    source = "import subprocess,time,sys; print('partial failure',flush=True); subprocess.Popen([sys.executable,'-c',\"import time; from pathlib import Path; time.sleep(1); Path('orphan').write_text('bad')\"]); time.sleep(10)"
    value = command(tmp_path, source, timeout=0.15)
    assert value["status"] == "tool_failure"
    assert value["returncode"] == 124
    assert "partial failure" in Path(value["log"]).read_text()
    assert value["duration_seconds"] < 2
    time.sleep(1.1)
    assert not (tmp_path / "orphan").exists()


def test_logs_exist_while_running(tmp_path):
    wrapper = subprocess.Popen(
        [
            sys.executable,
            str(TEMPLATES / "ai-quality-wrapper.py"),
            "--label",
            "stream",
            "--",
            sys.executable,
            "-c",
            "import time; print('live',flush=True); time.sleep(.8)",
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            logs = list((tmp_path / ".cache/ai-quality").glob("*.log"))
            if logs and "live" in logs[0].read_text():
                assert wrapper.poll() is None
                break
            time.sleep(0.02)
        else:
            pytest.fail("command output was not logged until completion")
    finally:
        wrapper.communicate(timeout=3)


def executable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + content)
    path.chmod(0o755)


def test_php_first_file_failure_survives_successful_later_file(tmp_path, monkeypatch):
    executable(
        tmp_path / "bin/php",
        'case "$2" in *bad.php) echo "bad.php:1 Parse error"; exit 1;; *) echo "No syntax errors detected in $2";; esac\n',
    )
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "bad.php").write_text("bad")
    (tmp_path / "good.php").write_text("good")
    value = CHECKER.check_files(tmp_path, ["bad.php", "good.php"])
    assert value["status"] == "findings"
    assert any("bad.php:1 Parse error" in line for line in value["summary"])
    assert "good.php" in Path(value["results"][0]["log"]).read_text()


@pytest.mark.parametrize(
    "version,code,status",
    [
        (3, 1, "passed"),
        (3, 3, "tool_failure"),
        (4, 0, "passed"),
        (4, 1, "findings"),
        (4, 16, "tool_failure"),
    ],
)
def test_phpcbf_version_specific_exit_status(
    tmp_path, monkeypatch, version, code, status
):
    executable(tmp_path / "bin/php", "exit 0\n")
    executable(
        tmp_path / "vendor/bin/phpcbf",
        f'if [ "$1" = "--version" ]; then echo "PHP_CodeSniffer version {version}.0.0"; exit 0; fi\necho "fixer result"\nexit {code}\n',
    )
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "demo.php").write_text("<?php")
    value = CHECKER.check_files(tmp_path, ["demo.php"])
    assert value["status"] == status


def test_required_missing_and_zero_checks_are_not_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    (tmp_path / "demo.py").write_text("pass")
    assert CHECKER.check_files(tmp_path, ["demo.py"])["status"] == "not_checked"
    (tmp_path / "data.unknown").write_text("data")
    assert CHECKER.check_files(tmp_path, ["data.unknown"])["status"] == "not_checked"
    assert CHECKER.check_files(tmp_path, [])["status"] == "not_checked"


def test_patch_payload_tracks_all_paths_and_rejects_outside(tmp_path):
    payload = {
        "tool_input": {
            "command": "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: a.py\n+x\n*** Update File: b.py\n*** Move to: c.py\n@@\n-x\n+y\n*** Delete File: d.py\n*** Add File: ../escape.py\n*** End Patch\nPATCH"
        }
    }
    assert HOOK.payload_paths(tmp_path, payload) == ["a.py", "b.py", "c.py", "d.py"]
    assert HOOK.payload_paths(
        tmp_path, {"tool_input": {"edits": [{"file_path": "a.py"}, {"path": "b.py"}]}}
    ) == ["a.py", "b.py"]


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_provider_delivery_preserves_edit_and_requests_stop_repair(provider):
    value = WRAPPER.result("findings", "lint", ["demo.py:4 F821 undefined name"])
    edited = HOOK.response(provider, "PostToolUse", value)
    assert set(edited) == {"hookSpecificOutput"}
    assert "F821" in edited["hookSpecificOutput"]["additionalContext"]
    stopped = HOOK.response(provider, "Stop", value)
    assert stopped["decision"] == "block"
    assert "continue" not in stopped
    assert HOOK.response(provider, "Stop", WRAPPER.result("passed", "lint", [])) == {}
    assert "Reassessment" in HOOK.response(provider, "Stop", value, 3)["reason"]
    exhausted = HOOK.response(provider, "Stop", value, 4)
    assert exhausted["continue"] is False
    assert "blocked/unverified" in exhausted["stopReason"]


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("status", ["findings", "tool_failure", "not_checked"])
def test_scope_guidance_preserves_failure_diagnostics_and_blocks(provider, status):
    value = WRAPPER.result(status, "lint", ["demo.py:4 F821 undefined name"])
    original = json.loads(json.dumps(value))
    edit = HOOK.response(provider, "PostToolUse", value)
    messages = [edit["hookSpecificOutput"]["additionalContext"]]
    for attempts in (0, 3, 4):
        stopped = HOOK.response(provider, "Stop", value, attempts)
        if attempts < 4:
            assert stopped["decision"] == "block"
            assert "continue" not in stopped
            messages.append(stopped["reason"])
        else:
            assert stopped["continue"] is False
            messages.append(stopped["stopReason"])
    for message in messages:
        assert "original goal and agreed scope" in message
        assert "Before each repair" in message
        assert "ask for explicit scope approval and wait" in message
        assert "Keep unresolved verification blocked" in message
        assert "demo.py:4 F821 undefined name" in message
    assert value == original


@pytest.mark.parametrize("event", ["SessionStart", "PostToolUse", "Stop"])
@pytest.mark.parametrize("repeated", [False, True])
def test_unavailable_hook_keeps_scope_guidance_and_blocking(event, repeated):
    reply = HOOK.unavailable_response(event, "Missing session baseline", repeated)
    if event == "Stop":
        if repeated:
            assert reply["continue"] is False
            message = reply["stopReason"]
        else:
            assert reply["decision"] == "block"
            message = reply["reason"]
    else:
        assert set(reply) == {"hookSpecificOutput"}
        message = reply["hookSpecificOutput"]["additionalContext"]
    assert "ask for explicit scope approval and wait" in message
    assert "Missing session baseline" in message


def test_scope_guidance_does_not_turn_passes_or_explicit_skips_into_failures():
    passed = WRAPPER.result("passed", "lint", ["passed"])
    assert HOOK.response("codex", "Stop", passed) == {}
    edit = HOOK.response("codex", "PostToolUse", passed)
    assert "Task scope:" not in edit["hookSpecificOutput"]["additionalContext"]
    skipped = WRAPPER.result("not_checked", "lint", [], explicitly_skipped=True)
    assert HOOK.response("claude", "Stop", skipped, 4) == {}


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)


def test_session_baseline_excludes_preexisting_and_reconciles_shell(
    tmp_path, monkeypatch
):
    git(tmp_path, "init", "-q")
    (tmp_path / "preexisting.py").write_text("unchanged user work")
    payload = {"session_id": "one"}
    assert HOOK.handle(tmp_path, "codex", "SessionStart", payload) == {}
    assert HOOK.handle(tmp_path, "codex", "Stop", payload) == {}
    (tmp_path / "shell.py").write_text("new shell edit")
    selected = []

    def check(root, paths, **kwargs):
        selected.extend(paths)
        return WRAPPER.result("passed", "fixture", ["passed"], command_count=1)

    monkeypatch.setattr(HOOK.CHECKER, "check_files", check)
    assert HOOK.handle(tmp_path, "codex", "Stop", payload) == {}
    assert selected == ["shell.py"]
    state = json.loads(
        next((tmp_path / ".cache/quality-sessions").glob("*.json")).read_text()
    )
    assert state["history"][-1]["command_count"] == 1
    assert state["history"][-1]["measured_tokens"] is None


def test_sessions_are_isolated_and_repair_success_resets_attempts(
    tmp_path, monkeypatch
):
    git(tmp_path, "init", "-q")
    for session in ["one", "two"]:
        HOOK.handle(tmp_path, "claude", "SessionStart", {"session_id": session})
    (tmp_path / "demo.py").write_text("error")
    status = ["findings"]

    def check(*args, **kwargs):
        return WRAPPER.result(
            status[0], "fixture", ["demo.py:1 error"], diagnostic_signature="same"
        )

    monkeypatch.setattr(HOOK.CHECKER, "check_files", check)
    for _ in range(3):
        reply = HOOK.handle(tmp_path, "claude", "Stop", {"session_id": "one"})
    assert "Reassessment" in reply["reason"]
    states = [
        json.loads(path.read_text())
        for path in (tmp_path / ".cache/quality-sessions").glob("*.json")
    ]
    assert sorted(state["attempts"] for state in states) == [0, 3]
    status[0] = "passed"
    assert HOOK.handle(tmp_path, "claude", "Stop", {"session_id": "one"}) == {}


def test_targeted_verifier_validates_arguments_without_shell(tmp_path):
    scripts = tmp_path / "vibe_scripts/codex"
    scripts.mkdir(parents=True)
    shutil.copy(TEMPLATES / "agent-verify.sh", scripts)
    (scripts / "ai-quality-wrapper.py").write_text(
        "import json,sys; print(json.dumps(sys.argv[1:]))"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_demo.py").write_text("")
    verifier = ["bash", str(scripts / "agent-verify.sh")]
    good = subprocess.run(
        [*verifier, "test", "tests/test_demo.py::test_safe"],
        check=False,
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
    )
    assert good.returncode == 0, good.stderr
    assert "tests/test_demo.py::test_safe" in good.stdout
    for target in [
        "../escape.py",
        "--override-ini",
        "tests/test_demo.py::$(touch injected)",
    ]:
        bad = subprocess.run(
            [*verifier, "test", target], check=False, capture_output=True, text=True
        )
        assert bad.returncode == 2
    assert (
        subprocess.run(
            [*verifier, "lint", "extra"], check=False, capture_output=True
        ).returncode
        == 2
    )
    assert not (tmp_path / "injected").exists()


def test_missing_helper_and_malformed_state_deliver_readable_errors(tmp_path):
    git(tmp_path, "init", "-q")
    script = tmp_path / "quality-hook.py"
    shutil.copy(TEMPLATES / "quality-hook.py", script)
    proc = subprocess.run(
        [sys.executable, str(script), "--provider", "codex", "--event", "Stop"],
        check=False,
        input=json.dumps({"cwd": str(tmp_path), "session_id": "one"}),
        capture_output=True,
        text=True,
    )
    reply = json.loads(proc.stdout)
    assert reply["decision"] == "block"
    assert "required helper files" in reply["reason"]
    assert "Traceback" not in proc.stderr
    for name in ("agent-check-edited.py", "ai-quality-wrapper.py"):
        shutil.copy(TEMPLATES / name, tmp_path / name)
    HOOK.handle(tmp_path, "codex", "SessionStart", {"session_id": "one"})
    next((tmp_path / ".cache/quality-sessions").glob("*.json")).write_text("[]")
    proc = subprocess.run(
        [sys.executable, str(script), "--provider", "codex", "--event", "Stop"],
        check=False,
        input=json.dumps({"cwd": str(tmp_path), "session_id": "one"}),
        capture_output=True,
        text=True,
    )
    assert "Malformed session state" in json.loads(proc.stdout)["reason"]
    assert "Traceback" not in proc.stderr
    repeated = subprocess.run(
        [sys.executable, str(script), "--provider", "codex", "--event", "Stop"],
        check=False,
        input=json.dumps(
            {"cwd": str(tmp_path), "session_id": "one", "stop_hook_active": True}
        ),
        capture_output=True,
        text=True,
    )
    assert json.loads(repeated.stdout)["continue"] is False


def test_detached_child_does_not_hold_timeout_pipe_open(tmp_path):
    source = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',\"import time; from pathlib import Path; time.sleep(.5); Path('detached-orphan').touch()\"],start_new_session=True); print('before timeout',flush=True); time.sleep(10)"
    value = command(tmp_path, source, timeout=0.1)
    assert value["status"] == "tool_failure"
    assert value["duration_seconds"] < 1
    assert "before timeout" in Path(value["log"]).read_text()
    time.sleep(0.6)
    assert not (tmp_path / "detached-orphan").exists()


def test_filtered_pytest_cannot_hide_failures(tmp_path, monkeypatch):
    executable(tmp_path / "bin/rtk", 'echo "Pytest: No tests collected"\n')
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    value = command(
        tmp_path,
        "print('demo.py:4 AssertionError: expected repaired behavior'); raise SystemExit(1)",
        adapter="rtk:pytest",
    )
    assert value["status"] == "findings"
    assert any("demo.py:4 AssertionError" in line for line in value["summary"])
    assert value["command_count"] == 2


def test_native_excerpt_and_empty_test_status(tmp_path):
    (tmp_path / "demo.sh").write_text("echo $unsafe\n")
    report = {
        "comments": [
            {
                "file": "demo.sh",
                "line": 1,
                "column": 6,
                "level": "error",
                "code": 2086,
                "message": "Quote variable",
            }
        ]
    }
    value = command(
        tmp_path,
        f"print({json.dumps(report)!r}); raise SystemExit(1)",
        adapter="native:shellcheck-json1",
    )
    assert any("1 | echo $unsafe" in line for line in value["summary"])
    value = command(
        tmp_path, "print('no tests ran'); raise SystemExit(5)", adapter="rtk:pytest"
    )
    assert value["status"] == "not_checked"


def test_optional_design_detector_and_provider_record_isolation(tmp_path, monkeypatch):
    groups = CHECKER.split_by_ext(["demo.js"])
    monkeypatch.setattr(CHECKER, "have_path", lambda root, name: True)
    monkeypatch.setattr(CHECKER, "local_impeccable_command", lambda root: None)
    assert not any(
        "Impeccable" in message for message in CHECKER.missing_tools(tmp_path, groups)
    )
    (tmp_path / ".agents/skills/impeccable").mkdir(parents=True)
    assert not any(
        "Impeccable" in message for message in CHECKER.missing_tools(tmp_path, groups)
    )
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache/codex-edited-files.txt").write_text("codex.py\n")
    (tmp_path / ".cache/claude-edited-files.txt").write_text("claude.py\n")
    monkeypatch.setattr(
        CHECKER, "__file__", str(tmp_path / "vibe_scripts/codex/agent-check-edited.py")
    )
    assert CHECKER.recorded_edited_files(tmp_path) == ["codex.py"]
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".cache/\n.agents/\n")
    (tmp_path / "new.py").write_text("print('new task edit')\n")
    selected = CHECKER.recorded_edited_files(tmp_path)
    assert {"codex.py", "new.py"} <= set(selected)
    assert "claude.py" not in selected


def test_project_boundaries_cover_recorded_explicit_and_full_selection(
    tmp_path, monkeypatch
):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("ignored/\n*.generated.sh\n")
    (tmp_path / ".git/info/exclude").write_text("local.sh\n")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/.gitignore").write_text("*.sh\n!keep.sh\n")
    selected = [
        "source.sh",
        "space name.sh",
        "line\nbreak.sh",
        "tracked.generated.sh",
        "nested/keep.sh",
    ]
    excluded = ["ignored/file.sh", "local.sh", "nested/drop.sh", ".unlazy/check.sh"]
    for name in selected + excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("echo untouched\n")
    git(tmp_path, "add", "-f", "tracked.generated.sh")
    (tmp_path / "alias.sh").symlink_to("ignored/file.sh")
    excluded.append("alias.sh")
    assert CHECKER.existing_project_files(tmp_path, selected + excluded) == sorted(
        selected
    )
    monkeypatch.setattr(
        CHECKER, "__file__", str(tmp_path / "vibe_scripts/codex/agent-check-edited.py")
    )
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache/codex-edited-files.txt").write_text("\n".join(excluded))
    assert not set(excluded) & set(
        CHECKER.existing_project_files(
            tmp_path, CHECKER.recorded_edited_files(tmp_path)
        )
    )
    listed = subprocess.run(
        [
            sys.executable,
            str(TEMPLATES / "agent-check-edited.py"),
            "--list-files",
            ".sh",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.split(b"\0")[:-1] == [
        os.fsencode("./" + name) for name in sorted(selected)
    ]
    assert all((tmp_path / name).read_text() == "echo untouched\n" for name in excluded)


def test_hook_drops_ignored_cached_paths_and_deletions(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    payload = {"session_id": "boundaries"}
    HOOK.handle(tmp_path, "codex", "SessionStart", payload)
    state_path = next((tmp_path / ".cache/quality-sessions").glob("*.json"))
    state = json.loads(state_path.read_text())
    state["paths"] = ["ignored/deleted.py", ".unlazy/GATES.md"]
    state_path.write_text(json.dumps(state))
    monkeypatch.setattr(
        HOOK.CHECKER,
        "check_files",
        lambda *a, **k: pytest.fail("Ignored paths reached checks"),
    )
    assert HOOK.handle(tmp_path, "codex", "Stop", payload) == {}
    assert json.loads(state_path.read_text())["paths"] == []


def test_ignore_lookup_failure_does_not_run_checks(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    (tmp_path / "source.py").write_text("print('source')\n")
    original = CHECKER.subprocess.run

    def run(args, **kwargs):
        if args[:2] == ["git", "check-ignore"]:
            return subprocess.CompletedProcess(args, 128, b"", b"lookup failed")
        return original(args, **kwargs)

    monkeypatch.setattr(CHECKER.subprocess, "run", run)
    result = CHECKER.check_files(tmp_path, ["source.py"])
    assert result["status"] == "tool_failure"
    assert result["command_count"] == 0
    assert "lookup failed" in result["summary"][0]


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize(
    "command,flag",
    [
        ("npx biome check --write --max-diagnostics=none", "--write"),
        ("markdownlint-cli2 --fix", "--fix"),
    ],
)
def test_generated_formatter_retains_post_fix_verification(
    tmp_path, platform, command, flag
):
    source = (TEMPLATES.parents[1] / "CodexHelper" / platform / "part2.sh").read_text()
    functions = []
    for name in ("selected_files_command", "fixer_verify_cmd"):
        start = source.index(name + "() {")
        functions.append(source[start : source.index("\n}", start) + 2])
    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(functions)
            + '\nfixer_verify_cmd "$(selected_files_command .md "$1")"',
            "fixture",
            command,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    verified = shlex.split(result.stdout)[-1]
    assert flag not in shlex.split(verified)
    assert "--list-files .md" in verified


def test_concurrent_lock_contention_is_bounded(tmp_path):
    with (
        (tmp_path / "lock").open("a") as first,
        (tmp_path / "lock").open("a") as second,
    ):
        HOOK.acquire_lock(first)
        started = time.monotonic()
        with pytest.raises(OSError, match="Another hook"):
            HOOK.acquire_lock(second, timeout=0.05)
        assert time.monotonic() - started < 0.5


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_deleted_files_do_not_run_tests_but_explicit_failures_remain(
    tmp_path, provider
):
    git(tmp_path, "init", "-q")
    (tmp_path / "deleted.py").write_text("old code")
    makefile = tmp_path / "vibe_scripts" / provider / "Makefile"
    makefile.parent.mkdir(parents=True)
    makefile.write_text("test-ai:\n\t@echo explicit-test-marker\n\t@false\n")
    payload = {"session_id": "deletion"}
    HOOK.handle(tmp_path, provider, "SessionStart", payload)
    (tmp_path / "deleted.py").unlink()
    assert HOOK.handle(tmp_path, provider, "Stop", payload) == {}
    result = subprocess.run(
        ["make", "-f", str(makefile), "test-ai"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "explicit-test-marker" in result.stdout


def test_restricted_child_discovery_reports_cleanup_limit(tmp_path, monkeypatch):
    original = WRAPPER.subprocess.run

    def run(args, **kwargs):
        if args[:1] == ["ps"]:
            return subprocess.CompletedProcess(args, 1, "", "operation not permitted")
        return original(args, **kwargs)

    monkeypatch.setattr(WRAPPER.subprocess, "run", run)
    value = command(tmp_path, "import time; time.sleep(2)", timeout=0.05)
    assert value["status"] == "tool_failure"
    assert any("Detached children may remain" in line for line in value["summary"])


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_fresh_hook_then_generated_edited_target_uses_git_fallback(
    tmp_path, provider, platform
):
    root = TEMPLATES.parents[1]
    repo = tmp_path / "project"
    home = tmp_path / "home"
    binaries = tmp_path / "bin"
    for directory in (repo, home, binaries):
        directory.mkdir()
    executable(binaries / "ruff", 'echo "ruff fixture checked $*"\n')
    executable(
        binaries / "shellcheck",
        'printf "%s\\n" "$@" >> "$BOUNDARY_LOG"\nprintf \'{"comments":[]}\\n\'\n',
    )
    executable(
        binaries / "markdownlint-cli2", 'printf "%s\\n" "$@" >> "$BOUNDARY_LOG"\n'
    )
    executable(
        binaries / "uname", "echo Darwin\n" if platform == "MacOS" else "echo Linux\n"
    )
    for name in ("node", "npm", "npx", "code-review-graph"):
        executable(binaries / name, "exit 0\n")
    env = os.environ | {
        "HOME": str(home),
        "PATH": f"{binaries}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "BOUNDARY_LOG": str(tmp_path / "checked-paths.txt"),
    }
    helper = "CodexHelper" if provider == "codex" else "ClaudeHelper"
    flags = (
        [
            "--yes",
            "--no-adhd",
            "--no-rtk-hook",
            "--no-crg-build",
            "--no-apply-codex-config",
        ]
        if provider == "codex"
        else [
            "--no-rtk",
            "--no-graphify",
            "--no-ponytail",
            "--no-crg",
            "--no-context7",
            "--no-chrome-mcp",
            "--no-system-packages",
        ]
    )
    git(repo, "init", "-q")
    (repo / ".git/info/exclude").write_text("private/\n")
    for name in [
        "source.sh",
        "README.md",
        "private/ignored.sh",
        "private/ignored.md",
        ".unlazy/GATES.md",
    ]:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture content\n")
    (repo / ".agents/skills/unlazy").mkdir(parents=True)
    (repo / ".agents/skills/unlazy/SKILL.md").write_text("# Fixture Unlazy\n")
    installed = subprocess.run(
        [
            "bash",
            str(root / helper / platform / "part1.sh"),
            "--repo-only",
            "--no-humanizer",
            *flags,
        ],
        check=False,
        cwd=repo,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    quality_flags = ["--yes", "--no-install", "--no-fix"] if provider == "codex" else []
    generated = subprocess.run(
        ["bash", str(root / helper / platform / "part2.sh"), "--wire", *quality_flags],
        check=False,
        cwd=repo,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    if provider == "codex":
        assert ".unlazy/" in (repo / ".gitignore").read_text().splitlines()
        assert (
            "--extend-exclude" not in (repo / "vibe_scripts/codex/Makefile").read_text()
        )
    full_lint = subprocess.run(
        ["make", "-f", f"vibe_scripts/{provider}/Makefile", "lint-ai"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert full_lint.returncode == 0, full_lint.stdout + full_lint.stderr
    checked_paths = Path(env["BOUNDARY_LOG"]).read_text().splitlines()
    assert "./source.sh" in checked_paths and "./README.md" in checked_paths
    assert not any("private/" in name or ".unlazy/" in name for name in checked_paths)
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture baseline",
    )
    scripts = repo / "vibe_scripts" / provider

    def hook(event, extra=None):
        return subprocess.run(
            [
                sys.executable,
                str(scripts / "quality-hook.py"),
                "--provider",
                provider,
                "--event",
                event,
            ],
            check=False,
            cwd=repo,
            env=env,
            input=json.dumps(
                {"session_id": "fresh", "cwd": str(repo), **(extra or {})}
            ),
            capture_output=True,
            text=True,
            timeout=15,
        )

    assert hook("SessionStart").returncode == 0
    (repo / "demo.py").write_text("print('task edit')\n")
    edit = hook("PostToolUse", {"tool_input": {"file_path": "demo.py"}})
    assert "passed" in edit.stdout
    assert not (repo / f".cache/{provider}-edited-files.txt").exists()
    checked = subprocess.run(
        ["make", "-f", str(scripts / "Makefile"), "edited-ai"],
        check=False,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "lint-ruff-edited: passed" in checked.stdout


def test_infrastructure_stop_errors_end_repeated_continuation(tmp_path):
    payload = {"stop_hook_active": True}
    missing_session = HOOK.handle(tmp_path, "codex", "Stop", payload)
    assert missing_session["continue"] is False
    assert "blocked/unverified" in missing_session["stopReason"]
    script = tmp_path / "quality-hook.py"
    shutil.copy(TEMPLATES / "quality-hook.py", script)
    missing_helper = subprocess.run(
        [sys.executable, str(script), "--provider", "claude", "--event", "Stop"],
        check=False,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert json.loads(missing_helper.stdout)["continue"] is False
    assert "Traceback" not in missing_helper.stderr
    normal = HOOK.response(
        "claude", "Stop", WRAPPER.result("findings", "lint", ["fix code"])
    )
    assert normal["decision"] == "block" and "continue" not in normal
