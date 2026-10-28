"""Compatibility coverage for Graphify output directories and their consumers."""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_project_memory import INSTALL, MEMORY, args, backend

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracking.py").write_text("def track():\n    return 1\n")
    return tmp_path


@pytest.mark.parametrize("directory", [".graphify", "graphify-out"])
def test_graphify_lifecycle(repo, monkeypatch, directory):
    calls = backend(monkeypatch, repo)
    real_run = MEMORY.run
    output = repo / directory
    output.mkdir()
    graph = output / "graph.json"
    graph.write_text('{"nodes": [], "links": []}')
    (repo / "README.md").write_text("Design evidence")
    INSTALL.install(repo, "codex", crg=False)

    def run(root, command, timeout=30):
        if command[0] == "git":
            return real_run(root, command, timeout)
        calls.append(command)
        if command[1] == "update":
            graph.write_text(json.dumps({"nodes": [], "links": [], "run": len(calls)}))
        return "updated"

    monkeypatch.setattr(MEMORY, "run", run)
    request = args("refresh")
    request.architecture = True
    value = MEMORY.handle(repo, request)
    assert value["freshness"]["graphify"]["status"] == "current"
    assert (output / "needs_update").exists()
    reviewed = args("accept-semantic")
    reviewed.snapshot = value["snapshot"]
    reviewed.evidence = "README reviewed"
    MEMORY.handle(repo, reviewed)
    assert not (output / "needs_update").exists()
    request = args("context")
    request.architecture = True
    MEMORY.handle(repo, request)
    assert calls[-1][-2:] == ["--graph", str(graph)]
    assert not any(
        name.startswith((".graphify/", "graphify-out/"))
        for name in MEMORY.snapshot(repo)
    )
    assert ".graphify/" in (repo / ".graphifyignore").read_text()
    assert "graphify-out/" in (repo / ".graphifyignore").read_text()


def test_current_output_preferred_without_hiding_legacy_graph(repo):
    current = repo / ".graphify"
    current.mkdir()
    legacy = repo / "graphify-out/graph.json"
    legacy.parent.mkdir()
    legacy.write_text('{"nodes": []}')
    assert MEMORY.backend_path(repo, "graphify") == legacy
    (current / "graph.json").write_text('{"nodes": []}')
    assert MEMORY.backend_path(repo, "graphify") == current / "graph.json"


def test_refresh_discovers_new_output_directory(repo, monkeypatch):
    legacy = repo / "graphify-out/graph.json"
    legacy.parent.mkdir()
    legacy.write_text('{"nodes": [], "links": []}')
    original = legacy.read_bytes()
    INSTALL.install(repo, "codex", crg=False)
    backend(monkeypatch, repo)
    original_index = MEMORY.index_backend

    def index(root, key, current):
        (root / ".graphify").mkdir()
        MEMORY.write_json(root / ".graphify/graph.json", {"nodes": [], "links": []})
        return original_index(root, key, current)

    monkeypatch.setattr(MEMORY, "index_backend", index)
    request = args("refresh")
    request.architecture = True
    value = MEMORY.handle(repo, request)
    assert value["freshness"]["graphify"]["status"] == "current"
    assert legacy.read_bytes() == original
    assert (repo / ".graphify/needs_update").exists()


def test_legacy_writer_cannot_make_current_graph_fresh_by_pruning(repo, monkeypatch):
    current = repo / ".graphify/graph.json"
    MEMORY.write_json(
        current, {"nodes": [{"id": "stale", "source_file": "deleted.py"}], "links": []}
    )
    original = current.read_bytes()
    INSTALL.install(repo, "codex", crg=False)

    def incomplete(root, key, current):
        MEMORY.write_json(root / "graphify-out/graph.json", {"nodes": [], "links": []})
        return {
            "indexed": [],
            "unsupported": [],
            "excluded": [],
            "failed": {},
            "missing": ["tracking.py"],
            "log": "fixture.log",
        }

    monkeypatch.setattr(MEMORY, "index_backend", incomplete)
    request = args("refresh")
    request.architecture = True
    value = MEMORY.handle(repo, request)
    assert value["freshness"]["graphify"]["status"] == "pending"
    assert "Incomplete graphify coverage" in value["freshness"]["graphify"]["error"]
    assert current.read_bytes() == original


@pytest.mark.parametrize("directory", [".graphify", "graphify-out"])
def test_pruning_updates_selected_report_only(repo, directory):
    output = repo / directory
    output.mkdir()
    MEMORY.write_json(
        output / "graph.json",
        {
            "nodes": [
                {"id": "generated", "source_file": f"{directory}/GRAPH_REPORT.md"},
                {"id": "deleted", "source_file": "deleted.py"},
            ],
            "links": [],
        },
    )
    (output / "GRAPH_REPORT.md").write_text("stale")
    MEMORY.prune_graphify(repo, MEMORY.snapshot(repo))
    assert not json.loads((output / "graph.json").read_text())["nodes"]
    assert "Regenerate" in (output / "GRAPH_REPORT.md").read_text()


@pytest.mark.parametrize("directory", [".graphify", "graphify-out"])
@pytest.mark.parametrize("name", ["graph.json", "needs_update", "GRAPH_REPORT.md"])
def test_output_symlink_escape_is_rejected(repo, tmp_path, directory, name):
    outside = tmp_path.parent / (tmp_path.name + "-external")
    outside.write_text("preserve")
    output = repo / directory
    output.mkdir()
    (output / name).symlink_to(outside)
    with pytest.raises(ValueError, match="inside the repository"):
        MEMORY.handle(repo, args())
    assert outside.read_text() == "preserve"


def test_graphify_command_uses_selected_directory(repo, monkeypatch):
    (repo / ".graphify").mkdir()
    executable = repo / "bin/graphify"
    executable.parent.mkdir()
    executable.write_text('#!/bin/sh\nprintf "%s" "$GRAPHIFY_OUT"\n')
    executable.chmod(0o755)
    monkeypatch.delenv("GRAPHIFY_OUT", raising=False)
    assert MEMORY.run(repo, [str(executable)]) == str(repo / ".graphify")
    assert "GRAPHIFY_OUT" not in os.environ


@pytest.mark.parametrize("provider", ["shared", "MacOS", "WSL"])
@pytest.mark.parametrize(
    "directories", [(".graphify",), ("graphify-out",), (".graphify", "graphify-out")]
)
def test_wiki_readers_use_actual_report_path(tmp_path, provider, directories):
    if provider == "shared":
        source = (ROOT / "templates/vibe_scripts/update-codebase-wiki.py").read_text()
    else:
        installer = (ROOT / f"CodexHelper/{provider}/part1.sh").read_text()
        start = installer.index('"""Draft concise codebase-wiki')
        source = installer[start : installer.index("\nEOF_WIKI_SCRIPT", start)]
    script = tmp_path / "vibe_scripts/codex/update-codebase-wiki.py"
    script.parent.mkdir(parents=True)
    script.write_text(source)
    for directory in directories:
        output = tmp_path / directory
        output.mkdir()
        (output / "GRAPH_REPORT.md").write_text(
            f"## Summary\n- evidence from {directory}\n"
        )
    completed = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert f"{directories[0]}/GRAPH_REPORT.md" in completed.stdout
    assert f"evidence from {directories[0]}" in completed.stdout
    if len(directories) == 2:
        assert "evidence from graphify-out" not in completed.stdout


def test_edited_checker_excludes_both_generated_layouts():
    path = ROOT / "templates/vibe_scripts/agent-check-edited.py"
    spec = importlib.util.spec_from_file_location("checker", path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert {".graphify", "graphify-out"} <= checker.EXCLUDED_DIRS


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize("stage", ["part1", "part2"])
def test_codex_detection_ignores_generated_graphs(tmp_path, platform, stage):
    source = (ROOT / f"CodexHelper/{platform}/{stage}.sh").read_text()
    function = re.search(
        r"^detect_file_signals\(\) \{\n.*?^\}", source, re.MULTILINE | re.DOTALL
    )[0]
    for directory in (".graphify", "graphify-out"):
        output = tmp_path / directory
        output.mkdir()
        for name in ("generated.py", "generated.sh", "GRAPH_REPORT.md", "package.json"):
            (output / name).touch()
    script = (
        function
        + '\ndetect_file_signals\nprintf "%s %s %s %s" "$PY_FILES" "$SHELL_FILES" "$MARKDOWN_FILES" "$NODE_MANIFESTS"\n'
    )
    for has_source in (False, True):
        if has_source:
            (tmp_path / "real.py").touch()
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout == ("1 0 0 0" if has_source else "0 0 0 0")
