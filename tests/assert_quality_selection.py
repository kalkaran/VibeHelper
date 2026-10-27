"""Shared installer-smoke assertions for generated lint file selection."""

import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
provider = sys.argv[2]
kind = sys.argv[3]
empty = kind == "empty-shell"
kind = "shell" if empty else kind
extensions, tool = {
    "shell": ([".sh"], "shellcheck"),
    "markdown": ([".md", ".markdown"], "markdownlint-cli2"),
}[kind]
expected = {f"selection-fixture/nested/app{extension}" for extension in extensions}
if not empty:
    for name in expected:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    for directory in (".agents", ".claude", ".codex"):
        (root / directory).mkdir(exist_ok=True)
        for extension in extensions:
            (root / directory / f"installed{extension}").write_text("fixture\n")

makefile = (root / f"vibe_scripts/{provider}/Makefile").read_text()
# Default lint uses task scope; full selection remains explicit and filtered.
assert "lint-all-ai:" in makefile
assert "agent-check-edited.py --mode lint --all" in makefile
result = subprocess.run(
    [
        sys.executable,
        f"vibe_scripts/{provider}/agent-check-edited.py",
        "--list-files",
        *extensions,
    ],
    cwd=root,
    check=True,
    capture_output=True,
)
paths = [Path(name).as_posix() for name in result.stdout.decode().split("\0") if name]
assert not paths if empty else expected.issubset(paths), paths
assert not any(
    name.startswith((".agents/", ".claude/", ".codex/")) for name in paths
), paths
