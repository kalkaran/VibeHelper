"""Exercise installed provider paths without external installs or user configuration."""

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd, env, success=True):
    result = subprocess.run(
        args, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=90
    )
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def load_module(path):
    spec = importlib.util.spec_from_file_location("isolation_loaded_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def snapshot(path):
    return {
        str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize("order", [("codex", "claude"), ("claude", "codex")])
def test_provider_install_upgrade_and_cleanup(tmp_path, platform, order):
    repo, home, binaries = (
        tmp_path / name for name in ("project with spaces", "home", "bin")
    )
    for path in (repo, home, binaries):
        path.mkdir()
    write(
        binaries / "uname",
        '#!/bin/sh\nprintf "%s\\n" "'
        + ("Darwin" if platform == "MacOS" else "Linux")
        + '"\n',
    )
    (binaries / "uname").chmod(0o755)
    env = os.environ | {
        "HOME": str(home),
        "PATH": f"{binaries}:{Path(sys.executable).parent}:/usr/bin:/bin",
    }
    if platform == "WSL":
        env["WSL_DISTRO_NAME"] = "Ubuntu-test"
    for name in (
        "npx",
        "node",
        "shellcheck",
        "markdownlint-cli2",
        "semgrep",
        "ruff",
        "mypy",
        "pytest",
        "code-review-graph",
    ):
        write(binaries / name, "#!/bin/sh\nexit 0\n")
        (binaries / name).chmod(0o755)
    write(binaries / "shellcheck", "#!/bin/sh\nprintf '{\"comments\": []}\\n'\n")
    write(binaries / "pytest", '#!/bin/sh\nprintf "1 passed in 0.01s\\n"\n')
    write(
        binaries / "npm",
        '#!/bin/sh\nprintf "npm-check:%s\\n" "$*"\nexit "${CHECK_EXIT:-0}"\n',
    )
    (binaries / "npm").chmod(0o755)
    run(["git", "init", "-q"], repo, env)
    write(repo / ".agents/skills/unlazy/SKILL.md", "# Unlazy\n")
    write(
        repo / "package.json",
        json.dumps(
            {"scripts": {"lint": "exit 0", "test": "exit 0", "typecheck": "exit 0"}}
        ),
    )
    write(repo / "main.py", "print('example')\n")
    write(repo / "node_modules/.bin/biome", "#!/bin/sh\nexit 0\n")
    (repo / "node_modules/.bin/biome").chmod(0o755)
    write(repo / "README.md", "# Example\n")
    write(repo / ".cache/claude-edited-files.txt", "README.md\n")
    write(repo / ".cache/codex-edited-files.txt", "README.md\n")
    project_makefile = "# Project-owned targets\nverify-ai lint-ai test-ai:\n\t@echo WRONG-MAKEFILE >&2; exit 91\n"
    write(repo / "Makefile", project_makefile)
    legacy_script = "# legacy shared script retained for manual migration\n"
    write(repo / "vibe_scripts/agent-verify.sh", legacy_script)

    def install(provider, *options):
        helper = ROOT / ("CodexHelper" if provider == "codex" else "ClaudeHelper")
        args = [
            "bash",
            str(helper / platform / "part1.sh"),
            "--repo-only",
            "--no-humanizer",
        ]
        if provider == "codex":
            args += [
                "--yes",
                "--no-adhd",
                "--no-rtk-hook",
                "--no-crg-build",
                "--no-apply-codex-config",
            ]
        else:
            args += [
                "--no-rtk",
                "--no-graphify",
                "--no-ponytail",
                "--no-crg",
                "--no-context7",
                "--no-chrome-mcp",
                "--no-system-packages",
            ]
        run(args + list(options), repo, env)
        quality = ["bash", str(helper / platform / "part2.sh"), "--wire"]
        if provider == "codex":
            quality += ["--yes", "--no-install", "--no-fix"]
        run(quality + list(options), repo, env)

    install(order[0])
    first = snapshot(repo / "vibe_scripts" / order[0])
    first_workflow = snapshot(repo / "agent" / order[0])
    install(order[1])
    assert snapshot(repo / "vibe_scripts" / order[0]) == first
    assert snapshot(repo / "agent" / order[0]) == first_workflow
    assert (repo / "Makefile").read_text() == project_makefile
    assert (repo / "vibe_scripts/agent-verify.sh").read_text() == legacy_script

    for provider in order:
        scripts = repo / "vibe_scripts" / provider
        for name in (
            "agent-check-edited.py",
            "ai-quality-wrapper.py",
            "sync-skills.py",
            "update-codebase-wiki.py",
            "omniroute.py",
            "quality-hook.py",
        ):
            ast.parse((scripts / name).read_text())
        mf = (scripts / "Makefile").read_text()
        assert f"$(MAKE) -f vibe_scripts/{provider}/Makefile" in mf
        instructions = (
            repo / ("AGENTS.md" if provider == "codex" else "CLAUDE.md")
        ).read_text()
        assert f"make -f vibe_scripts/{provider}/Makefile edited-ai" in instructions
        assert f"/agent/{provider}/" in instructions
        assert f"python3 vibe_scripts/{provider}/omniroute.py ask" in instructions
        assert (repo / "agent" / provider / "omniroute.md").is_file()
        hook_name = "post_edit_check.py"
        hook_source = (repo / f".{provider}/hooks/{hook_name}").read_text()
        assert "quality-hook.py" in hook_source and provider in hook_source
        hook_config = (
            repo
            / (
                ".codex/config.toml"
                if provider == "codex"
                else ".claude/settings.local.json"
            )
        ).read_text()
        assert "SessionStart" in hook_config and "quality-hook.py" in hook_config
        # Verifier chooses its own root even from a subdirectory with spaces.
        nested = repo / "sub directory"
        nested.mkdir(exist_ok=True)
        result = run([str(scripts / "agent-verify.sh"), "verify"], nested, env)
        assert "WRONG-MAKEFILE" not in result.stdout + result.stderr
        result = run(
            [str(scripts / "agent-verify.sh"), "test"],
            nested,
            env | {"CHECK_EXIT": "7"},
            success=False,
        )
        assert result.returncode != 0 and "npm-check:" in result.stdout
        # The moved wiki and skills scripts must still resolve the project root.
        run(["make", "-f", f"vibe_scripts/{provider}/Makefile", "wiki-ai"], repo, env)
        module = load_module(scripts / "sync-skills.py")
        assert module.ROOT == repo
        for name in ("index.md", "architecture.md", "testing.md"):
            write(repo / "codebase-wiki" / name, f"# User memory: {name}\n")
        before = snapshot(scripts)
        install(provider, "--dry-run", "--force")
        assert snapshot(scripts) == before
        install(provider, "--force")
        assert (repo / "Makefile").read_text() == project_makefile
        for name in ("index.md", "architecture.md", "testing.md"):
            assert (
                repo / "codebase-wiki" / name
            ).read_text() == f"# User memory: {name}\n"
        run([str(scripts / "agent-verify.sh"), "lint"], repo, env)

    # Unified Claude coexistence upgrades refresh only Claude's owned runtime.
    codex_before = snapshot(repo / "vibe_scripts/codex")
    write(repo / "codebase-wiki/preserve.txt", "durable project knowledge\n")
    write(repo / "notes/preserve.txt", "user notes\n")
    checker = repo / "vibe_scripts/claude/agent-check-edited.py"
    checker.write_text(checker.read_text() + "\n# stale helper\n")
    run(
        [
            "bash",
            str(ROOT / "ClaudeHelper/install.sh"),
            "--force",
            "--repo-only",
            "--no-humanizer",
            "--no-rtk",
            "--no-graphify",
            "--no-ponytail",
            "--no-crg",
            "--no-context7",
            "--no-chrome-mcp",
            "--no-system-packages",
        ],
        repo,
        env,
    )
    assert "stale helper" not in checker.read_text()
    assert snapshot(repo / "vibe_scripts/codex") == codex_before
    assert (
        repo / "codebase-wiki/preserve.txt"
    ).read_text() == "durable project knowledge\n"
    assert (repo / "notes/preserve.txt").read_text() == "user notes\n"

    # Remove the first provider; the second must remain usable with all shared data.
    survivor = order[1]
    surviving_files = snapshot(repo / "vibe_scripts" / survivor)
    write(repo / "notes/preserve.txt", "user notes\n")
    write(repo / "codebase-wiki/preserve.txt", "user memory\n")
    write(repo / ".mcp.json", '{"mcpServers": {}}\n')
    helper = "CodexHelper" if order[0] == "codex" else "ClaudeHelper"
    custom = repo / "vibe_scripts" / order[0] / "user-owned.txt"
    write(custom, "preserve my custom helper\n")
    run(["bash", str(ROOT / helper / "install.sh"), "--cleanup", "--yes"], repo, env)
    assert custom.read_text() == "preserve my custom helper\n"
    assert not (custom.parent / "quality-hook.py").exists()
    assert snapshot(repo / "vibe_scripts" / survivor) == surviving_files
    for name in (
        "Makefile",
        "notes/preserve.txt",
        "codebase-wiki/preserve.txt",
        ".mcp.json",
        "skills-lock.json",
        "vibe_scripts/agent-verify.sh",
    ):
        assert (repo / name).exists(), name
    run([str(repo / "vibe_scripts" / survivor / "agent-verify.sh"), "test"], repo, env)


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_policy_allows_only_codex_makefile(tmp_path, platform):
    source = (ROOT / "CodexHelper" / platform / "part1.sh").read_text()
    payload = source.split("cat <<'EOF_PRE_HOOK'", 1)[1].split("\nEOF_PRE_HOOK", 1)[0]
    payload = payload.split("\ntry:\n    raw_payload", 1)[0]
    policy_file = tmp_path / "policy.py"
    policy_file.write_text(payload)
    violation = load_module(policy_file).quality_violation
    good = "make -f vibe_scripts/codex/Makefile lint-ai"
    assert not violation(good)
    for command in (
        "make lint-ai",
        "make -f vibe_scripts/claude/Makefile lint-ai",
        good + " -f alternate.mk",
        good + " -f vibe_scripts/codex/Makefile",
        good + " --eval=lint-ai:",
        good + " -C elsewhere",
        good + " PHPSTAN_LEVEL=0",
        "MAKEFLAGS=-fweak.mk " + good,
        "env " + good,
        good.replace("-f ", "--file="),
        "ruff check .",
        "./vibe_scripts/claude/agent-verify.sh verify",
        "python3 vibe_scripts/claude/agent-check-edited.py app.php",
        "bash vibe_scripts/claude/agent-verify.sh verify",
        "./vibe_scripts/agent-verify.sh verify",
    ):
        assert violation(command), command
