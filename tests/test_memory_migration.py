"""Narrow, backed-up memory migrations preserve customization and opt-outs."""

import json
import shutil
import subprocess
import sys

import pytest
from test_project_memory import INSTALL, MANAGED, ROOT


def installed(root):
    INSTALL.install(root, "codex", hooks=False)
    assets = {}
    for name in MANAGED.MEMORY_ASSETS:
        path = root / "vibe_scripts/codex" / name
        shutil.copy2(ROOT / "templates/vibe_scripts" / name, path)
        assets[str(path.relative_to(root))] = MANAGED.digest(path)
    receipt = root / ".cache/vibehelper-install/codex.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(assets))
    return receipt


def test_migration_backup_optout_custom_settings_and_rerun(tmp_path):
    installed(tmp_path)
    policy = tmp_path / "vibe_scripts/codex/memory-policy.json"
    data = json.loads(policy.read_text()) | {"custom": "keep", "crg": False}
    policy.write_text(json.dumps(data))
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text('[mcp_servers.custom]\ncommand = "keep"\n')
    graph = tmp_path / ".graphify/graph.json"
    graph.parent.mkdir()
    graph.write_text('{"nodes": [], "links": []}')
    old = policy.read_bytes()
    state = tmp_path / ".cache/project-memory/state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"crg": {}, "graphify": {}, "semantic": "keep"}))
    result = MANAGED.memory_refresh(tmp_path, "codex")
    from pathlib import Path

    backup = Path(result["backup"])
    assert (backup / "files/vibe_scripts/codex/memory-policy.json").read_bytes() == old
    assert (backup / "files/.graphify/graph.json").read_bytes() == graph.read_bytes()
    assert (backup / "RESTORE.txt").is_file()
    assert json.loads(state.read_text()) == {"semantic": "keep"}
    assert "crg" in json.loads(
        (backup / "files/.cache/project-memory/state.json").read_text()
    )
    new = json.loads(policy.read_text())
    assert new["watch"] is False and new["crg"] is False and new["custom"] == "keep"
    assert 'command = "keep"' in config.read_text()
    assert MANAGED.memory_refresh(tmp_path, "codex")["status"] == "migrated"


def test_custom_helper_conflict_is_preflighted(tmp_path):
    installed(tmp_path)
    helper = tmp_path / "vibe_scripts/codex/project-memory.py"
    helper.write_text(helper.read_text() + "\n# user customization\n")
    policy = tmp_path / "vibe_scripts/codex/memory-policy.json"
    old = policy.read_bytes()
    with pytest.raises(ValueError, match="customized memory helper"):
        MANAGED.memory_refresh(tmp_path, "codex")
    assert policy.read_bytes() == old
    assert not list((tmp_path / ".cache/vibehelper-install").glob("memory-backup-*"))


def test_discovery_deduplicates_and_omits_backups(tmp_path):
    for name in ("react_app", "other", "notes/backup"):
        installed(tmp_path / name)
    found = MANAGED.memory_projects([tmp_path, tmp_path / "react_app"])
    assert list(found) == [str(tmp_path / "react_app"), str(tmp_path / "other")]


def test_malformed_configuration_rolls_back_and_installer_lock_blocks(tmp_path):
    receipt = installed(tmp_path)
    active = receipt.parent / "codex.lock"
    active.mkdir()
    with pytest.raises(ValueError, match="already active"):
        MANAGED.memory_refresh(tmp_path, "codex")
    active.rmdir()
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text("[broken\n")
    policy = tmp_path / "vibe_scripts/codex/memory-policy.json"
    original = policy.read_bytes()
    with pytest.raises(ValueError):
        MANAGED.memory_refresh(tmp_path, "codex")
    assert config.read_text() == "[broken\n"
    assert policy.read_bytes() == original
    backups = list(receipt.parent.glob("memory-backup-*/manifest.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["status"] == "rolled-back"


def test_refresh_command_records_failure_and_preserves_exit(
    tmp_path, monkeypatch, capsys
):
    installed(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "managed-install.py",
            "memory-refresh",
            "--provider",
            "codex",
            "--root",
            str(tmp_path),
        ],
    )

    def failed(command, **kwargs):
        assert command[-1] == "--architecture"
        kwargs["stdout"].write("diagnostic: parser unavailable\n")
        return MANAGED.subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(MANAGED.subprocess, "run", failed)
    with pytest.raises(SystemExit) as caught:
        MANAGED.main()
    assert caught.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["refresh_exit_code"] == 1
    assert result["duration_seconds"] >= 0
    from pathlib import Path

    assert Path(result["log"]).read_text() == "diagnostic: parser unavailable\n"
    assert (Path(result["backup"]) / "result.json").is_file()


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
def test_public_installer_forwards_watch_optout(tmp_path, provider):
    result = subprocess.run(
        [
            "bash",
            "-x",
            str(ROOT / provider / "install.sh"),
            "--repo",
            str(tmp_path),
            "--dry-run",
            "--repo-only",
            "--no-memory-watch",
            "--no-omniroute",
            "--no-humanizer",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(
        "bash " in line and "/part1.sh " in line and "--no-memory-watch" in line
        for line in result.stderr.splitlines()
    )
