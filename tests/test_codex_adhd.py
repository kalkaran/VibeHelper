"""Default plugin and response-style setup through both platform installers."""

import json
import os
import shutil
import subprocess

import pytest
from test_codex_config import ROOT, function, write


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_adhd_setup(tmp_path, platform):
    script = (ROOT / "CodexHelper" / platform / "part1.sh").read_text()
    harness = "\n".join(
        function(script, name)
        for name in (
            "setup_adhd",
            "enable_adhd_style",
            "append_if_missing",
            "backup_file",
            "run",
            "codex_plugin_marketplace_configured",
        )
    )
    harness += r"""
have() { [[ "$MISSING" != 1 ]]; }
log() { :; }
confirm() { [[ "$DECLINE" != 1 ]]; }
record_install_ok() { echo "OK: $*"; }
record_install_skipped() { echo "SKIP: $*"; }
record_install_failed() { echo "FAIL: $*"; FAILED=1; }
codex() {
    if [[ "$*" == "plugin marketplace list" ]]; then
        [[ "$CONFIGURED" == 1 ]] && echo 'i-have-adhd /marketplace'
        return 0
    fi
    printf '%s\n' "$*" >> "$CALLS"
    [[ "$*" != "$FAIL_COMMAND" ]]
}
FAILED=0
setup_adhd
exit "$FAILED"
"""
    calls = tmp_path / "calls"
    instructions = tmp_path / "home/.codex/AGENTS.md"
    write(instructions, "Keep existing instructions.\n")

    def run(**overrides):
        calls.write_text("")
        result = subprocess.run(
            ["bash", "-euc", harness],
            env=os.environ
            | dict.fromkeys(
                ["MISSING", "DECLINE", "CONFIGURED", "FORCE", "DRY_RUN"], "0"
            )
            | {
                "FAIL_COMMAND": "",
                "CALLS": str(calls),
                "HOME": str(tmp_path / "home"),
                "CODEX_HOME": "",
            }
            | overrides,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, calls.read_text().splitlines()

    add_market = "plugin marketplace add ayghri/i-have-adhd --ref main"
    install = "plugin add i-have-adhd@i-have-adhd"
    upgrade = "plugin marketplace upgrade i-have-adhd"
    for options, expected in (
        ({}, [add_market, install]),
        ({"CONFIGURED": "1"}, [install]),
        ({"CONFIGURED": "1", "FORCE": "1"}, [upgrade, install]),
    ):
        result, actual = run(**options)
        assert result.returncode == 0 and actual == expected, result.stdout
        assert "OK:" in result.stdout
        assert instructions.read_text().startswith("Keep existing instructions.\n")
        assert (
            instructions.read_text().count("<!-- BEGIN CodexHelper: i-have-adhd -->")
            == 1
        )
    installed = instructions.read_bytes()
    assert list(instructions.parent.glob("AGENTS.md.bak.*"))
    for options in ({"DRY_RUN": "1"}, {"DECLINE": "1"}):
        result, actual = run(**options)
        assert result.returncode == 0 and actual == [], result.stdout
        assert "SKIP:" in result.stdout and "OK:" not in result.stdout
        assert instructions.read_bytes() == installed
    result, actual = run(MISSING="1")
    assert result.returncode == 1 and actual == []
    for command, options, expected in (
        (add_market, {}, [add_market]),
        (install, {}, [add_market, install]),
        (upgrade, {"CONFIGURED": "1", "FORCE": "1"}, [upgrade]),
    ):
        result, actual = run(FAIL_COMMAND=command, **options)
        assert result.returncode == 1 and actual == expected, result.stdout
        assert "FAIL:" in result.stdout and "OK:" not in result.stdout
        assert instructions.read_bytes() == installed
    # Neither a failed first install nor a dry run creates global instructions.
    for options in ({"DRY_RUN": "1"}, {"FAIL_COMMAND": install}, {"DECLINE": "1"}):
        result, _ = run(CODEX_HOME=str(tmp_path / "unused-home"), **options)
        assert not (tmp_path / "unused-home").exists()

    # Exercise the real global-setup dispatch through its early return, isolating
    # the unrelated installers that follow it.
    dispatch = function(script, "install_global_tools")
    dispatch = dispatch[: dispatch.index("\n\t# Ponytail:")] + "\n}\n"
    dispatch += """
log() { :; }
setup_humanizer() { :; }
setup_unlazy() { :; }
setup_impeccable() { :; }
setup_adhd() { echo selected; }
CREATE_CODEX_HOOKS=0 CREATE_RTK_HOOK=0
install_global_tools
"""
    entry = tmp_path / "parse-and-dispatch.sh"
    write(entry, script[: script.index("\nconfirm() {")] + dispatch)
    for args, expected in (
        ([], "selected\n"),
        (["--adhd"], "selected\n"),
        (["--no-adhd"], ""),
        (["--repo-only"], ""),
        (["--adhd", "--repo-only"], ""),
        (["--fresh-install"], "selected\n"),
        (["--fresh-install", "--no-adhd"], ""),
        (["--no-adhd", "--fresh-install"], ""),
    ):
        result = subprocess.run(
            ["bash", str(entry), *args],
            env=os.environ,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == expected


@pytest.mark.parametrize("flag", ["--adhd", "--no-adhd"])
def test_adhd_entry_point_forwarding(tmp_path, flag):
    shutil.copy(ROOT / "CodexHelper/install.sh", tmp_path / "install.sh")
    assets = tmp_path / "templates/vibe_scripts"
    assets.mkdir(parents=True)
    shutil.copy(ROOT / "templates/vibe_scripts/managed-install.py", assets)
    for platform in ("MacOS", "WSL"):
        for phase in (1, 2):
            write(
                tmp_path / platform / f"part{phase}.sh",
                f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{tmp_path}/part{phase}.args'\n",
            )
    subprocess.run(
        ["bash", str(tmp_path / "install.sh"), flag, "--yes", "--dry-run"],
        env=os.environ | {"WSL_DISTRO_NAME": "test"},
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    assert flag in (tmp_path / "part1.args").read_text().splitlines()
    assert flag not in (tmp_path / "part2.args").read_text().splitlines()


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_adhd_custom_home_and_override(tmp_path, platform):
    script = (ROOT / "CodexHelper" / platform / "part1.sh").read_text()
    harness = "\n".join(
        function(script, name)
        for name in ("enable_adhd_style", "append_if_missing", "backup_file")
    )
    harness += "\nlog() { :; }\nDRY_RUN=0\nenable_adhd_style\nenable_adhd_style\n"
    for name, override in (("active", "Existing override.\n"), ("empty", " \n")):
        codex_dir = tmp_path / name
        base = codex_dir / "AGENTS.md"
        preferred = codex_dir / "AGENTS.override.md"
        write(base, "Existing base.\n")
        write(preferred, override)
        subprocess.run(
            ["bash", "-euc", harness],
            env=os.environ
            | {"CODEX_HOME": str(codex_dir), "HOME": str(tmp_path / "home")},
            capture_output=True,
            check=True,
        )
        target, unchanged = (preferred, base) if name == "active" else (base, preferred)
        assert target.read_text().count("<!-- BEGIN CodexHelper: i-have-adhd -->") == 1
        assert "from the first reply in each session" in target.read_text()
        assert unchanged.read_text() == (
            "Existing base.\n" if name == "active" else override
        )
        assert next(codex_dir.glob(target.name + ".bak.*")).read_text() == (
            override if name == "active" else "Existing base.\n"
        )
        assert not (tmp_path / "home").exists()


def test_native_codex_plugin_repeat_install(tmp_path):
    codex = shutil.which("codex")
    if not codex:
        pytest.skip("Codex CLI unavailable for local plugin compatibility check")
    market = tmp_path / "market"
    write(
        market / ".agents/plugins/marketplace.json",
        json.dumps(
            {
                "name": "adhd-test",
                "plugins": [
                    {
                        "name": "sample",
                        "source": {"source": "local", "path": "./sample"},
                    }
                ],
            }
        ),
    )
    write(
        market / "sample/.codex-plugin/plugin.json",
        json.dumps({"name": "sample", "version": "1.0.0", "skills": "./skills/"}),
    )
    write(
        market / "sample/skills/sample/SKILL.md",
        "---\nname: sample\ndescription: Test\n---\nTest.\n",
    )
    home = tmp_path / "codex-home"
    home.mkdir()
    env = os.environ | {"CODEX_HOME": str(home), "HOME": str(tmp_path)}
    for args in (
        ["plugin", "marketplace", "add", str(market)],
        ["plugin", "add", "sample@adhd-test"],
        ["plugin", "add", "sample@adhd-test"],
    ):
        result = subprocess.run(
            [codex, *args],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert (
        home / "plugins/cache/adhd-test/sample/1.0.0/skills/sample/SKILL.md"
    ).exists()
