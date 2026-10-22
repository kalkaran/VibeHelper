"""Codex installers reconcile integration config without losing hook definitions."""

import importlib.util
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = ROOT / "CodexHelper/templates/normalize-codex-config.py"
SPEC = importlib.util.spec_from_file_location("normalize_codex_config", NORMALIZER)
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)
HOOKS = {
    "PostToolUse": [
        {
            "matcher": "Edit|Write",
            "hooks": [
                {
                    "type": "command",
                    "command": "echo 'custom hook'",
                    "timeout": 5,
                    "async": True,
                    "statusMessage": "Keep this hook",
                }
            ],
        }
    ],
    "Stop": [{"hooks": [{"type": "command", "command": "echo stop", "timeout": 30}]}],
}


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def function(script, name):
    start = script.index(name + "() {")
    following = re.search(r"\n[a-z_]+\(\) \{", script[start + 1 :])
    return script[start : start + 1 + following.start()]


def assert_quality_recovery(agents, procedures):
    assert "read `/agent/codex/procedures.md`" in agents
    agents = procedures
    guidance = agents.split("Quality command recovery:\n", 1)[1].split(
        "Before final answer:", 1
    )[0]
    replacements = {
        "edited": "ruff|biome|black|isort|prettier|shfmt|gofmt|phpcbf|go fmt|cargo fmt|dotnet format|make format|make fix|make format-ai",
        "lint": "eslint|pylint|htmlhint|markdownlint|markdownlint-cli2|shellcheck|phpcs|golangci-lint|staticcheck|php -l|go vet|cargo clippy|make lint",
        "typecheck": "mypy|pyright|phpstan|phpstan.phar|php vendor/bin/phpstan|php tools/phpstan.phar|cargo check|dotnet build|make typecheck",
        "test": "pytest|phpunit|go test|cargo test|dotnet test|make test",
        "verify": "make verify",
    }
    for target, commands in replacements.items():
        row = next(
            line
            for line in guidance.splitlines()
            if line.endswith(f"| `make -f vibe_scripts/codex/Makefile {target}-ai` |")
        )
        for command in commands.split("|"):
            assert f"`{command}`" in row
    for requirement in (
        "immediately retry",
        "inspect its output and exit status",
        "A blocked attempt is not verification",
        "does not replace requested full-repository coverage",
        "Do not evade a block",
        "incomplete verification",
        "`./vibe_scripts/codex/agent-verify.sh`",
        "`make -f vibe_scripts/codex/Makefile wiki-ai`",
        "`make -f vibe_scripts/codex/Makefile skills-check`",
        "`make -f vibe_scripts/codex/Makefile skills-update`",
    ):
        assert requirement in guidance


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_installer_upgrade(tmp_path, platform):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    home.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    write(repo / ".agents/skills/unlazy/SKILL.md", "# Unlazy\n")
    npx = tmp_path / "bin/npx"
    write(npx, "#!/bin/sh\nexit 0\n")
    npx.chmod(0o755)
    config = repo / ".codex/config.toml"
    hooks = repo / ".codex/hooks.json"
    user_config = home / ".codex/config.toml"
    user_hooks = home / ".codex/hooks.json"
    original_user = '[mcp_servers.context7]\ntype = "http"\nurl = "https://mcp.context7.com/mcp/oauth"\n'
    write(user_config, original_user)
    write(user_hooks, json.dumps({"hooks": HOOKS}))
    write(hooks, json.dumps({"hooks": HOOKS}))
    env = os.environ | {
        "HOME": str(home),
        "CODEX_CONFIG": str(user_config),
        "PATH": f"{npx.parent}:{os.environ['PATH']}",
    }

    def install(*options, success=True):
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "CodexHelper" / platform / "part1.sh"),
                "--repo-only",
                "--yes",
                "--no-humanizer",
                "--no-rtk-hook",
                "--no-crg-build",
                "--no-apply-codex-config",
                *options,
            ],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) == success, result.stdout + result.stderr

    install("--dry-run")
    assert not config.exists() and hooks.exists()
    install()
    agents = repo / "AGENTS.md"
    assert_quality_recovery(
        agents.read_text(), (repo / "agent/codex/procedures.md").read_text()
    )
    parsed = tomllib.loads(config.read_text())
    for event, groups in HOOKS.items():
        assert all(group in parsed["hooks"][event] for group in groups)
    pre_hooks = parsed["hooks"]["PreToolUse"]
    assert len(pre_hooks) == 1
    commands = [hook["command"] for group in pre_hooks for hook in group["hooks"]]
    assert sum("pre_tool_use_policy.py" in command for command in commands) == 1
    assert sum("project-memory.py" in command for command in commands) == 0
    assert not hooks.exists()
    assert list(hooks.parent.glob("hooks.json.bak.*"))
    installed = config.read_bytes()
    previous_agents = (
        "<!-- CodexHelper-Version: previous -->\n# Keep project instructions\n"
    )
    agents.write_text(previous_agents)
    install()
    assert config.read_bytes() == installed
    assert agents.read_text() == previous_agents
    install("--force", "--dry-run")
    assert config.read_bytes() == installed
    assert agents.read_text() == previous_agents
    install("--force")
    assert tomllib.loads(config.read_text()) == parsed
    assert_quality_recovery(
        agents.read_text(), (repo / "agent/codex/procedures.md").read_text()
    )
    assert any(
        path.read_text() == previous_agents for path in repo.glob("AGENTS.md.bak.*")
    )
    # Integration installers may recreate hooks.json on a later run.
    write(hooks, json.dumps({"description": "Integration", "hooks": HOOKS}))
    install("--force")
    assert tomllib.loads(config.read_text()) == parsed
    assert not hooks.exists()
    assert user_config.read_text() == original_user and user_hooks.exists()
    write(hooks, "{broken json")
    before = config.read_bytes()
    install("--force", success=False)
    assert config.read_bytes() == before and hooks.read_text() == "{broken json"
    install("--no-codex-hooks")
    assert config.read_bytes() == before and hooks.exists()

    # Existing global helper upgrades need no new registration or config write.
    hooks.unlink()
    install("--force", "--apply-codex-config")
    global_hook = home / ".codex/hooks/ai_git_blocker.py"
    global_config_before = user_config.read_bytes()
    stale = "#!/usr/bin/env python3\n# CodexHelper-Version: previous\n# old policy\n"
    global_hook.write_text(stale)
    install("--force", "--dry-run")
    assert global_hook.read_text() == stale
    install("--force")
    global_policy = global_hook.read_text()
    assert "def mutation_reason" in global_policy
    assert "def quality_violation" not in global_policy
    assert "git-policy-command-v1" in global_policy
    assert user_config.read_bytes() == global_config_before
    assert any(
        p.read_text() == stale
        for p in global_hook.parent.glob("ai_git_blocker.py.bak.*")
    )
    global_hook.write_text("# User-owned hook\n")
    install("--force")
    assert global_hook.read_text() == "# User-owned hook\n"
    global_hook.unlink()
    install("--force")
    assert not global_hook.exists()


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize("already_configured", [False, True])
def test_context7_setup_and_existing_path(tmp_path, platform, already_configured):
    script = (ROOT / "CodexHelper" / platform / "part1.sh").read_text()
    config = tmp_path / "config.toml"
    original = (
        'model = "keep"\n[mcp_servers.context7]\ntype = "http"\n'
        'url = "https://mcp.context7.com/mcp/oauth"\n'
        '[mcp_servers.context7.http_headers]\nX-Custom = "keep"\n'
    )
    write(config, original)
    # Exercise the real setup orchestration with external installers stubbed.
    harness = (
        "\n".join(
            function(script, name)
            for name in (
                "normalize_codex_config",
                "normalize_context7_config",
                "context7_codex_configured",
                "context7_codex_login",
                "context7_codex_check",
                "install_global_tools",
            )
        )
        + r"""
log() { :; }
warn() { :; }
record_install_ok() { :; }
record_install_skipped() { :; }
record_install_failed() { echo "$*" >&2; exit 1; }
have() { [[ "$1" == npx || "$1" == codex ]]; }
have_cli() { return 1; }
codex_plugin_installed() { return 0; }
codex_plugin_marketplace_configured() { return 0; }
patch_codex_ponytail_hooks() { :; }
code_review_graph_version() { :; }
install_with_uv_or_pipx() { :; }
install_graphify_skill() { :; }
require_cli_available() { :; }
has_source_file() { return 1; }
setup_chrome_mcp() { :; }
setup_humanizer() { :; }
setup_unlazy() { :; }
setup_impeccable() { :; }
choose_codex_config_target() { printf '%s\n' "$TEST_CONFIG"; }
confirm() { return 0; }
probe_count=0
python3() {
    if [[ "$1" == "$HELPER_ROOT/templates/check-context7.py" ]]; then
        probe_count=$((probe_count + 1))
        [[ "$probe_count" -gt 1 ]] && return 0
        return 10
    fi
    command python3 "$@"
}
codex() {
    if [[ "$1 $2" == "mcp get" ]]; then
        [[ "$ALREADY_CONFIGURED" == 1 ]] || return 1
        printf 'enabled: true\nurl: https://mcp.context7.com/mcp/oauth\n'
    fi
}
run() {
    if [[ "$1" == npx ]]; then
        printf '\n' >> "$TEST_CONFIG"
        cp "$ORIGINAL" "$TEST_CONFIG"
    elif [[ "$2 $3 $4" == "codex mcp login" ]]; then
        python3 -c 'import tomllib,os; assert "type" not in tomllib.load(open(os.environ["TEST_CONFIG"],"rb"))["mcp_servers"]["context7"]'
        printf 'login verified\n'
    fi
}
SKIP_GLOBAL=0 CREATE_CODEX_HOOKS=0 CREATE_RTK_HOOK=0 FORCE=0 DRY_RUN=0
FRESH_INSTALL=0 CONTEXT7_EXPLICIT=1 RUN_CONTEXT7=1 YES=1 WITH_LLM_COUNCIL=0 RUN_ADHD=0
install_global_tools
"""
    )
    conventional = tmp_path / ".codex/config.toml"
    write(conventional, original)
    snapshot = tmp_path / "original.toml"
    write(snapshot, original)
    result = subprocess.run(
        ["bash", "-euc", harness],
        cwd=tmp_path,
        env=os.environ
        | {
            "HOME": str(tmp_path),
            "TEST_CONFIG": str(config),
            "ORIGINAL": str(snapshot),
            "HELPER_ROOT": str(ROOT / "CodexHelper"),
            "ALREADY_CONFIGURED": str(int(already_configured)),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "login verified" in result.stdout
    expected = tomllib.loads(original)
    del expected["mcp_servers"]["context7"]["type"]
    assert tomllib.loads(config.read_text()) == expected
    assert tomllib.loads(conventional.read_text()) == expected


def test_normalizer_preserves_and_rejects_invalid_input(tmp_path):
    config, hooks = tmp_path / "config.toml", tmp_path / "hooks.json"
    original = (
        'model = "keep"\n[mcp_servers.context7]\ntype = "http"\nurl = "https://example.test"\n'
        '[[hooks.PreToolUse]]\nmatcher = "Bash"\nhooks = [{type = "command", command = "guard"}]\n'
        '[hooks.state."existing"]\ntrusted_hash = "keep"\nenabled = false\n'
    )
    write(config, original)
    for invalid in [
        "{",
        '{"hooks": [], "other": true}',
        json.dumps({"hooks": {"Stop": [{"hooks": [{"command": None}]}]}}),
    ]:
        write(hooks, invalid)
        with pytest.raises(ValueError):
            CONFIG.normalize(config)
        assert config.read_text() == original and hooks.read_text() == invalid
        assert not list(tmp_path.glob("*.bak.*"))
    write(hooks, json.dumps({"hooks": HOOKS}))
    CONFIG.normalize(config)
    expected = tomllib.loads(original)
    del expected["mcp_servers"]["context7"]["type"]
    expected["hooks"].update(HOOKS)
    assert tomllib.loads(config.read_text()) == expected
    before = config.read_bytes()
    CONFIG.normalize(config)
    assert config.read_bytes() == before


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
@pytest.mark.parametrize("repo_only", [False, True])
def test_global_hooks_default_installer_flags(tmp_path, platform, repo_only):
    script = (ROOT / "CodexHelper" / platform / "part1.sh").read_text()
    config = tmp_path / ".codex/config.toml"
    hooks = config.with_name("hooks.json")
    original = '[hooks.state."existing"]\ntrusted_hash = "keep"\n'
    write(config, original)
    write(hooks, json.dumps({"hooks": HOOKS}))
    main = script[script.index("main() {") : script.rindex('\nmain "$@"')]
    harness = (
        function(script, "normalize_codex_config")
        + function(script, "repo_root").split("\n}\n", 1)[0]
        + "\n}\n"
        + function(script, "ensure_git_repository").split("\n}\n", 1)[0]
        + "\n}\n"
        + 'have() { command -v "$1" >/dev/null 2>&1; }\n'
        + main
        + "\n"
    )
    for name in (
        "ensure_existing_user_tool_paths",
        "bootstrap_prereqs",
        "check_prereqs",
        "install_global_tools",
        "install_omniroute",
        "create_agent_files",
        "create_session_logging_files",
        "ensure_cache_gitignored",
        "create_codebase_wiki_script",
        "create_skill_sync_script",
        "create_skills_lock",
        "create_ai_quality_wrapper_script",
        "create_edited_check_script",
        "create_verify_script",
        "create_quality_hook_script",
        "create_project_memory",
        "start_managed_lifecycle",
        "create_codex_hooks_templates",
        "apply_codex_hook_config",
        "run_final_checks",
        "print_install_summary",
        "print_quality_install_step",
        "print_next_steps",
        "log",
    ):
        harness += name + "() { :; }\n"
    harness += """
record_install_failed() { echo "$*" >&2; exit 1; }
choose_codex_config_target() { printf '%s/.codex/config.toml\\n' "$HOME"; }
CREATE_CODEX_HOOKS=1 CODEX_CONFIG_MODE=skip DRY_RUN=0 FINAL_EXIT_CODE=0
    INSTALLER_DIR="$HELPER_ROOT/MacOS"
    CODEXHELPER_UNIFIED_INSTALL=1
main
"""
    result = subprocess.run(
        ["bash", "-euc", harness],
        cwd=tmp_path,
        env=os.environ
        | {
            "HOME": str(tmp_path),
            "SKIP_GLOBAL": str(int(repo_only)),
            "HELPER_ROOT": str(ROOT / "CodexHelper"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if repo_only:
        assert config.read_text() == original and hooks.exists()
    else:
        expected = tomllib.loads(original)
        expected["hooks"].update(HOOKS)
        assert tomllib.loads(config.read_text()) == expected
        assert not hooks.exists()


@pytest.mark.parametrize("helper", ["CodexHelper", "ClaudeHelper"])
def test_existing_installer_smoke_suite(tmp_path, helper):
    home, binaries = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    binaries.mkdir()
    npx = binaries / "npx"
    npx.write_text("#!/bin/sh\nexit 0\n")
    npx.chmod(0o755)
    script = "install_test.sh" if helper == "CodexHelper" else "run.sh"
    result = subprocess.run(
        ["bash", str(ROOT / helper / "tests" / script)],
        cwd=ROOT,
        env=os.environ
        | {"HOME": str(home), "PATH": f"{binaries}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
