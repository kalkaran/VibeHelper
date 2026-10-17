"""The access probe stays bounded when process signaling is denied."""

import importlib.util
import json
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, call

import pytest

SPEC = importlib.util.spec_from_file_location(
    "context7_cleanup_probe",
    Path(__file__).resolve().parents[1] / "CodexHelper/templates/check-context7.py",
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


@pytest.mark.parametrize("expected", [0, 1, 10])
@pytest.mark.parametrize(
    "failure", ["group_denied", "gone", "direct_denied", "force_kill", "stuck"]
)
def test_cleanup(monkeypatch, capsys, expected, failure):
    process = Mock(pid=12345)
    process.poll.return_value = None if failure == "stuck" else 0
    timeout = subprocess.TimeoutExpired("codex", 2)
    if failure == "stuck":
        process.wait.side_effect = timeout
    elif failure == "force_kill":
        process.wait.side_effect = [timeout, 0]
    if failure in ("direct_denied", "stuck"):
        process.send_signal.side_effect = PermissionError("private upstream detail")
    killpg = Mock(
        side_effect=ProcessLookupError if failure == "gone" else PermissionError
    )
    monkeypatch.setattr(PROBE.os, "killpg", killpg)
    monkeypatch.setattr(PROBE.subprocess, "run", Mock(return_value=Mock(stdout=b"[]")))
    monkeypatch.setattr(PROBE.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(PROBE.selectors, "DefaultSelector", MagicMock())
    server = {"name": "context7", "tools": {}}
    if expected == 0:
        server["tools"] = {"resolve-library-id": {}, "query-docs": {}}
    elif expected == 10:
        server["toolsError"] = "OAuth authorization required"
    response = json.dumps({"id": 2, "result": {"data": [server]}}).encode() + b"\n"
    monkeypatch.setattr(PROBE.os, "read", Mock(return_value=response))

    assert PROBE.check_access() == expected
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        "Context7 probe cleanup could not stop the Codex process.\n"
        if failure == "stuck"
        else ""
    )

    assert killpg.call_args_list == [
        call(process.pid, signal.SIGTERM),
        call(process.pid, signal.SIGKILL),
    ]
    assert process.send_signal.call_args_list == (
        [] if failure == "gone" else [call(signal.SIGTERM), call(signal.SIGKILL)]
    )
    assert process.wait.call_args_list == [call(timeout=2), call(timeout=2)]
    process.stdin.close.assert_called_once_with()
    process.stdout.close.assert_called_once_with()
