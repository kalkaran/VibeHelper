"""Default Fallow setup, advisory lint and provider delivery."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fallow_helper", ROOT / "templates/vibe_scripts/fallow.py"
)
FALLOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FALLOW)


@pytest.fixture
def project(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/tracking.ts").write_text("export const track = 1;\n")
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "fixture", "private": True})
    )
    (tmp_path / ".gitignore").write_text("node_modules/\n.cache/\n")
    return tmp_path


def fake_binary(project, dead=None, dupes=None, code=0):
    path = project / "node_modules/.bin/fallow"
    path.parent.mkdir(parents=True, exist_ok=True)
    outputs = {
        "dead-code": dead or {"kind": "dead-code", "total_issues": 0},
        "dupes": dupes or {"kind": "dupes", "total_clones": 0},
    }
    path.write_text(
        f"#!{sys.executable}\nimport sys,json\nfrom pathlib import Path\nPath('calls.log').open('a').write(json.dumps(sys.argv[1:])+'\\n')\nprint(json.dumps({outputs!r}[sys.argv[1]]))\nsys.exit({code})\n"
    )
    path.chmod(0o755)
    return path


def test_scoped_reports_are_advisory_and_keep_clone_partners(project, capsys):
    fake_binary(
        project,
        code=1,
        dead={
            "kind": "dead-code",
            "total_issues": 2,
            "unused_exports": [
                {"path": "src/tracking.ts", "name": "track"},
                {"path": "unrelated.ts", "name": "other"},
            ],
        },
        dupes={
            "kind": "dupes",
            "total_clones": 1,
            "clone_groups": [
                {"instances": [{"path": "src/tracking.ts"}, {"path": "partner.ts"}]}
            ],
        },
    )
    assert FALLOW.report(project, ["src"], False) == 0
    output = capsys.readouterr().out
    assert "track" in output and "partner.ts" in output and "unrelated.ts" not in output
    assert "passed" not in output and "reported 1 scoped items" in output
    calls = [
        json.loads(line) for line in (project / "calls.log").read_text().splitlines()
    ]
    assert calls == [
        [mode, "--format", "json", "--quiet"] for mode in ["dead-code", "dupes"]
    ]
    assert len(list((project / ".cache/ai-quality").glob("*.log"))) == 2


@pytest.mark.parametrize(
    "code,document", [(2, {"error": "config invalid"}), (0, {"kind": "wrong"})]
)
def test_analyzer_errors_are_not_success(project, capsys, code, document):
    fake_binary(project, dead=document, code=code)
    assert FALLOW.report(project, [], True) == 2
    assert "tool_failure" in capsys.readouterr().out


def test_empty_missing_and_outside_scope(project, capsys):
    assert FALLOW.report(project, [], True) == 2
    fake_binary(project)
    assert FALLOW.report(project, ["docs"], False) == 2
    assert not (project / "calls.log").exists()
    with pytest.raises(ValueError, match="leaves repository"):
        FALLOW.report(project, ["../other"], False)
    assert "not_checked" in capsys.readouterr().out


@pytest.mark.parametrize(
    "document",
    [
        {"kind": "dupes", "stats": []},
        {"kind": "dupes", "stats": {"clone_groups": 1}, "clone_groups": ["invalid"]},
        {"kind": "dupes", "total_clones": 0, "workspace_diagnostics": ["invalid"]},
    ],
)
def test_malformed_report_is_a_tool_failure(project, capsys, document):
    fake_binary(project, dupes=document)
    assert FALLOW.report(project, [], True) == 2
    assert "tool_failure" in capsys.readouterr().out


def test_nested_source_locations_and_no_repair_instructions(project):
    item = {
        "locations": [{"path": "src/tracking.ts", "line": 2}],
        "actions": [{"command": "fix"}],
    }
    assert list(FALLOW.finding_paths(item, project)) == ["src/tracking.ts"]
    assert "actions" not in FALLOW.brief(item)


def test_source_fragment_error_words_are_findings_not_tool_errors(project, capsys):
    fake_binary(
        project,
        dupes={
            "kind": "dupes",
            "stats": {"clone_groups": 1},
            "clone_groups": [
                {
                    "instances": [
                        {
                            "file": "src/tracking.ts",
                            "fragment": "throw new Error('permission denied')",
                        }
                    ]
                }
            ],
        },
    )
    result = FALLOW.sibling("ai-quality-wrapper").run_quality(
        [FALLOW.binary(project), "dupes", "--format", "json", "--quiet"],
        label="generic-wrapper-preserved",
        cwd=project,
    )
    assert result["status"] == "tool_failure"
    assert result["execution_error"] is None
    assert FALLOW.report(project, ["src"], False) == 0
    assert "tool_failure" not in capsys.readouterr().out


def test_execution_failure_with_native_report_still_fails(project, monkeypatch, capsys):
    fake_binary(project)
    quality = FALLOW.sibling("ai-quality-wrapper")
    original_run = quality.run_quality
    original_sibling = FALLOW.sibling

    def run(*args, **kwargs):
        result = original_run(*args, **kwargs)
        result["execution_error"] = "Unable to finish logging"
        result["status"] = "tool_failure"
        return result

    monkeypatch.setattr(quality, "run_quality", run)
    monkeypatch.setattr(
        FALLOW,
        "sibling",
        lambda name: (
            quality if name == "ai-quality-wrapper" else original_sibling(name)
        ),
    )
    assert FALLOW.report(project, [], True) == 2
    assert "tool_failure" in capsys.readouterr().out


def test_install_modes_preserve_project_files(project, monkeypatch, capsys):
    (project / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    original = {p.name: p.read_bytes() for p in project.iterdir() if p.is_file()}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command == ["node", "--version"]:
            return subprocess.CompletedProcess(command, 0, "v22.1.0\n", "")
        target = FALLOW.cache(project) / "node_modules/.bin/fallow"
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(FALLOW, "sources", lambda root: ["src/tracking.ts"])
    monkeypatch.setattr(FALLOW.shutil, "which", lambda name: name)
    monkeypatch.setattr(FALLOW.subprocess, "run", run)
    assert FALLOW.install(project, False, False) == 0
    assert FALLOW.install(project, True, True) == 0
    assert not calls
    assert FALLOW.install(project, True, False) == 0
    assert calls[1] == [
        "npm",
        "install",
        "--prefix",
        str(FALLOW.cache(project)),
        "--no-audit",
        "--no-fund",
        "--save-exact",
        "fallow@3.33.1",
    ]
    assert FALLOW.install(project, True, False) == 0
    assert len(calls) == 2
    assert {
        p.name: p.read_bytes() for p in project.iterdir() if p.is_file()
    } == original
    assert "analysis has not run" in capsys.readouterr().out


def test_existing_binary_nonjs_and_cache_escape(project, capsys):
    fake_binary(project)
    assert FALLOW.install(project, True, False) == 0
    (project / "src/tracking.ts").unlink()
    assert FALLOW.install(project, True, False) == 0
    assert "skipped" in capsys.readouterr().out
    (project / ".cache").symlink_to(project.parent)
    with pytest.raises(ValueError, match="cache leaves"):
        FALLOW.cache(project)


@pytest.mark.parametrize("node_version", ["v20.1.0", "invalid"])
def test_unsupported_node_does_not_install(project, monkeypatch, node_version):
    monkeypatch.setattr(FALLOW, "sources", lambda root: ["src/tracking.ts"])
    monkeypatch.setattr(FALLOW.shutil, "which", lambda name: name)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, node_version, "")

    monkeypatch.setattr(FALLOW.subprocess, "run", run)
    with pytest.raises(ValueError, match="requires Node"):
        FALLOW.install(project, True, False)
    assert calls == [["node", "--version"]]


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
def test_old_verifier_is_preserved_and_reported(project, provider):
    name = provider.removesuffix("Helper").lower()
    runtime = project / f"vibe_scripts/{name}"
    runtime.mkdir(parents=True)
    verifier = runtime / "agent-verify.sh"
    verifier.write_text("#!/bin/sh\n# user-preserved old verifier\nexit 0\n")
    before = verifier.read_bytes()
    command = [
        "bash",
        str(ROOT / provider / "MacOS/part2.sh"),
        "--wire",
    ]
    if provider == "CodexHelper":
        command += ["--no-install", "--yes", "--no-fix"]
    result = subprocess.run(
        command, cwd=project, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preserved custom/unowned runtime" in result.stdout
    assert verifier.read_bytes() == before
    assert not (project / ".cache/vibehelper-tools").exists()


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
def test_no_wire_skips_optional_install(project, provider):
    command = [
        "bash",
        str(ROOT / provider / "MacOS/part2.sh"),
    ]
    if provider == "CodexHelper":
        command += ["--no-install", "--no-wire", "--yes", "--no-fix"]
    result = subprocess.run(
        command, cwd=project, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (project / "vibe_scripts").exists()
    assert not (project / ".cache/vibehelper-tools").exists()


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_part2_default_delivery_rerun_and_advisory_lint(
    project, tmp_path, provider, platform
):
    fake_binary(project)
    (project / "check.sh").write_text("#!/usr/bin/env bash\ntrue\n")
    home = tmp_path / "home"
    home.mkdir()
    command = [
        "bash",
        str(ROOT / provider / platform / "part2.sh"),
        "--wire",
    ]
    if provider == "CodexHelper":
        command += ["--no-install", "--yes", "--no-fix"]
    env = os.environ | {"HOME": str(home)}
    for extra in ([], ["--refresh-managed"]):
        result = subprocess.run(
            command + extra,
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    name = provider.removesuffix("Helper").lower()
    runtime = project / f"vibe_scripts/{name}"
    assert (runtime / "fallow.py").exists()
    assert (
        f"python3 vibe_scripts/{name}/fallow.py lint"
        in (runtime / "Makefile").read_text()
    )
    result = subprocess.run(
        [str(runtime / "agent-verify.sh"), "fallow", "--files", "src"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "reported" in result.stdout

    # Normal lint uses only this session, even with other dirty sources/sessions.
    (project / "unrelated.ts").write_text("export const other = 1;\n")
    session = "fallow-default-lint-fixture"
    key = name + "-" + hashlib.sha256(session.encode()).hexdigest()[:24]
    state = project / ".cache/quality-sessions"
    state.mkdir(parents=True, exist_ok=True)
    (state / (key + ".json")).write_text(json.dumps({"paths": ["src/tracking.ts"]}))
    (state / "other-session.json").write_text(json.dumps({"paths": ["unrelated.ts"]}))
    env.update({"CODEX_THREAD_ID": session, "CLAUDE_SESSION_ID": session})
    fake_binary(
        project,
        code=1,
        dead={
            "kind": "dead-code",
            "total_issues": 2,
            "unused_exports": [
                {"path": "src/tracking.ts", "name": "track"},
                {"path": "unrelated.ts", "name": "other"},
            ],
        },
    )
    lint_command = ["make", "-f", f"vibe_scripts/{name}/Makefile", "lint-ai"]
    result = subprocess.run(
        lint_command,
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "reported 1 scoped items" in result.stdout
    assert '"name": "other"' not in result.stdout
    fake_binary(project, code=2)
    result = subprocess.run(
        lint_command,
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "tool_failure" in result.stdout

    # Existing lint failures must still stop the recipe before advisory analysis.
    (project / "package.json").write_text(json.dumps({"scripts": {"lint": "exit 7"}}))
    result = subprocess.run(
        command + ["--refresh-managed"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls_before = (project / "calls.log").read_bytes()
    result = subprocess.run(
        lint_command,
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert ("lint-node" if name == "codex" else "lint-npm") in result.stdout
    assert (project / "calls.log").read_bytes() == calls_before


def test_receipt_owned_runtime_refresh_and_custom_preservation(project):
    runtime = project / "vibe_scripts/codex"
    runtime.mkdir(parents=True)
    verifier = runtime / "agent-verify.sh"
    verifier.write_text("#!/bin/sh\n# old managed verifier\n")
    receipt = project / ".cache/vibehelper-install/codex.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "vibe_scripts/codex/agent-verify.sh": hashlib.sha256(
                    verifier.read_bytes()
                ).hexdigest()
            }
        )
    )
    FALLOW.prepare_runtime(project, "codex", True)
    assert "old managed" in verifier.read_text()
    FALLOW.prepare_runtime(project, "codex", False)
    assert "fallow.py" in verifier.read_text()
    assert list(runtime.glob("agent-verify.sh.bak.*"))
    verifier.write_text("#!/bin/sh\n# user customization\n")
    with pytest.raises(ValueError, match="custom/unowned"):
        FALLOW.prepare_runtime(project, "codex", False)
    assert "user customization" in verifier.read_text()


def test_nonjs_install_does_not_require_runtime_upgrade(project, capsys):
    (project / "src/tracking.ts").unlink()
    runtime = project / "vibe_scripts/codex"
    runtime.mkdir(parents=True)
    verifier = runtime / "agent-verify.sh"
    verifier.write_text("custom verifier")
    assert FALLOW.install(project, True, False, "codex") == 0
    assert verifier.read_text() == "custom verifier"
    assert FALLOW.lint(project) == 0
    output = capsys.readouterr().out
    assert "not_applicable" in output and "--files" not in output


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
def test_standard_rerun_refreshes_runtime_using_installers_receipt(
    project, tmp_path_factory, provider
):
    fake_binary(project)
    previous = tmp_path_factory.mktemp("previous-installer")
    platform = previous / provider / "MacOS"
    platform.mkdir(parents=True)
    shutil.copy2(ROOT / provider / "MacOS/part2.sh", platform / "part2.sh")
    shared = previous / "templates/vibe_scripts"
    shutil.copytree(ROOT / "templates/vibe_scripts", shared)
    old_verifier = shared / "agent-verify.sh"
    old_verifier.write_text(old_verifier.read_text() + "\n# Previous managed release\n")
    name = provider.removesuffix("Helper").lower()
    args = ["--wire"]
    if name == "codex":
        args += ["--no-install", "--yes", "--no-fix"]
    for installer in (previous, ROOT):
        result = subprocess.run(
            ["bash", str(installer / provider / "MacOS/part2.sh"), *args],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "custom/unowned" not in result.stdout
    runtime = project / f"vibe_scripts/{name}"
    verifier = runtime / "agent-verify.sh"
    assert (
        verifier.read_bytes()
        == (ROOT / "templates/vibe_scripts/agent-verify.sh").read_bytes()
    )
    assert list(runtime.glob("agent-verify.sh.bak.*"))
    receipt = json.loads(
        (project / f".cache/vibehelper-install/{name}.json").read_text()
    )
    assert (
        receipt[f"vibe_scripts/{name}/agent-verify.sh"]
        == hashlib.sha256(verifier.read_bytes()).hexdigest()
    )


def test_install_failure_is_advisory(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    monkeypatch.setattr(sys, "argv", ["fallow.py", "install", "--allow-install", "1"])
    monkeypatch.setattr(FALLOW.shutil, "which", lambda name: None)
    assert FALLOW.main() == 0
    assert "unavailable: Fallow installation requires Node" in capsys.readouterr().out
    assert not (project / ".cache/vibehelper-tools").exists()


@pytest.mark.parametrize(
    "state",
    [
        None,
        "{partial",
        {"paths": [3]},
        {"paths": ["../escape"]},
        {"paths": ["docs/notes.md"]},
        {"paths": ["src/tracking.ts"]},
    ],
)
def test_default_lint_unavailable_scope_and_tool_are_explicit(
    project, monkeypatch, capsys, state
):
    monkeypatch.setattr(
        FALLOW, "__file__", str(project / "vibe_scripts/codex/fallow.py")
    )
    # Preserve imports from the canonical helper while identifying the provider.
    modules = {
        name: import_helper(name)
        for name in ("agent-check-edited", "ai-quality-wrapper")
    }
    monkeypatch.setattr(FALLOW, "sibling", modules.__getitem__)
    session = "scope-fixture"
    monkeypatch.setenv("CODEX_THREAD_ID", session)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    if state is not None:
        key = "codex-" + hashlib.sha256(session.encode()).hexdigest()[:24]
        path = project / ".cache/quality-sessions" / (key + ".json")
        path.parent.mkdir(parents=True)
        path.write_text(state if isinstance(state, str) else json.dumps(state))
    assert FALLOW.lint(project) == 0
    output = capsys.readouterr().out
    assert "not_checked" in output or "unavailable" in output
    assert not (project / "calls.log").exists()


def import_helper(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "templates/vibe_scripts" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("provider", ["CodexHelper", "ClaudeHelper"])
def test_unified_default_setup_and_repo_only(project, provider):
    command = [
        "bash",
        str(ROOT / provider / "install.sh"),
        "--repo",
        str(project),
        "--repo-only",
        "--dry-run",
        "--no-humanizer",
        "--no-omniroute",
    ]
    if provider == "CodexHelper":
        command += ["--yes", "--no-fix", "--no-rtk-hook", "--no-crg-build"]
    else:
        command += [
            "--no-crg",
            "--no-graphify",
            "--no-rtk",
            "--no-context7",
            "--no-ponytail",
        ]
    result = subprocess.run(
        command, cwd=project, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[fallow] not installed: package changes disabled" in result.stdout
    assert not (project / ".cache/vibehelper-tools").exists()
    assert not (project / "vibe_scripts").exists()


def test_live_fallow_reports_preserve_sources_and_config(project, capsys):
    executable = ROOT / ".cache/fallow-test/node_modules/.bin/fallow"
    if not executable.is_file():
        pytest.skip("Optional real Fallow test installation is unavailable")
    local = project / "node_modules/.bin/fallow"
    local.parent.mkdir(parents=True)
    local.symlink_to(executable)
    (project / ".fallowrc.json").write_text(json.dumps({"entry": ["src/index.ts"]}))
    (project / "src/index.ts").write_text(
        "import {used} from './tracking'; console.log(used(3));\n"
    )
    body = (
        "export function used(input: number) {\n"
        + "\n".join(f"  input += {n};" for n in range(16))
        + "\n  return input;\n}\n"
    )
    (project / "src/tracking.ts").write_text(body + "export const unused = 1;\n")
    (project / "src/other.ts").write_text(body)
    preserved = {
        str(p): p.read_bytes()
        for p in [
            *project.glob("src/*"),
            project / ".fallowrc.json",
            project / "package.json",
        ]
    }
    assert FALLOW.report(project, ["src/tracking.ts"], False) == 0
    output = capsys.readouterr().out
    assert "unused" in output and "clone_groups" in output
    assert {p: Path(p).read_bytes() for p in preserved} == preserved
