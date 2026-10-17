"""Context7 runtime authentication checks and installer repair decisions."""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_context7", ROOT / "CodexHelper/templates/check-context7.py"
)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


@pytest.mark.parametrize(
    "state,expected",
    [
        ("healthy", 0),
        ("expired", 10),
        ("network", 1),
        ("missing", 1),
        ("paginated", 0),
        ("empty", 1),
        ("malformed", 1),
        ("partial", 1),
        ("timeout", 1),
        ("rpc_error", 1),
        ("child", 1),
    ],
)
def test_runtime_probe(tmp_path, monkeypatch, state, expected):
    fake = tmp_path / "codex"
    fake.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, sys, time, subprocess
from pathlib import Path
state = os.environ["PROBE_STATE"]
if sys.argv[1:] == ["mcp", "list", "--json"]:
    print(json.dumps([{"name": "context7"}, {"name": "unrelated"}, {"name": "chrome-devtools"}]))
    sys.exit()
assert sys.argv[1:] == [
    "app-server", "--stdio",
    "-c", "mcp_servers.unrelated.enabled=false",
    "-c", "mcp_servers.chrome-devtools.enabled=false",
]
Path(os.environ["PROBE_PID"]).write_text(str(os.getpid()))
if state == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path(os.environ["PROBE_PID"] + ".child").write_text(str(child.pid))
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    if request["id"] == 1:
        print(json.dumps({"id": 1, "result": {}}), flush=True)
        continue
    if state in ("timeout", "partial", "child"):
        if state == "partial":
            sys.stdout.write('{"id":'); sys.stdout.flush()
        time.sleep(30)
    if state == "malformed":
        print("not json", flush=True); continue
    if state == "rpc_error":
        print(json.dumps({"id": 2, "error": {"message": "private"}}), flush=True)
        continue
    server = {"name": "context7", "authStatus": "loggedIn", "tools": {
        "resolve-library-id": {}, "query-docs": {}}}
    if state in ("expired", "network", "empty"):
        server["authStatus"] = "notLoggedIn"
        server["tools"] = {}
        server["toolsError"] = {
            "expired": "MCP startup failed: failed to refresh OAuth tokens for server context7: OAuth authorization required",
            "network": "Network request failed: private credential must not be printed",
            "empty": None,
        }[state]
    result = {"data": [server]}
    if state == "missing":
        result = {"data": []}
    if state == "paginated" and not request["params"].get("cursor"):
        result = {"data": [{"name": "other"}], "nextCursor": "page2"}
    print(json.dumps({"method": "notification"}), flush=True)
    print(json.dumps({"id": 2, "result": result}), flush=True)
"""
    )
    fake.chmod(0o755)
    pid = tmp_path / "pid"
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PROBE_STATE", state)
    monkeypatch.setenv("PROBE_PID", str(pid))
    assert PROBE.check_access(timeout=0.5) == expected
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)
    if state == "child":
        import time

        child_pid = int(pid.with_suffix(".child").read_text())
        for _ in range(100):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            # Linux may retain an exited orphan as a zombie until init reaps it.
            stat = Path(f"/proc/{child_pid}/stat")
            assert stat.exists() and stat.read_text().split()[2] == "Z"


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize(
    "first,second,login,dry,expected_calls,expected_code",
    [
        (0, 0, 0, 0, ["probe"], 0),
        (10, 0, 0, 0, ["probe", "login", "probe"], 0),
        (1, 0, 0, 0, ["probe"], 1),
        (10, 1, 0, 0, ["probe", "login", "probe"], 1),
        (10, 10, 0, 0, ["probe", "login", "probe"], 1),
        (10, 0, 1, 0, ["probe", "login"], 1),
        (10, 0, 0, 1, [], 0),
    ],
)
def test_installer_check(
    tmp_path, platform, first, second, login, dry, expected_calls, expected_code
):
    source = (ROOT / "CodexHelper" / platform / "part1.sh").read_text()
    functions = []
    for name in ("context7_codex_login", "context7_codex_check"):
        functions.append(
            re.search(
                r"^" + name + r"\(\) \{.*?^\}", source, flags=re.MULTILINE | re.DOTALL
            ).group()
        )
    harness = (
        "\n".join(functions)
        + r"""
have() { return 0; }
log() { echo "$*"; }
warn() { echo "$*"; }
record_install_ok() { echo "OK: $*"; }
record_install_skipped() { echo "SKIP: $*"; }
record_install_failed() { echo "FAIL: $*"; }
run() {
    [[ "$DRY_RUN" == 1 ]] && return 0
    shift
    "$@"
}
codex() {
    [[ "$*" == 'mcp login context7' ]] || exit 99
    echo login >> "$CALLS"
    return "$LOGIN"
}
python3() {
    [[ "$1" == "$HELPER_ROOT/templates/check-context7.py" ]] || exit 98
    echo probe >> "$CALLS"
    count=$((count + 1))
    if [[ "$count" == 1 ]]; then return "$FIRST"; fi
    return "$SECOND"
}
count=0
context7_codex_check
"""
    )
    calls = tmp_path / "calls"
    calls.write_text("")
    result = subprocess.run(
        ["bash", "-eu", "-c", harness],
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "FIRST": str(first),
            "SECOND": str(second),
            "LOGIN": str(login),
            "DRY_RUN": str(dry),
            "CALLS": str(calls),
            "HELPER_ROOT": str(ROOT / "CodexHelper"),
        },
        check=False,
    )
    assert result.returncode == expected_code, result.stdout + result.stderr
    assert calls.read_text().splitlines() == expected_calls
    if expected_code or dry:
        assert "access verified" not in result.stdout
    # Both existing-registration and new-registration paths use the same check.
    setup = source.split("# Registration alone", 1)[1].split("setup_chrome_mcp", 1)[0]
    assert setup.count("context7_codex_check || true") == 3
    assert "context7_codex_login" not in setup
