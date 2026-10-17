"""Installer Git initialization preserves repositories and honors dry-run."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("helper", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize(
    "mode", ["new", "dry", "existing", "nested", "linked", "bare", "broken"]
)
def test_git_initialization(tmp_path, helper, platform, mode):
    project = tmp_path / "project with spaces"
    project.mkdir()
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=project,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    if mode in ("existing", "nested", "linked", "bare"):
        git("init", "--quiet", *(["--bare"] if mode == "bare" else []))
    if mode == "linked":
        git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        )
        linked = tmp_path / "linked"
        git("worktree", "add", "--detach", str(linked))
        project = linked
    target = project
    if mode in ("nested", "broken"):
        target = project / "nested"
        target.mkdir()
    if mode == "broken":
        (project / ".git").write_text("gitdir: missing\n")
    before = (project / ".git").read_bytes() if mode in ("broken", "linked") else None
    head = (
        git("rev-parse", "--git-path", "HEAD")
        if mode in ("existing", "nested")
        else None
    )
    original_head = (project / head).read_bytes() if head else None
    source = (ROOT / helper / platform / "part1.sh").read_text()
    function = source.split("ensure_git_repository() {", 1)[1].split("\n}\n", 1)[0]
    script = (
        """set -eu
log() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }
ROOT="$PWD"
ensure_git_repository() {"""
        + function
        + "\n}\nensure_git_repository\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=target,
        env=env | {"DRY_RUN": str(int(mode == "dry"))},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is (mode not in ("bare", "broken")), result.stderr
    if mode == "new":
        assert (project / ".git").is_dir()
        assert git("ls-files") == ""
        assert git("remote") == ""
    elif mode == "dry":
        assert not (project / ".git").exists()
        assert "Would initialize Git" in result.stdout
    elif mode == "nested":
        assert not (target / ".git").exists()
    elif mode == "bare":
        assert not (project / ".git").exists()
    if before is not None:
        assert (project / ".git").read_bytes() == before
    if original_head is not None:
        assert (project / head).read_bytes() == original_head
    invocation = source.index("ensure_git_repository || exit 1")
    if helper == "CodexHelper":
        assert (
            source.index("\n\tbootstrap_prereqs\n")
            < invocation
            < source.index("\n\tcreate_agent_files\n")
        )
    else:
        assert invocation < source.index("\nwhile IFS= read -r -d '' file; do")
        if platform == "WSL":
            assert source.index("\nearly_install_system_packages\n") < invocation


@pytest.mark.parametrize("helper", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_manifest_guidance_is_installed(helper, platform):
    source = (ROOT / helper / platform / "part1.sh").read_text()
    marker = "EOF_AGENTS" if helper == "CodexHelper" else "CLAUDEMD_EOF"
    instructions = (
        source.split("<<'" + marker + "'", 1)[1]
        .split("\n", 1)[1]
        .split("\n" + marker, 1)[0]
    )
    assert "git add --pathspec-from-file=git-add.txt" in instructions
    assert ":(top,literal)" in instructions
    assert "Do not stage automatically" in instructions
    assert "pre-existing edits" in instructions


def test_manifest_preview_uses_literal_paths(tmp_path):
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            check=True,
            text=True,
        ).stdout

    git("init", "--quiet")
    selected = ["space name.py", "star*.py", "bracket[1].py"]
    unrelated = ["star-other.py", "bracket1.py", "unrelated.py"]
    for name in selected + unrelated:
        (tmp_path / name).write_text("example")
    (tmp_path / "git-add.txt").write_text(
        "".join(":(top,literal)" + name + "\n" for name in selected)
    )
    preview = git("add", "--dry-run", "--pathspec-from-file=git-add.txt")
    assert all(name in preview for name in selected)
    assert all(name not in preview for name in unrelated)
    assert "git-add.txt" not in preview
    assert git("ls-files") == ""
