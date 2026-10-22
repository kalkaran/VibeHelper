"""Project memory lifecycle, scoped retrieval and installer regressions."""

import argparse
import fcntl
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "templates/vibe_scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MEMORY = load("project-memory")
INSTALL = load("install-project-memory")
MANAGED = load("managed-install")


def test_memory_settings_ownership_preserves_custom_hooks(repo):
    settings = repo / "settings.local.json"
    document = {"hooks": INSTALL.desired("claude")}
    settings.write_text(json.dumps(document))
    assert MANAGED.recognizable(settings, "claude")
    document["hooks"]["Stop"][0]["hooks"].append(
        {"command": "echo custom", "type": "command"}
    )
    settings.write_text(json.dumps(document))
    assert not MANAGED.recognizable(settings, "claude")


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracking.py").write_text("def track():\n    return 1\n")
    return tmp_path


def args(action="status", **kwargs):
    return argparse.Namespace(
        action=action,
        provider="codex",
        architecture=False,
        task="tracking reliability",
        files=["tracking.py"],
        snapshot=None,
        evidence="",
        **kwargs,
    )


def state(repo):
    return json.loads((repo / ".cache/project-memory/state.json").read_text())


def backend(monkeypatch, repo, fail=False):
    calls = []
    real_run = MEMORY.run

    def run(root, command, timeout=30):
        if command[0] == "git":
            return real_run(root, command, timeout)
        calls.append(command)
        if fail:
            raise subprocess.CalledProcessError(1, command)
        key = "crg" if "code-review-graph" in command[0] else "graphify"
        path = root / MEMORY.BACKENDS[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"nodes": [], "links": [], "revision": time.time_ns()})
            if key == "graphify"
            else str(time.time_ns())
        )
        return "updated"

    monkeypatch.setattr(MEMORY, "run", run)
    monkeypatch.setattr(MEMORY.shutil, "which", lambda name: name)
    return calls


def test_content_scan_deletion_rename_config_and_noise(repo):
    for directory in ("notes", "graphify-out", ".cache", "codebase-wiki"):
        (repo / directory).mkdir()
        (repo / directory / "readme.md").write_text("tracking")
    (repo / "config.yaml").write_text("test: 1")
    (repo / "Dockerfile").write_text("FROM scratch")
    before = MEMORY.snapshot(repo)
    assert "config.yaml" in before and "Dockerfile" in before
    assert "codebase-wiki/readme.md" in before
    assert not any(
        name.startswith(("notes/", "graphify-out/", ".cache/")) for name in before
    )
    old_time = (repo / "tracking.py").stat().st_mtime
    (repo / "tracking.py").write_text("def track():\n    return 2\n")
    os.utime(repo / "tracking.py", (old_time - 100, old_time - 100))
    changed = MEMORY.snapshot(repo)
    assert MEMORY.changes(before, changed) == ["tracking.py"]
    (repo / "tracking.py").rename(repo / "renamed.py")
    assert MEMORY.changes(changed, MEMORY.snapshot(repo)) == [
        "renamed.py",
        "tracking.py",
    ]


def test_record_only_then_batch_once_and_deletion_rebuild(repo, monkeypatch):
    calls = backend(monkeypatch, repo)
    MEMORY.handle(repo, args("hook"), {"hook_event_name": "PostToolUse"})
    assert not calls
    value = MEMORY.handle(repo, args("refresh"))
    assert value["freshness"]["crg"]["status"] == "current"
    assert calls[0][1] == "build"
    MEMORY.handle(repo, args("hook"), {"hook_event_name": "Stop"})
    assert len(calls) == 1
    (repo / "tracking.py").unlink()
    MEMORY.handle(repo, args("refresh"))
    assert len(calls) == 2 and calls[-1][1] == "build"


def test_failure_and_noop_remain_pending(repo, monkeypatch):
    backend(monkeypatch, repo, fail=True)
    value = MEMORY.handle(repo, args("refresh"))
    assert value["freshness"]["crg"]["status"] == "pending"
    assert "tracking.py" in value["freshness"]["crg"]["paths"]
    assert value["freshness"]["crg"]["error"]
    assert "crg" not in state(repo)


def test_separate_semantic_freshness_and_optouts(repo, monkeypatch):
    calls = backend(monkeypatch, repo)
    (repo / "README.md").write_text("Tracking details")
    request = args("refresh")
    request.architecture = True
    value = MEMORY.handle(repo, request)
    assert value["freshness"]["graphify"]["status"] == "current"
    assert value["freshness"]["semantic"]["status"] == "needs review"
    assert (repo / "graphify-out/needs_update").exists()
    reviewed = args("accept-semantic")
    reviewed.snapshot = value["snapshot"]
    reviewed.evidence = "README.md inspected against implementation"
    MEMORY.handle(repo, reviewed)
    assert not (repo / "graphify-out/needs_update").exists()
    (repo / "README.md").write_text("Changed meaning")
    MEMORY.handle(repo, request)
    assert (repo / "graphify-out/needs_update").exists()
    before = len(calls)
    INSTALL.install(repo, "codex", hooks=False, crg=False, graphify=False)
    value = MEMORY.handle(repo, request)
    assert value["freshness"]["graphify"]["status"] == "disabled"
    request.action = "context"
    MEMORY.handle(repo, request)
    assert len(calls) == before


def test_branch_identity_invalidates_and_concurrent_lock_is_advisory(repo, monkeypatch):
    calls = backend(monkeypatch, repo)
    MEMORY.handle(repo, args("refresh"))
    monkeypatch.setattr(
        MEMORY,
        "identity",
        lambda root: {"root": str(root), "head": "another", "branch": "feature"},
    )
    MEMORY.handle(repo, args("refresh"))
    assert len(calls) == 2 and calls[-1][1] == "build"
    with (repo / ".cache/project-memory/lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert MEMORY.handle(repo, args("refresh"))["status"] == "busy"
    assert len(calls) == 2


def test_planning_scope_and_source_paths(repo, monkeypatch):
    calls = backend(monkeypatch, repo)
    (repo / "codebase-wiki").mkdir()
    (repo / "codebase-wiki/tracking.md").write_text(
        "Tracking reliability depends on attribution.py"
    )
    (repo / "codebase-wiki/design.md").write_text("Typography and colors")
    value = MEMORY.handle(repo, args("context"))
    assert value["inspection_paths"] == ["tracking.py"]
    assert value["wiki"] == ["codebase-wiki/tracking.md"]
    assert any(command[1:3] == ["query", "tests_for"] for command in calls)
    assert "does not authorize" in value["instructions"]
    request = args("context")
    request.files = ["../outside.py"]
    with pytest.raises(ValueError, match="inside"):
        MEMORY.handle(repo, request)


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_hook_merge_preserves_custom_and_deduplicates(repo, provider):
    path = repo / (
        ".codex/config.toml" if provider == "codex" else ".claude/settings.local.json"
    )
    path.parent.mkdir()
    custom = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "echo keep", "async": True}],
    }
    legacy = {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": min(INSTALL.legacy_commands(repo))}],
    }
    if provider == "codex":
        path.write_text(
            '[features]\nhooks = false\n\n[mcp_servers.custom]\ncommand = "keep"\n\n'
            + INSTALL.render("PostToolUse", custom)
            + INSTALL.render("PostToolUse", legacy)
        )
    else:
        path.write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Read"]},
                    "hooks": {"PostToolUse": [custom, legacy]},
                }
            )
        )
    INSTALL.install(repo, provider)
    first = path.read_text()
    INSTALL.install(repo, provider)
    assert path.read_text() == first
    data = tomllib.loads(first) if provider == "codex" else json.loads(first)
    assert custom in data["hooks"]["PostToolUse"]
    assert legacy not in data["hooks"]["PostToolUse"]
    for groups in data["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                if hook.get("command") == INSTALL.command(provider):
                    assert hook["timeout"] == 200
    assert (
        sum(
            h.get("command") == INSTALL.command(provider)
            for g in data["hooks"]["PostToolUse"]
            for h in g["hooks"]
        )
        == 0
    )
    if provider == "codex":
        assert data["features"]["hooks"] is False
        assert data["mcp_servers"]["custom"]["command"] == "keep"
    else:
        assert data["permissions"] == {"allow": ["Read"]}


@pytest.mark.parametrize(
    "provider,folder", [("codex", "CodexHelper"), ("claude", "ClaudeHelper")]
)
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_installer_delivery_and_rerun(tmp_path, provider, folder, platform):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    tool_bin = tmp_path / "bin"
    tool_bin.mkdir()
    npx = tool_bin / "npx"
    npx.write_text("#!/bin/sh\nexit 0\n")
    npx.chmod(0o755)
    env = os.environ | {
        "PATH": str(tool_bin) + os.pathsep + os.environ["PATH"],
        "HOME": str(home),
        "CODEX_CONFIG": str(home / ".codex/config.toml"),
    }
    options = ["--repo-only", "--no-omniroute", "--no-humanizer"]
    if provider == "codex":
        options += [
            "--yes",
            "--no-crg-build",
            "--no-apply-codex-config",
            "--no-rtk-hook",
        ]
    else:
        options += ["--no-crg", "--no-graphify", "--no-rtk"]
    command = ["bash", str(ROOT / folder / platform / "part1.sh"), *options]
    for extra in ([], ["--refresh-managed"]):
        result = subprocess.run(
            command + extra,
            check=False,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert (repo / f"vibe_scripts/{provider}/project-memory.py").exists()
    assert (repo / f"vibe_scripts/{provider}/cleanup-cache.py").exists()
    config = repo / (
        ".codex/config.toml" if provider == "codex" else ".claude/settings.local.json"
    )
    installed = (
        tomllib.loads(config.read_text())
        if provider == "codex"
        else json.loads(config.read_text())
    )
    pre_commands = [
        hook["command"]
        for group in installed["hooks"]["PreToolUse"]
        for hook in group["hooks"]
    ]
    assert sum("pre_tool_use_policy.py" in command for command in pre_commands) == 1
    assert not any("project-memory.py" in command for command in pre_commands)
    assert not any("graph_first_policy.py" in command for command in pre_commands)
    stop_commands = [
        hook["command"]
        for group in installed["hooks"]["Stop"]
        for hook in group["hooks"]
    ]
    assert sum("stop_edited_check.py" in command for command in stop_commands) == 1
    assert sum("cleanup-cache.py" in command for command in stop_commands) == 1
    strategy = (repo / f"agent/{provider}/context-strategy.md").read_text()
    assert "context --task" in strategy and "accept-semantic" in strategy
    policy = json.loads(
        (repo / f"vibe_scripts/{provider}/memory-policy.json").read_text()
    )
    if provider == "claude":
        assert policy == {
            "_managed": "ClaudeHelper-Version: project-memory-v1",
            "hooks": False,
            "crg": False,
            "graphify": False,
        }
    assert not (home / ".codex/config.toml").exists()

    instructions = (
        repo / ("AGENTS.md" if provider == "codex" else "CLAUDE.md")
    ).read_text()
    docs = repo / f"agent/{provider}"
    procedures = (docs / "procedures.md").read_text()
    assert f"read `/agent/{provider}/procedures.md`" in instructions.lower()
    assert "git-add.txt" in procedures
    assert "Manifest review is mandatory" in procedures
    assert "Do not remove validation" in instructions
    usage = (docs / "usage.md").read_text()
    assert "@PROVIDER@" not in usage
    assert f"vibe_scripts/{provider}/project-memory.py usage" in usage
    assert len(json.loads((docs / "evaluation.json").read_text())["cases"]) == 12
    assert (
        "def usage_report("
        in (repo / f"vibe_scripts/{provider}/project-memory.py").read_text()
    )

    # A later managed refresh must preserve custom docs and global model choices.
    custom = usage + "\nUser-owned workflow guidance\n"
    (docs / "usage.md").write_text(custom)
    global_config = home / ".codex/config.toml"
    global_config.parent.mkdir(exist_ok=True)
    model_config = 'model = "user-selected-model"\n'
    global_config.write_text(model_config)
    refreshed = subprocess.run(
        command + ["--refresh-managed"],
        check=False,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert refreshed.returncode == 0, (
        refreshed.stdout[-4000:] + refreshed.stderr[-2000:]
    )
    assert (docs / "usage.md").read_text() == custom
    assert global_config.read_text() == model_config


@pytest.mark.parametrize("custom", [False, True])
def test_claude_memory_cleanup(tmp_path, custom):
    repo, home, binaries = (tmp_path / name for name in ("repo", "home", "bin"))
    for path in (repo, home, binaries):
        path.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    npx = binaries / "npx"
    npx.write_text("#!/bin/sh\nexit 0\n")
    npx.chmod(0o755)
    env = os.environ | {"HOME": str(home), "PATH": f"{binaries}:{os.environ['PATH']}"}
    command = ["bash", str(ROOT / "ClaudeHelper/install.sh"), "--repo", str(repo)]
    installed = subprocess.run(
        command
        + [
            "--repo-only",
            "--no-humanizer",
            "--no-omniroute",
            "--no-rtk",
            "--no-graphify",
            "--no-ponytail",
            "--no-crg",
            "--no-context7",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    settings = repo / ".claude/settings.local.json"
    if custom:
        document = json.loads(settings.read_text())
        document["hooks"]["Stop"].append(
            {"hooks": [{"type": "command", "command": "echo custom"}]}
        )
        settings.write_text(json.dumps(document))
    before = settings.read_bytes()
    cleaned = subprocess.run(
        command + ["--cleanup", "--yes"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert cleaned.returncode == 0, cleaned.stdout + cleaned.stderr
    if custom:
        assert settings.read_bytes() == before
    else:
        assert not settings.exists()


def test_live_graphify_deletion_and_semantic_marker(repo):
    if not shutil.which("graphify"):
        pytest.skip("Graphify not installed")
    INSTALL.install(repo, "codex", hooks=False, crg=False)
    (repo / "README.md").write_text("Durable tracking design")
    request = args("refresh")
    request.architecture = True
    value = MEMORY.handle(repo, request)
    assert value["freshness"]["graphify"]["status"] == "current", value
    assert "track" in MEMORY.backend_path(repo, "graphify").read_text()
    (repo / "tracking.py").write_text("def track():\n    return 2\n")
    value = MEMORY.handle(repo, request)
    assert value["freshness"]["graphify"]["status"] == "current", value
    (repo / "tracking.py").write_text("def replacement():\n    return 2\n")
    value = MEMORY.handle(repo, request)
    graph = json.loads(MEMORY.backend_path(repo, "graphify").read_text())
    labels = [node.get("label") for node in graph["nodes"]]
    assert "track()" not in labels and "replacement()" in labels
    assert value["freshness"]["semantic"]["status"] == "needs review"
    (repo / "tracking.py").unlink()
    value = MEMORY.handle(repo, request)
    assert value["freshness"]["graphify"]["status"] == "current", value
    graph = json.loads(MEMORY.backend_path(repo, "graphify").read_text())
    assert not graph["nodes"]


def test_graphify_pruning_preserves_reviewed_docs_and_removes_feedback(repo):
    (repo / "README.md").write_text("Durable")
    output = repo / "graphify-out"
    output.mkdir()
    graph = {
        "nodes": [
            {"id": "doc", "source_file": "README.md", "file_type": "document"},
            {
                "id": "generated",
                "source_file": "graphify-out/GRAPH_REPORT.md",
                "file_type": "document",
            },
            {"id": "deleted", "source_file": "gone.py", "file_type": "code"},
        ],
        "links": [{"source": "doc", "target": "deleted"}],
        "hyperedges": [{"nodes": ["doc"]}, {"nodes": ["doc", "deleted"]}],
    }
    graph["graph"] = {"hyperedges": graph["hyperedges"]}
    MEMORY.write_json(output / "graph.json", graph)
    MEMORY.prune_graphify(repo, MEMORY.snapshot(repo))
    result = json.loads((output / "graph.json").read_text())
    assert [node["id"] for node in result["nodes"]] == ["doc"]
    assert result["links"] == []
    assert result["hyperedges"] == [{"nodes": ["doc"]}]
    assert result["graph"]["hyperedges"] == [{"nodes": ["doc"]}]


def test_mixed_legacy_group_preserves_metadata_and_custom_hook(repo):
    path = repo / ".claude/settings.local.json"
    path.parent.mkdir()
    custom = {"type": "command", "command": "echo user graph update"}
    group = {
        "matcher": "Read",
        "customMetadata": "preserve",
        "hooks": [
            custom,
            {"type": "command", "command": min(INSTALL.legacy_commands(repo))},
        ],
    }
    path.write_text(json.dumps({"hooks": {"PreToolUse": [group]}}))
    INSTALL.configure(path, repo, "claude")
    value = json.loads(path.read_text())
    assert value["hooks"]["PreToolUse"][0] == dict(group, hooks=[custom])


def test_installer_refuses_symlink_parent_and_preserves_custom_exclusions(
    repo, tmp_path
):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (repo / ".codex").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="leaves repository"):
        INSTALL.install(repo, "codex")
    assert not list(outside.iterdir())
    (repo / ".codex").unlink()
    (repo / ".graphifyignore").write_text("private-docs/\n")
    INSTALL.install(repo, "codex", hooks=False)
    first = (repo / ".graphifyignore").read_text()
    INSTALL.install(repo, "codex", hooks=False)
    assert (repo / ".graphifyignore").read_text() == first
    assert first.startswith("private-docs/")


def test_snapshot_uses_metadata_without_reading_file_contents(repo, monkeypatch):
    path = repo / "large.txt"
    path.write_bytes(b"x" * 131072)
    before = MEMORY.snapshot(repo)

    def no_read(*args, **kwargs):
        raise AssertionError("snapshot must not read source contents")

    monkeypatch.setattr(Path, "read_bytes", no_read)
    monkeypatch.setattr(Path, "open", no_read)
    assert MEMORY.snapshot(repo) == before
    path.touch()
    assert MEMORY.snapshot(repo)["large.txt"] != before["large.txt"]


def test_failed_backend_exit_zero_and_mid_update_edits_remain_pending(
    repo, monkeypatch
):
    calls = backend(monkeypatch, repo)
    MEMORY.handle(repo, args("refresh"))
    original = MEMORY.run
    (repo / "tracking.py").write_text("def changed(): pass\n")

    def run(root, command, timeout=30):
        return (
            original(root, command, timeout)
            if command[0] == "git"
            else "nothing updated"
        )

    monkeypatch.setattr(MEMORY, "run", run)
    value = MEMORY.handle(repo, args("refresh"))
    assert value["freshness"]["crg"]["status"] == "pending"
    assert len(calls) == 1

    def racing(root, command, timeout=30):
        result = original(root, command, timeout)
        if command[0] != "git":
            (repo / "tracking.py").write_text("def raced(): pass\n")
        return result

    monkeypatch.setattr(MEMORY, "run", racing)
    value = MEMORY.handle(repo, args("refresh"))
    assert "files changed during refresh" in value["freshness"]["crg"]["error"]


def test_live_crg_updates_reverts_and_deletions(repo):
    if not shutil.which("code-review-graph"):
        pytest.skip("code-review-graph unavailable")
    INSTALL.install(repo, "codex", hooks=False, graphify=False)
    value = MEMORY.handle(repo, args("refresh"))
    assert value["freshness"]["crg"]["status"] == "current", value

    (repo / "tracking.py").write_text("def replacement(): return 2\n")
    value = MEMORY.handle(repo, args("refresh"))
    assert value["freshness"]["crg"]["status"] == "current", value
    output = MEMORY.run(
        repo,
        [
            "code-review-graph",
            "query",
            "file_summary",
            "tracking.py",
            "--repo",
            str(repo),
        ],
    )
    assert "replacement" in output
    (repo / "tracking.py").unlink()
    value = MEMORY.handle(repo, args("refresh"))
    assert value["freshness"]["crg"]["status"] == "current", value


def test_graph_refresh_timeouts(repo, monkeypatch):
    backend(monkeypatch, repo)
    refresh = MEMORY.run
    timeouts = []

    def run(root, command, timeout=30):
        if command[0] != "git":
            timeouts.append(timeout)
        return refresh(root, command, timeout)

    monkeypatch.setattr(MEMORY, "run", run)
    request = args("refresh")
    request.architecture = True
    MEMORY.handle(repo, request)
    assert timeouts == [80, 80]


def test_hook_budget_and_deadline_restoration(repo, monkeypatch):
    clock = [0]
    monkeypatch.setattr(MEMORY.time, "monotonic", lambda: clock[0])
    previous = MEMORY.DEADLINE

    def work(*_args):
        assert MEMORY.remaining(200) == 110
        clock[0] = 80
        assert MEMORY.remaining(80) == 30
        clock[0] = 111
        MEMORY.remaining(1)

    monkeypatch.setattr(MEMORY, "handle_locked", work)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        MEMORY.handle(repo, args("hook"))
    assert MEMORY.DEADLINE == previous


def test_timeout_permission_fallback_and_total_budget(repo, monkeypatch):
    def denied(*_args):
        raise PermissionError("group signal denied")

    monkeypatch.setattr(MEMORY.os, "killpg", denied)
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        MEMORY.run(
            repo, [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05
        )
    assert time.monotonic() - start < 3
    monkeypatch.setattr(MEMORY, "DEADLINE", time.monotonic() - 1)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        MEMORY.run(repo, ["git", "status", "--short"])


def test_hook_malformed_state_and_missing_tool_never_block(repo):
    path = repo / ".cache/project-memory/state.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "templates/vibe_scripts/project-memory.py"),
            "hook",
            "--root",
            str(repo),
        ],
        input=json.dumps({"hook_event_name": "Stop"}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    value = json.loads(result.stdout)
    assert "unavailable" in value["systemMessage"]
    assert "decision" not in value and "continue" not in value
