"""Parse installers with the system shell and preserve instructions verbatim."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELLS = sorted(
    {
        str(Path(shell).resolve())
        for shell in ("/bin/bash", shutil.which("bash"))
        if shell and Path(shell).is_file()
    }
)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("helper", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_installer_shell_syntax(shell, helper, platform):
    result = subprocess.run(
        [shell, "-n", str(ROOT / helper / platform / "part1.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("helper", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize("document", ["instructions", "omniroute"])
def test_instruction_heredoc_is_literal(tmp_path, shell, helper, platform, document):
    source = (ROOT / helper / platform / "part1.sh").read_text()
    variable, marker = (
        ("agents_md", "EOF_AGENTS")
        if helper == "CodexHelper"
        else ("content", "CLAUDEMD_EOF")
    )
    if document == "omniroute":
        variable, marker = "content", "OMNIROUTE_DOC"
    start = source.index("\tIFS= read -r -d '' " + variable + " <<'" + marker)
    end = source.index("\n\t" + variable + '="${' + variable + "%$'\\n'}\"", start)
    end = source.index("\n", end + 1)
    assignment = source[start:end]
    expected = assignment.split(marker + "' || :\n", 1)[1].split("\n" + marker, 1)[0]
    harness = "set -eu\n" + assignment + '\nprintf "%s" "$' + variable + '"\n'
    result = subprocess.run(
        [shell, "--noprofile", "--norc", "-c", harness],
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key != "BASH_ENV"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
