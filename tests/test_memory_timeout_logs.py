"""Retain useful timeout evidence without changing memory hook behavior."""

import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_project_memory import MEMORY, args


def logs(root):
    return list((root / ".cache/project-memory").glob("timeout-*.json"))


def test_process_timeout_keeps_output_and_redacts_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "private-environment-value")
    command = [
        sys.executable,
        "-c",
        (
            "import os,sys,time; "
            "print('progress before timeout', flush=True); "
            "print(os.environ['TEST_API_TOKEN'], flush=True); "
            "print('password=private-password Authorization: Bearer private-bearer', flush=True); "
            "sys.stderr.buffer.write(b'last diagnostic\\xff\\n'); sys.stderr.flush(); "
            "time.sleep(30)"
        ),
    ]
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        MEMORY.run(tmp_path, command, timeout=0.3)
    saved = logs(tmp_path)
    assert len(saved) == 1
    text = saved[0].read_text()
    record = json.loads(text)
    assert "progress before timeout" in record["details"]["stdout"]
    assert "last diagnostic" in record["details"]["stderr"]
    assert record["details"]["budget_seconds"] == 0.3
    assert record["details"]["elapsed_seconds"] >= 0.3
    assert record["details"]["command"][0] == sys.executable
    assert record["time_utc"] and "TimeoutExpired" in record["traceback"]
    for secret in ("private-environment-value", "private-password", "private-bearer"):
        assert secret not in text
    assert saved[0].stat().st_mode & 0o777 == 0o600
    message = MEMORY.timeout_message(tmp_path, caught.value)
    assert saved[0].name in message and len(logs(tmp_path)) == 1


def test_scan_timeout_refresh_reports_path_and_preserves_state(
    tmp_path, monkeypatch, capsys
):
    cache = tmp_path / ".cache/project-memory"
    cache.mkdir(parents=True)
    state = cache / "state.json"
    state.write_text('{"previous": "untouched"}')
    source = tmp_path / "large.txt"
    source.write_bytes(b"x" * 131072)

    def scan(*_args):
        raise MEMORY.MemoryTimeout(
            "scan deadline exceeded",
            path=str(source),
            operation="source scan",
            budget_seconds=8,
        )

    monkeypatch.setattr(MEMORY, "handle_locked", scan)
    monkeypatch.setattr(MEMORY, "run", lambda *_args: str(tmp_path))
    monkeypatch.setattr(
        sys, "argv", ["project-memory.py", "refresh", "--root", str(tmp_path)]
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"hook_event_name": "Stop"}'))
    assert MEMORY.main() == 1
    output = capsys.readouterr().err
    saved = logs(tmp_path)
    assert len(saved) == 1 and saved[0].name in output
    record = json.loads(saved[0].read_text())
    assert record["details"]["path"] == str(source)
    assert record["details"]["operation"] == "source scan"
    assert record["details"]["budget_seconds"] == 8
    assert state.read_text() == '{"previous": "untouched"}'


@pytest.mark.parametrize("operation", ["guidance traversal", "source scan"])
def test_snapshot_timeout_records_current_path(tmp_path, monkeypatch, operation):
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent/rules.md").write_text("rules")
    clock = iter([0, 9])
    monkeypatch.setattr(MEMORY.time, "monotonic", lambda: next(clock, 9))
    monkeypatch.setattr(MEMORY, "run", lambda *_args: "source.py\0")
    if operation == "source scan":
        (tmp_path / "agent/rules.md").unlink()
    with pytest.raises(MEMORY.MemoryTimeout) as caught:
        MEMORY.snapshot(tmp_path)
    message = MEMORY.timeout_message(tmp_path, caught.value)
    record = json.loads(logs(tmp_path)[0].read_text())
    assert record["details"]["operation"] == operation
    assert record["details"]["path"]
    assert record["details"]["budget_seconds"] == 8
    assert "Timeout log:" in message


def test_total_budget_timeout_and_success(tmp_path, monkeypatch):
    assert MEMORY.run(tmp_path, [sys.executable, "-c", "print('ok')"]).strip() == "ok"
    assert not logs(tmp_path)
    monkeypatch.setattr(MEMORY, "DEADLINE", time.monotonic() - 1)
    with pytest.raises(RuntimeError, match="budget exhausted") as caught:
        MEMORY.run(tmp_path, ["git", "status"])
    record = json.loads(logs(tmp_path)[0].read_text())
    assert record["details"]["operation"] == "total budget"
    assert record["details"]["command"] == ["git", "status"]
    assert "Timeout log:" in MEMORY.timeout_message(tmp_path, caught.value)


def test_relative_root_and_quoted_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    error = MEMORY.MemoryTimeout(
        "memory scan timeout",
        output='password="private phrase" api_key=\'another secret\' {"token": "json secret"}',
    )
    message = MEMORY.timeout_message(Path("."), error)
    assert logs(tmp_path)[0].name in message
    text = logs(tmp_path)[0].read_text()
    json.loads(text)
    for secret in ("private phrase", "another secret", "json secret"):
        assert secret not in text
    assert "phrase" not in text


@pytest.mark.parametrize("failure", ["unwritable", "symlink"])
def test_log_failure_preserves_original_timeout(tmp_path, monkeypatch, failure):
    if failure == "symlink":
        outside = tmp_path.parent / (tmp_path.name + "-outside")
        outside.mkdir()
        (tmp_path / ".cache").symlink_to(outside, target_is_directory=True)
    else:

        def denied(**_kwargs):
            raise PermissionError("read-only cache")

        monkeypatch.setattr(MEMORY.tempfile, "mkstemp", denied)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        MEMORY.run(
            tmp_path,
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.05,
        )
    message = MEMORY.timeout_message(tmp_path, caught.value)
    assert "Timeout log: unavailable" in message and "timed out" in message
    if failure == "symlink":
        assert not list(outside.iterdir())


def test_handle_total_budget_logs_once(tmp_path, monkeypatch):
    def expire(*_args):
        monkeypatch.setattr(MEMORY, "DEADLINE", time.monotonic() - 1)
        MEMORY.remaining(1)

    monkeypatch.setattr(MEMORY, "handle_locked", expire)
    with pytest.raises(MEMORY.MemoryTimeout) as caught:
        MEMORY.handle(tmp_path, args("hook"))
    assert len(logs(tmp_path)) == 1
    assert logs(tmp_path)[0].name in MEMORY.timeout_message(tmp_path, caught.value)


def test_backend_and_context_errors_keep_log_reference(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "source.py").write_text("pass\n")
    real_run = MEMORY.run

    def timeout(root, command, limit=30):
        if command[0] == "git":
            return real_run(root, command, limit)
        raise subprocess.TimeoutExpired(["backend", "x" * 600], 5)

    monkeypatch.setattr(MEMORY, "run", timeout)
    monkeypatch.setattr(
        MEMORY,
        "index_backend",
        lambda *_args: timeout(tmp_path, ["backend", "x" * 600]),
    )
    value = MEMORY.handle(tmp_path, args("refresh"))
    status = value["freshness"]
    assert status["crg"]["status"] == "pending"
    assert logs(tmp_path)[0].name in status["crg"]["error"]
    assert "crg" not in json.loads(
        (tmp_path / ".cache/project-memory/state.json").read_text()
    )
    status["crg"]["status"] = "current"
    value = MEMORY.context(tmp_path, "timeout", ["source.py"], status)
    for item in value["code_context"]:
        assert "Timeout log: .cache/project-memory/timeout-" in item["error"]
    assert len(logs(tmp_path)) == 4
