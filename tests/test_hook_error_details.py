"""Generated hooks keep diagnostic detail on disk, not in agent summaries."""

import errno
import importlib.util
import json
from pathlib import Path

import pytest
from test_project_memory import MEMORY

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("provider,count", [("CodexHelper", 2), ("ClaudeHelper", 3)])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_generated_hook_error_details(tmp_path, monkeypatch, provider, count, platform):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    script = ROOT / provider / platform / "part1.sh"
    lines = script.read_text().splitlines()
    starts = [
        i for i, line in enumerate(lines) if line.startswith("def log_hook_error(")
    ]
    assert len(starts) == count
    raw = '{"password":"DO_NOT_LOG_PAYLOAD", invalid}'
    try:
        json.loads(raw)
    except json.JSONDecodeError as error:
        malformed = error
    for start in starts:
        stop = start + 1
        while stop < len(lines) and (not lines[stop] or lines[stop][0].isspace()):
            stop += 1
        module_path = tmp_path / f".codex/hooks/logger_{start}.py"
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(
            "import json, os, sys\nfrom datetime import UTC, datetime, timezone\nfrom pathlib import Path\n"
            + "\n".join(lines[start:stop])
        )
        spec = importlib.util.spec_from_file_location(f"logger_{start}", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        log = module.log_hook_error
        log("json-decode-failed", raw=raw, error=malformed)
        log(
            "stdin-read-failed",
            error=OSError(errno.EACCES, "Permission denied", "input.json"),
        )
        log(
            "stdin-read-failed",
            error=RuntimeError("password=DO_NOT_LOG_SECRET Bearer PRIVATE_TOKEN"),
        )
    path = tmp_path / ".cache/hook-errors.log"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == count * 3
    for index in range(0, len(records), 3):
        malformed_record, io_record, redacted = records[index : index + 3]
        assert malformed_record["error"] == str(malformed)
        assert malformed_record["lineno"] == malformed.lineno
        assert malformed_record["colno"] == malformed.colno
        assert malformed_record["pos"] == malformed.pos
        assert malformed_record["input_bytes"] == len(raw.encode())
        assert (
            io_record["errno"] == errno.EACCES and io_record["filename"] == "input.json"
        )
        assert "Permission denied" in io_record["error"]
        assert redacted["error"] == "password=[REDACTED] Bearer [REDACTED]"
    assert (
        "DO_NOT_LOG" not in path.read_text() and "PRIVATE_TOKEN" not in path.read_text()
    )
    report = MEMORY.hook_errors_report(tmp_path)
    assert "Permission denied" not in report and malformed.msg not in report
    assert "exception recorded" in report and len(report) < 2048
