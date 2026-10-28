"""Session hooks expose the provider protocol, not internal watcher state."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_project_memory import MANAGED, MEMORY


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
@pytest.mark.parametrize(
    "outcome", ["watching", "released", "disabled", "unavailable", "exception"]
)
def test_session_hook_output(tmp_path, provider, event, outcome):
    helper = tmp_path / "project-memory.py"
    shutil.copy2(MEMORY.__file__, helper)
    watcher = tmp_path / "project-memory-watch.py"
    result = {"status": outcome, "log": "watcher.log", "error": "watcher unavailable"}
    watcher.write_text(
        "import json\n"
        "def session(root, provider, payload):\n"
        "    (root / 'event.json').write_text(json.dumps([provider, payload]))\n"
        + (
            "    raise OSError('watcher failed')\n"
            if outcome == "exception"
            else f"    return {result!r}\n"
        )
    )
    payload = {"hook_event_name": event, "session_id": "protocol-check"}
    completed = subprocess.run(
        [
            sys.executable,
            str(helper),
            "hook",
            "--provider",
            provider,
            "--root",
            str(tmp_path),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads((tmp_path / "event.json").read_text()) == [provider, payload]
    output = json.loads(completed.stdout)
    if outcome in {"unavailable", "exception"}:
        assert set(output) == {"systemMessage"}
        expected = (
            "watcher unavailable" if outcome == "unavailable" else "watcher failed"
        )
        assert expected in output["systemMessage"]
    else:
        assert output == {}


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_memory_hook_refresh_is_scoped_and_preserves_customization(tmp_path, provider):
    helper = tmp_path / f"vibe_scripts/{provider}/project-memory.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# original managed helper\n")
    helper.chmod(0o755)
    policy = helper.with_name("memory-policy.json")
    policy.write_text('{"watch": false, "custom": "keep"}\n')
    receipt = tmp_path / f".cache/vibehelper-install/{provider}.json"
    receipt.parent.mkdir(parents=True)
    name = str(helper.relative_to(tmp_path))
    owned = {name: MANAGED.digest(helper), "unrelated": "keep"}
    receipt.write_text(json.dumps(owned))
    old_helper, old_receipt = helper.read_bytes(), receipt.read_bytes()
    old_policy = policy.read_bytes()
    MANAGED.install_usage_tracker(tmp_path, provider, dry_run=True, memory_hook=True)
    assert helper.read_bytes() == old_helper and receipt.read_bytes() == old_receipt
    command = [
        sys.executable,
        MANAGED.__file__,
        "memory-hook-refresh",
        "--provider",
        provider,
        "--root",
        str(tmp_path),
    ]
    for _ in range(2):
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
        assert completed.returncode == 0, completed.stderr
        assert helper.read_bytes() == Path(MEMORY.__file__).read_bytes()
        assert helper.stat().st_mode & 0o777 == 0o755
        assert json.loads(receipt.read_text()) == owned | {name: MANAGED.digest(helper)}
        assert policy.read_bytes() == old_policy
    helper.write_text("# user customization\n")
    old_receipt = receipt.read_bytes()
    with pytest.raises(ValueError, match="modified or symlinked"):
        MANAGED.install_usage_tracker(tmp_path, provider, memory_hook=True)
    assert helper.read_text() == "# user customization\n"
    assert receipt.read_bytes() == old_receipt and policy.read_bytes() == old_policy
