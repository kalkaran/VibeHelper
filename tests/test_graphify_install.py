"""Graphify package and skill setup stays consistent across all installers."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "install_graphify", ROOT / "templates/vibe_scripts/install-graphify.py"
)
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(INSTALL.Path, "home", lambda: tmp_path)
    binary = tmp_path / "graphify"
    binary.write_text("#!/other/python3\n")
    state = {"owner": "uv", "binary": str(binary), "calls": [], "probes": []}

    def probe(*command):
        state["probes"].append(command)
        if command[:2] == ("/other/python3", "-c"):
            return subprocess.CompletedProcess(
                command, 0, str(state.get("user_site", False)), ""
            )
        owned = command == {
            "uv": ("uv", "tool", "list"),
            "pipx": ("pipx", "list", "--short"),
            "pip": ("/other/python3", "-m", "pip", "show", "graphifyy"),
        }.get(state["owner"])
        return subprocess.CompletedProcess(
            command, 0 if owned else 1, "graphifyy 1.0\n", ""
        )

    def run(command, check):
        state["calls"].append(command)
        failed = state.get("fail") in command
        if "graphifyy" in command and "install" in command and not failed:
            state["binary"] = str(binary)
        return subprocess.CompletedProcess(command, int(failed))

    monkeypatch.setattr(INSTALL, "probe", probe)
    monkeypatch.setattr(INSTALL.subprocess, "run", run)
    monkeypatch.setattr(INSTALL, "find_graphify", lambda: state["binary"])
    monkeypatch.setattr(
        INSTALL.shutil, "which", lambda name: name if name == state["owner"] else None
    )
    return state


@pytest.mark.parametrize("platform", ["codex", "claude"])
@pytest.mark.parametrize("owner", ["uv", "pipx", "pip", "unknown"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_refresh(runtime, platform, owner, dry_run, capsys):
    runtime["owner"] = owner
    assert INSTALL.install(platform, refresh=True, dry_run=dry_run) == 0
    calls = runtime["calls"]
    if dry_run:
        assert calls == []
        assert "Would run:" in capsys.readouterr().out
        return
    assert calls[-1] == [runtime["binary"], "install", "--platform", platform]
    expected = {
        "uv": ["uv", "tool", "upgrade", "graphifyy"],
        "pipx": ["pipx", "upgrade", "graphifyy"],
        "pip": ["/other/python3", "-m", "pip", "install", "--upgrade", "graphifyy"],
    }
    if owner == "unknown":
        assert len(calls) == 1
        assert "package upgrade skipped" in capsys.readouterr().out
    else:
        assert calls[:-1] == [expected[owner]]


@pytest.mark.parametrize("owner", ["uv", "pipx", "pip"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_fresh_install(runtime, owner, dry_run, capsys):
    runtime.update(owner=owner, binary=None)
    assert INSTALL.install("codex", dry_run=dry_run) == 0
    if dry_run:
        assert runtime["calls"] == []
        assert "graphify install --platform codex" in capsys.readouterr().out
    else:
        install, register = runtime["calls"]
        assert install[-1] == "graphifyy"
        assert "install" in install
        assert "upgrade" not in install
        assert install[0] == (sys.executable if owner == "pip" else owner)
        assert register == [runtime["binary"], "install", "--platform", "codex"]


def test_existing_without_refresh(runtime):
    assert INSTALL.install("claude") == 0
    assert runtime["probes"] == []
    assert runtime["calls"] == [[runtime["binary"], "install", "--platform", "claude"]]


@pytest.mark.parametrize("failure", ["upgrade", "--platform"])
def test_failure_still_reports_failure(runtime, failure, capsys):
    runtime["fail"] = failure
    assert INSTALL.install("codex", refresh=True) == 1
    assert len(runtime["calls"]) == 2
    assert "skill ready" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "platform,root", [("codex", ".agents"), ("codex", ".codex"), ("claude", ".claude")]
)
def test_existing_skill_without_refresh_is_preserved(runtime, tmp_path, platform, root):
    skill = tmp_path / root / "skills/graphify/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Custom skill instructions")
    assert INSTALL.install(platform) == 0
    assert runtime["calls"] == []
    assert skill.read_text() == "Custom skill instructions"


@pytest.mark.parametrize("user_site", [False, True])
def test_pip_preserves_install_scope(runtime, user_site):
    runtime.update(owner="pip", user_site=user_site)
    assert INSTALL.install("claude", refresh=True) == 0
    assert ("--user" in runtime["calls"][0]) == user_site


def test_failed_fresh_install_does_not_register(runtime):
    runtime.update(binary=None, fail="graphifyy")
    assert INSTALL.install("claude") == 1
    assert len(runtime["calls"]) == 1


@pytest.mark.parametrize(
    "header", ["#!/bin/sh\n", "not a script\n", "#!/usr/bin/env python3\n"]
)
def test_unrecognized_interpreter_never_uses_current_python(runtime, header):
    runtime["owner"] = "unknown"
    Path(runtime["binary"]).write_text(header)
    assert INSTALL.upgrade_command(runtime["binary"]) is None
    assert not any(sys.executable in command for command in runtime["probes"])


def test_off_path_binary(tmp_path, monkeypatch):
    binary = tmp_path / ".local/bin/graphify"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(INSTALL.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(INSTALL.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        INSTALL, "probe", lambda *args: subprocess.CompletedProcess(args, 1, "", "")
    )
    assert INSTALL.find_graphify() == str(binary)


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize("mode", ["refresh", "dry", "normal", "failure", "disabled"])
def test_installer_adapter(tmp_path, provider, platform, mode):
    source = (ROOT / provider / platform / "part1.sh").read_text()
    function = (
        "install_graphify_skill() {"
        + source.split("install_graphify_skill() {", 1)[1].split("\n}\n", 1)[0]
        + "\n}"
    )
    harness = r"""
set -eu
log() { printf '%s\n' "$*"; }
record_install_ok() { log "OK $*"; }
record_install_failed() { log "FAILED $*"; }
record_failure() { log "FAILED $*"; }
resolve_binary() { printf '/custom/bin/graphify'; }
python3() { printf 'HELPER %s\n' "$*"; test "$MODE" != failure; }
"""
    result = subprocess.run(
        ["bash", "-c", harness + function + "\ninstall_graphify_skill\n"],
        env=os.environ
        | {
            "HELPER_ROOT": str(ROOT / provider),
            "FORCE": str(int(mode in ("refresh", "dry"))),
            "REFRESH_TOOLS": str(int(mode in ("refresh", "dry"))),
            "DRY_RUN": str(int(mode == "dry")),
            "RUN_GRAPHIFY": str(int(mode != "disabled")),
            "MODE": mode,
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if mode == "disabled" and provider == "ClaudeHelper":
        assert result.stdout == ""
    else:
        assert str(
            ROOT / "templates/vibe_scripts/install-graphify.py"
        ) in result.stdout.replace(f"{provider}/../", "")
        assert f"--platform {provider.removesuffix('Helper').lower()}" in result.stdout
        assert f"--dry-run {int(mode == 'dry')}" in result.stdout
        assert ("FAILED" in result.stdout) == (mode == "failure")
        if mode == "dry":
            assert "OK " not in result.stdout
