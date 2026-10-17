"""Cancellation and forced restart use temporary repositories and stub phases."""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "templates/vibe_scripts/managed-install.py"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def helper(root, operation, run_id, *flags):
    return subprocess.run(
        [
            sys.executable,
            str(HELPER),
            operation,
            "--provider",
            "codex",
            "--run-id",
            str(run_id),
            *flags,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def stale_transaction(root, provider="codex"):
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=10)
    directory = root / ".cache/vibehelper-install"
    write(directory / f"{provider}.lock/run-id", str(process.pid))
    before = write(directory / f"{provider}.{process.pid}.before.json", "{}")
    return before


def installer(root, provider, phase1="exit 0", phase2="exit 0"):
    base = root / provider
    base.mkdir()
    shutil.copy2(ROOT / provider / "install.sh", base / "install.sh")
    write(root / "templates/vibe_scripts/managed-install.py", HELPER.read_text())
    for phase, body in [(1, phase1), (2, phase2)]:
        write(
            base / f"WSL/part{phase}.sh",
            f"#!/bin/bash\nprintf ran > phase{phase}-ran\n{body}\n",
        )
    fake = write(root / "bin/uname", "#!/bin/sh\nprintf Linux\n")
    fake.chmod(0o755)
    env = dict(
        os.environ,
        PATH=str(fake.parent) + os.pathsep + os.environ["PATH"],
        WSL_DISTRO_NAME="mock",
    )
    return ["bash", str(base / "install.sh"), "--force"], env


def test_force_recovers_dead_owner_preserving_files(tmp_path):
    before = stale_transaction(tmp_path)
    custom = write(tmp_path / "AGENTS.md", "User instructions\n")
    result = helper(tmp_path, "begin", os.getpid())
    assert result.returncode != 0
    assert before.exists()
    result = helper(tmp_path, "begin", os.getpid(), "--force")
    assert result.returncode == 0, result.stderr
    assert "cleared abandoned" in result.stdout
    assert not before.exists()
    assert custom.read_text() == "User instructions\n"
    assert helper(tmp_path, "abort", os.getpid()).returncode == 0


@pytest.mark.parametrize("owner", ["live", "unknown", "0", "999999999999999999999"])
def test_force_preserves_live_or_unknown_owner(tmp_path, owner):
    owner = str(os.getpid()) if owner == "live" else owner
    lock = write(tmp_path / ".cache/vibehelper-install/codex.lock/run-id", owner)
    before = write(lock.parent.parent / f"codex.{owner}.before.json", "{}")
    result = helper(tmp_path, "begin", "new", "--force")
    assert result.returncode != 0
    assert lock.read_text() == owner
    assert before.read_text() == "{}"


@pytest.mark.parametrize("target", ["owner", "receipt", "guard"])
def test_force_preserves_symlinked_metadata(tmp_path, target):
    before = stale_transaction(tmp_path)
    directory = before.parent
    metadata = {
        "owner": directory / "codex.lock/run-id",
        "receipt": before,
        "guard": directory / "codex.guard",
    }[target]
    outside = write(
        tmp_path / "preserved", metadata.read_text() if metadata.exists() else "data"
    )
    content = outside.read_text()
    metadata.unlink(missing_ok=True)
    metadata.symlink_to(outside)
    result = helper(tmp_path, "begin", os.getpid(), "--force")
    assert result.returncode != 0
    assert metadata.is_symlink()
    assert outside.read_text() == content


def test_concurrent_forced_retries_preserve_winner(tmp_path):
    stale_transaction(tmp_path)
    owners = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        for _ in range(2)
    ]
    try:
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(HELPER),
                    "begin",
                    "--provider",
                    "codex",
                    "--run-id",
                    str(owner.pid),
                    "--force",
                ],
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for owner in owners
        ]
        for process in processes:
            process.communicate(timeout=10)
        assert sorted(process.returncode for process in processes) == [0, 1]
        winner = next(
            owner.pid
            for owner, process in zip(owners, processes)
            if process.returncode == 0
        )
        directory = tmp_path / ".cache/vibehelper-install"
        assert (directory / "codex.lock/run-id").read_text() == str(winner)
        assert list(directory.glob("*.before.json")) == [
            directory / f"codex.{winner}.before.json"
        ]
        assert (directory / f"codex.{winner}.before.json").read_text() == "{}"
    finally:
        for owner in owners:
            owner.terminate()
            owner.wait(timeout=10)


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
def test_unified_force_recovers_existing_stale_lock(tmp_path, provider):
    before = stale_transaction(tmp_path, provider.removesuffix("Helper").lower())
    command, env = installer(tmp_path, provider)
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert "cleared abandoned" in result.stdout, result.stderr
    assert (tmp_path / "phase2-ran").exists()
    assert not before.exists()
    assert not list(before.parent.glob("*.lock"))


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("phase", [1, 2])
def test_cancellation_releases_lock_and_allows_retry(tmp_path, provider, signum, phase):
    bodies = {"phase1": "exit 0", "phase2": "exit 0"}
    bodies[f"phase{phase}"] = "printf ready > ready\nread -r answer"
    command, env = installer(tmp_path, provider, **bodies)
    process = subprocess.Popen(
        command,
        cwd=tmp_path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.02)
        os.killpg(process.pid, signum)
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 128 + signum, output + errors
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=10)
    if phase == 1:
        assert not (tmp_path / "phase2-ran").exists()
    directory = tmp_path / ".cache/vibehelper-install"
    assert not list(directory.glob("*.lock"))
    assert not list(directory.glob("*.before.json"))
    write(tmp_path / provider / f"WSL/part{phase}.sh", "exit 0\n")
    retry = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert "another installation transaction" not in retry.stderr
    assert "does not own this lock" not in retry.stderr
    assert not list(directory.glob("*.lock"))


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("status", [130, 143])
def test_cancelled_child_stops_next_phase(tmp_path, provider, status):
    command, env = installer(tmp_path, provider, phase1=f"exit {status}")
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == status
    assert not (tmp_path / "phase2-ran").exists()
    assert not list((tmp_path / ".cache/vibehelper-install").glob("*.lock"))
