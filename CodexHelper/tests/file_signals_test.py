"""Virtual environments must not trigger project tooling detection."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "CodexHelper"


def function(source: str, name: str) -> str:
    return re.search(rf"^{name}\(\) \{{\n.*?^\}}", source, re.MULTILINE | re.DOTALL)[0]


def test_virtual_environment_detection(tmp_path: Path) -> None:
    for directory in ("venv", "backend/custom env", "vids/ytdlp-venv"):
        environment = tmp_path / directory
        packages = environment / "lib/site-packages/example"
        packages.mkdir(parents=True)
        (environment / "pyvenv.cfg").touch()
        for filename in ("dependency.py", "package.json", "setup.py", "install.sh"):
            (packages / filename).touch()

    for platform in ("MacOS", "WSL"):
        for stage in ("part1", "part2"):
            source = (ROOT / platform / f"{stage}.sh").read_text()
            script = function(source, "detect_file_signals")
            script += '\ndetect_file_signals\nprintf "%s %s %s %s\\n" "$PY_FILES" "$PY_CONFIGS" "$NODE_MANIFESTS" "$SHELL_FILES"\n'
            if stage == "part1":
                script += function(source, "has_source_file")
                script += '\nif has_source_file -name "*.sh"; then echo shell; else echo no-shell; fi\n'
            for with_source in (False, True):
                if with_source:
                    (tmp_path / "app.py").touch()
                    (tmp_path / "run.sh").touch()
                result = subprocess.run(
                    ["bash", "-c", script],
                    cwd=tmp_path,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                expected = "1 0 0 1\n" if with_source else "0 0 0 0\n"
                if stage == "part1":
                    expected += "shell\n" if with_source else "no-shell\n"
                assert result.stdout == expected, (platform, stage, result.stdout)
                if with_source:
                    (tmp_path / "app.py").unlink()
                    (tmp_path / "run.sh").unlink()
