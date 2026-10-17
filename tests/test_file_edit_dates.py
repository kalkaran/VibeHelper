"""Filesystem dates remain independent of Git commit history."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "file-edit-dates.py"


def report(root, *paths, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, paths)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def rows(result):
    return [
        (datetime.fromisoformat(date), json.loads(name))
        for date, name in (
            line.split("\t", 1) for line in result.stdout.splitlines()[1:]
        )
    ]


def test_dates_order_spaces_and_no_changes(tmp_path):
    older, newer = tmp_path / "old.py", tmp_path / "new file\nname.py"
    for path, seconds in ((older, 1600000000), (newer, 1700000000)):
        path.write_text("unchanged\n")
        os.utime(path, (seconds, seconds))
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (older, newer)
    }
    result = report(tmp_path, older.name, newer.name, older.name)
    assert result.returncode == 0, result.stderr
    values = rows(result)
    assert [name for _, name in values] == [newer.name, older.name]
    assert [date.timestamp() for date, _ in values] == [1700000000, 1600000000]
    assert all(date.utcoffset() is not None for date, _ in values)
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before
    }


def test_default_uses_git_ignore_rules(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("ignored.log\n")
    for name in ("tracked.py", "new.py", "ignored.log"):
        (tmp_path / name).write_text("example\n")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    result = report(tmp_path)
    assert result.returncode == 0, result.stderr
    assert {name for _, name in rows(result)} == {".gitignore", "tracked.py", "new.py"}


def test_missing_and_directory_are_errors(tmp_path):
    result = report(tmp_path, "missing.py", ".")
    assert result.returncode == 1
    assert "missing.py" in result.stderr and "not a directory/device" in result.stderr
    assert "Traceback" not in result.stderr


def test_symlink_uses_link_date(tmp_path):
    target, link = tmp_path / "target", tmp_path / "link"
    target.write_text("target\n")
    link.symlink_to(target)
    os.utime(target, (1600000000, 1600000000))
    os.utime(link, (1700000000, 1700000000), follow_symlinks=False)
    result = report(tmp_path, link.name)
    assert result.returncode == 0, result.stderr
    assert rows(result)[0][0].timestamp() == 1700000000


def test_git_unavailable_or_outside_worktree(tmp_path):
    for env in (None, os.environ | {"PATH": ""}):
        result = report(tmp_path, env=env)
        assert result.returncode == 2
        assert "supply file paths explicitly" in result.stderr
        assert "Traceback" not in result.stderr
