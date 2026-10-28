"""Session ownership, queued saves and real filesystem observer regression checks."""

import json
import os
import signal
import subprocess
import time

import pytest
from test_project_memory import INSTALL, MEMORY, load

WATCH = load("project-memory-watch")


def test_debounce_and_changes_during_refresh(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(WATCH.time, "monotonic", lambda: clock[0])
    pending = WATCH.Pending()
    assert pending.take() == ["startup"]
    pending.add("first.py")
    clock[0] += 2
    pending.add("second.py")
    assert not pending.take()
    clock[0] += 3
    assert pending.take() == ["first.py", "second.py"]
    pending.add("during-refresh.py")
    assert not pending.take()
    clock[0] += 3
    assert pending.take() == ["during-refresh.py"]


def test_pid_reuse_and_optout(tmp_path, monkeypatch):
    monkeypatch.setattr(WATCH, "process", lambda pid: {"pid": pid, "started": "new"})
    assert not WATCH.alive({"pid": 12, "started": "old"})
    assert WATCH.alive({"pid": 12, "started": "new"})
    INSTALL.install(tmp_path, "codex", hooks=False)
    assert WATCH.session(
        tmp_path, "codex", {"hook_event_name": "SessionStart", "session_id": "disabled"}
    ) == {"status": "disabled"}


def wait_for(check, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.2)
    pytest.fail(
        "Timed out waiting for watcher; inspect fixture .cache/project-memory/watcher.log"
    )


def test_live_shared_watcher_stop_restart_and_save_burst(tmp_path):
    root = tmp_path.resolve()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    INSTALL.install(root, "codex", hooks=True)
    (root / "module.py").write_text("def initial(): pass\n")
    events = lambda name, event: {"hook_event_name": event, "session_id": name}
    WATCH.session(root, "codex", events("one", "SessionStart"))
    try:
        first = wait_for(lambda: WATCH.status(root).get("last_successful_refresh"))
        pid = WATCH.status(root)["process"]["pid"]
        WATCH.session(root, "codex", events("two", "SessionStart"))
        assert WATCH.status(root)["process"]["pid"] == pid
        stop = subprocess.run(
            ["python3", str(MEMORY.__file__), "hook", "--root", str(root)],
            input=json.dumps(events("one", "Stop")),
            capture_output=True,
            text=True,
            check=False,
        )
        assert stop.returncode == 0 and json.loads(stop.stdout) == {}
        assert WATCH.status(root)["healthy"]
        for value in range(3):
            (root / "module.py").write_text(f"def changed(): return {value}\n")
            time.sleep(0.15)
        wait_for(lambda: WATCH.status(root).get("last_successful_refresh", 0) > first)
        state = json.loads((root / ".cache/project-memory/state.json").read_text())
        refreshed = state["crg"]["updated_at"]
        time.sleep(4)
        assert (
            json.loads((root / ".cache/project-memory/state.json").read_text())["crg"][
                "updated_at"
            ]
            == refreshed
        )
        WATCH.session(root, "codex", events("one", "SessionEnd"))
        assert WATCH.status(root)["healthy"]
        os.kill(pid, signal.SIGTERM)
        wait_for(lambda: not WATCH.status(root)["healthy"])
        (root / "module.py").write_text("def missed_while_down(): pass\n")
        WATCH.session(root, "codex", events("two", "SessionStart"))
        wait_for(lambda: WATCH.status(root).get("process", {}).get("pid") != pid)
        wait_for(lambda: WATCH.status(root).get("last_successful_refresh"))
        graph = MEMORY.backend_path(root, "graphify").read_text()
        assert "missed_while_down" in graph
    finally:
        WATCH.session(root, "codex", events("one", "SessionEnd"))
        WATCH.session(root, "codex", events("two", "SessionEnd"))
        wait_for(lambda: WATCH.status(root).get("phase") == "stopped")


def test_abandoned_session_process_is_released(tmp_path):
    child = subprocess.Popen(["sleep", "10"])
    try:
        info = WATCH.process(child.pid)
        assert info
        WATCH.leases(
            tmp_path, ("codex:abandoned", {"provider": "codex", "process": info})
        )
        assert WATCH.leases(tmp_path)
        child.terminate()
        child.wait(timeout=5)
        assert WATCH.leases(tmp_path) == {}
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
