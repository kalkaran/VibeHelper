"""Reviewed recommendations retain their exact bytes without staging anything."""

from pathlib import Path

import pytest
from test_project_memory import load

WRITER = load("write-git-add")


def test_current_and_history_match_and_preserve_old_recommendation(tmp_path):
    current = tmp_path / "git-add.txt"
    current.write_bytes(b"previous recommendation\n")
    first = b':(top,literal)file with spaces.py\n":(top,literal)quoted\\"file.py"\n'
    history = WRITER.write_manifest(tmp_path, first)
    assert current.read_bytes() == history.read_bytes() == first
    assert (
        next(history.parent.glob("*-previous.txt")).read_bytes()
        == b"previous recommendation\n"
    )
    later = WRITER.write_manifest(tmp_path, b":(top,literal)next.py\n")
    assert later != history and history.read_bytes() == first
    assert current.read_bytes() == later.read_bytes()
    assert len(list(history.parent.glob("*.txt"))) == 4


def test_history_failure_preserves_current_recommendation(tmp_path, monkeypatch):
    current = tmp_path / "git-add.txt"
    current.write_bytes(b"keep\n")
    original = Path.open

    def fail(path, *args, **kwargs):
        if path.parent.name == "git-add-history":
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError, match="disk full"):
        WRITER.write_manifest(tmp_path, b"new\n")
    assert current.read_bytes() == b"keep\n"
