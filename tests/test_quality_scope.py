"""Task scope, preserved project checks and read-only quality route regressions."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "templates/vibe_scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = load("agent-check-edited")
HOOK = load("quality-hook")


@pytest.fixture
def project(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tools = tmp_path / "bin"
    tools.mkdir()
    # Real subprocesses record argv and can expose out-of-scope diagnostics.
    for name in (
        "ruff",
        "black",
        "isort",
        "pylint",
        "shellcheck",
        "shfmt",
        "markdownlint-cli2",
        "eslint",
        "prettier",
        "go",
        "gofmt",
        "cargo",
        "dotnet",
        "tsc",
    ):
        p = tools / name
        p.write_text(
            f"#!{sys.executable}\n"
            + """import json, os, sys
from pathlib import Path
with Path(os.environ['SCOPE_CALLS']).open('a') as f:
    f.write(json.dumps({'tool': Path(sys.argv[0]).name, 'args': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')
if os.environ.get('SCOPE_FAIL') == Path(sys.argv[0]).name:
    print('unchanged/caller.py: dependency error from task change')
    sys.exit(1)
if Path(sys.argv[0]).name == 'gofmt':
    if '-l' in sys.argv and os.environ.get('GOFMT_DIRTY'):
        print(sys.argv[-1])
    sys.exit(0)
print('{"comments":[]}' if Path(sys.argv[0]).name == 'shellcheck' else 'ok')
"""
        )
        p.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("SCOPE_CALLS", str(tmp_path / "calls.jsonl"))
    (tmp_path / ".gitignore").write_text("bin/\ncalls.jsonl\n.cache/\n")
    return tmp_path


def calls(root):
    p = root / "calls.jsonl"
    return (
        [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
    )


def test_lint_preserves_explicit_scope_and_is_read_only(project):
    for name in ("task.py", "unrelated.py"):
        (project / name).write_text("x = 1\n")
    result = CHECKER.check_files(project, ["task.py"], mode="lint")
    assert result["status"] == "passed", result
    assert calls(project)
    assert all(
        "./task.py" in c["args"] and "./unrelated.py" not in c["args"]
        for c in calls(project)
    )
    assert all(
        "format" not in c["args"] and "--fix" not in c["args"] for c in calls(project)
    )


def test_config_change_does_not_expand_any_file_targets(project):
    (project / "ruff.toml").write_text("")
    (project / "task.py").write_text("x = 1\n")
    (project / "other.py").write_text("x = 2\n")
    result = CHECKER.check_files(project, ["task.py", "ruff.toml"])
    assert result["status"] == "passed", result
    writes = [c for c in calls(project) if "format" in c["args"]]
    assert writes and all("./other.py" not in c["args"] for c in writes)
    assert all("./other.py" not in c["args"] for c in calls(project))


def test_configured_black_wins_over_default_formatter(project):
    (project / "pyproject.toml").write_text("[tool.black]\nline-length = 90\n")
    (project / "task.py").write_text("x = 1\n")
    result = CHECKER.check_files(project, ["task.py"])
    assert result["status"] == "passed", result
    assert any(
        c["tool"] == "black" and "--check" not in c["args"] for c in calls(project)
    )
    assert not any(
        c["tool"] == "ruff" and "format" in c["args"] for c in calls(project)
    )


def test_nested_package_scripts_keep_rules_and_replace_broad_selectors(project):
    package = project / "web"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "lint": "eslint . --max-warnings 0",
                    "format": "prettier --write .",
                }
            }
        )
    )
    (package / "task.js").write_text("let a=1\n")
    (package / "other.js").write_text("let a=2\n")
    result = CHECKER.check_files(project, ["web/task.js"], mode="lint")
    assert result["status"] == "passed", result
    observed = calls(project)
    assert {c["tool"] for c in observed} == {"eslint", "prettier"}
    assert all(
        c["cwd"] == str(package) and "./task.js" in c["args"] and "." not in c["args"]
        for c in observed
    )
    assert all(
        "--write" not in c["args"] and "--fix" not in c["args"] for c in observed
    )
    assert ["--max-warnings", "0"] == observed[0]["args"][:2]


@pytest.mark.parametrize(
    "script",
    ["eslint . && touch surprise", "node custom-lint.js", "eslint --unknown ."],
)
def test_opaque_script_stays_blocked(project, script):
    (project / "package.json").write_text(json.dumps({"scripts": {"lint": script}}))
    (project / "task.js").write_text("let a=1\n")
    result = CHECKER.check_files(project, ["task.js"], mode="lint")
    assert result["status"] == "not_checked", result
    assert any("cannot be safely scoped" in s for s in result["summary"])
    assert not (project / "surprise").exists()


def test_opaque_formatter_does_not_run_configured_fallback(project):
    (project / "package.json").write_text(
        json.dumps(
            {"scripts": {"lint": "eslint .", "format": "node project-format.js"}}
        )
    )
    (project / ".prettierrc").write_text("{}")
    (project / "task.js").write_text("let a=1\n")
    result = CHECKER.check_files(project, ["task.js"])
    assert result["status"] == "not_checked"
    assert all(
        "--write" not in c["args"] and "--fix" not in c["args"] for c in calls(project)
    )


def test_unsupported_mixed_selection_only_checks_supported_files(project):
    (project / "task.py").write_text("x = 1\n")
    skipped = [".todo.swp", ".DS_Store", "video.flv", "change.patch", "unknown.exotic"]
    for name in skipped:
        (project / name).write_text("artifact\n")
    result = CHECKER.check_files(
        project, ["task.py", "deleted.py", *skipped], mode="lint"
    )
    assert result["status"] == "passed", result
    assert all(
        result["coverage"][name].startswith("not applicable") for name in skipped
    )
    assert calls(project)
    assert all(c["args"][-1] == "./task.py" for c in calls(project))


def test_supported_linter_failure_remains_blocking(project, monkeypatch):
    (project / "task.py").write_text("x = 1\n")
    monkeypatch.setenv("SCOPE_FAIL", "ruff")
    result = CHECKER.check_files(project, ["task.py"], mode="lint")
    assert result["status"] == "findings", result


def test_deleted_file_is_silently_skipped_by_hook(project, monkeypatch, capsys):
    HOOK.ensure_helpers()
    (project / "deleted.py").write_text("x = 1\n")
    payload = {"session_id": "deletion"}
    HOOK.handle(project, "codex", "SessionStart", payload)
    (project / "deleted.py").unlink()
    assert HOOK.handle(project, "codex", "Stop", payload) == {}
    assert calls(project) == []
    assert capsys.readouterr().err == ""


def test_aggregate_and_hook_bound_bytes_lines_with_full_evidence(project):
    wrapper = CHECKER.QUALITY
    value = wrapper.result(
        "findings", "fixture", [f"error {i}: " + "界" * 1000 for i in range(100)]
    )
    wrapper.save_summary(value, root=project)
    assert len(json.loads(Path(value["evidence"]).read_text())["summary"]) == 100
    for message in [
        wrapper.render_result(value),
        HOOK.response("codex", "Stop", value, attempts=3)["reason"],
    ]:
        assert len(message.encode()) <= 4096
        assert len(message.splitlines()) <= 30
        assert "findings" in message
        assert "saved evidence" in message
        assert value["evidence"] in message
        assert value["summary_log"] in message


def test_prettier_does_not_replace_lint(project):
    (project / ".prettierrc").write_text("{}")
    (project / "task.js").write_text("const a = 1\n")
    result = CHECKER.check_files(project, ["task.js"], mode="lint")
    assert result["status"] == "not_checked", result
    assert any("Biome" in line for line in result["summary"])


def test_nested_config_retains_parent_scripts(project):
    (project / "package.json").write_text(
        json.dumps({"scripts": {"lint": "eslint .", "format": "prettier --write ."}})
    )
    child = project / "nested"
    child.mkdir()
    (child / "biome.json").write_text("{}")
    (child / "task.js").write_text("const a = 1\n")
    result = CHECKER.check_files(project, ["nested/task.js"], mode="lint")
    assert result["status"] == "passed", result
    assert {c["tool"] for c in calls(project)} == {"eslint", "prettier"}


def test_deleted_python_does_not_lint_remaining_files(project):
    (project / "README.md").write_text("# Docs\n")
    (project / "unrelated.py").write_text("invalid !!!\n")
    result = CHECKER.check_files(project, ["deleted.py"], mode="lint")
    assert result["explicitly_skipped"], result
    assert calls(project) == []


def test_asset_is_explicitly_not_applicable(project):
    (project / "image.png").write_bytes(b"image")
    result = CHECKER.check_files(project, ["image.png"])
    assert result["status"] == "not_checked" and result["explicitly_skipped"]
    assert calls(project) == []


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_unchanged_stops_reuse_failure_without_commands_or_logs(
    project, provider, monkeypatch
):
    HOOK.ensure_helpers()
    payload = {"session_id": "repeat"}
    HOOK.handle(project, provider, "SessionStart", payload)
    (project / "task.py").write_text("x = 1\n")
    monkeypatch.setenv("SCOPE_FAIL", "ruff")
    assert HOOK.handle(project, provider, "Stop", payload)["decision"] == "block"
    before_calls = calls(project)
    before_logs = set((project / ".cache").rglob("*"))
    for _ in range(9):
        result = HOOK.handle(project, provider, "Stop", payload)
        assert result == {
            "continue": False,
            "stopReason": "BLOCKED — unchanged lint findings.",
        }
    assert calls(project) == before_calls
    assert set((project / ".cache").rglob("*")) == before_logs
    (project / "task.py").write_text("x = 2\n")
    assert HOOK.handle(project, provider, "Stop", payload)["decision"] == "block"
    assert len(calls(project)) > len(before_calls)
    before_calls = calls(project)
    CHECKER.check_files(project, ["task.py"], mode="lint")
    assert len(calls(project)) > len(before_calls)


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_successful_hook_only_emits_pass_and_does_not_review_design(
    project, provider, monkeypatch, capsys
):
    HOOK.ensure_helpers()
    monkeypatch.setattr(
        HOOK, "review_design", lambda *a: pytest.fail("design review ran")
    )
    payload = {"session_id": "success"}
    HOOK.handle(project, provider, "SessionStart", payload)
    (project / "task.py").write_text("x = 1\n")
    assert HOOK.handle(project, provider, "Stop", payload) == {}
    captured = capsys.readouterr()
    assert captured.err == "PASS\n"
    assert captured.out == ""
    payload["tool_input"] = {"file_path": "task.py"}
    result = HOOK.handle(project, provider, "PostToolUse", payload)
    assert result["hookSpecificOutput"]["additionalContext"] == "PASS"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_unsupported_only_hook_is_silent(project, provider, capsys):
    payload = {"session_id": "skip"}
    HOOK.handle(project, provider, "SessionStart", payload)
    for name in (".DS_Store", ".todo.swp", "clip.flv", "usage.patch"):
        (project / name).write_text("artifact")
    assert HOOK.handle(project, provider, "Stop", payload) == {}
    assert calls(project) == []
    assert capsys.readouterr().err == ""


def test_missing_required_tool_still_blocks(project, monkeypatch):
    (project / "task.py").write_text("x = 1\n")
    original = CHECKER.executable
    monkeypatch.setattr(
        CHECKER,
        "executable",
        lambda root, name: None if name == "ruff" else original(root, name),
    )
    result = CHECKER.check_files(project, ["task.py"], mode="lint")
    assert result["status"] == "not_checked"
    assert any("Ruff" in line for line in result["summary"])


def test_python_typecheck_config_does_not_add_analysis(project):
    (project / "pyproject.toml").write_text("[tool.mypy]\n[tool.pyright]\n")
    (project / "task.py").write_text("x = 1\n")
    result = CHECKER.check_files(project, ["task.py"], mode="lint")
    assert result["status"] == "passed", result
    assert {c["tool"] for c in calls(project)} == {"ruff"}


def test_gofmt_checks_only_selected_files_and_reports_formatting(project, monkeypatch):
    for name in ("task.go", "other.go"):
        (project / name).write_text("package example\n")
    assert CHECKER.check_files(project, ["task.go"], mode="lint")["status"] == "passed"
    monkeypatch.setenv("GOFMT_DIRTY", "1")
    assert (
        CHECKER.check_files(project, ["task.go"], mode="lint")["status"] == "findings"
    )
    assert {c["tool"] for c in calls(project)} == {"gofmt"}
    assert all(c["args"] == ["-l", "./task.go"] for c in calls(project))


@pytest.mark.parametrize("name", ["task.py", "notes.md", "unknown.yaml"])
def test_unrelated_package_script_cannot_block_other_files(project, name):
    (project / "package.json").write_text(
        json.dumps({"scripts": {"lint": "npm run custom", "format": "node custom.js"}})
    )
    (project / name).write_text("content\n")
    result = CHECKER.check_files(project, [name], mode="lint")
    assert result["status"] == "passed" or result.get("explicitly_skipped"), result
    assert not any(c["tool"] in {"eslint", "prettier"} for c in calls(project))


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        [],
        {"scripts": []},
        {"scripts": {"lint": 42}},
        {"scripts": {"format": ["custom"]}},
    ],
)
def test_optional_unsupported_file_ignores_malformed_package_scripts(project, manifest):
    (project / "package.json").write_text(json.dumps(manifest))
    (project / "unknown.yaml").write_text("name: value\n")
    result = CHECKER.check_files(project, ["unknown.yaml"], mode="lint")
    assert result.get("explicitly_skipped"), result
    assert calls(project) == []
    (project / "task.js").write_text("const x = 1;\n")
    result = CHECKER.check_files(project, ["task.js"], mode="lint")
    assert result["status"] == "tool_failure", result


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_explicit_check_invalidates_old_automatic_failure(
    project, provider, monkeypatch
):
    HOOK.ensure_helpers()
    payload = {"session_id": "explicit"}
    HOOK.handle(project, provider, "SessionStart", payload)
    (project / "task.py").write_text("x=1\n")
    monkeypatch.setenv("SCOPE_FAIL", "ruff")
    assert HOOK.handle(project, provider, "Stop", payload)["decision"] == "block"
    assert HOOK.handle(project, provider, "Stop", payload)["continue"] is False
    monkeypatch.delenv("SCOPE_FAIL")
    checked = subprocess.run(
        [
            sys.executable,
            str(SOURCE / "agent-check-edited.py"),
            "--mode",
            "lint",
            "task.py",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert HOOK.handle(project, provider, "Stop", payload) == {}


def test_missing_failure_evidence_forces_fresh_check(project, monkeypatch):
    payload = {"session_id": "evidence"}
    HOOK.handle(project, "codex", "SessionStart", payload)
    (project / "task.py").write_text("x=1\n")
    monkeypatch.setenv("SCOPE_FAIL", "ruff")
    assert HOOK.handle(project, "codex", "Stop", payload)["decision"] == "block"
    state = json.loads(
        next((project / ".cache/quality-sessions").glob("*.json")).read_text()
    )
    (project / state["blocked_evidence"]).unlink()
    before = len(calls(project))
    assert HOOK.handle(project, "codex", "Stop", payload)["decision"] == "block"
    assert len(calls(project)) > before


def test_quality_refresh_is_owned_scoped_and_safe_to_repeat(project):
    manager = load("managed-install")
    provider = "codex"
    scripts = project / "vibe_scripts/codex"
    scripts.mkdir(parents=True)
    (scripts / "Makefile").write_text(
        "lint-ai:\n\t@old-lint\n\nverify-ai:\n\t@$(MAKE) -f vibe_scripts/codex/Makefile lint-ai\n\ncustom:\n\t@echo keep\n"
    )
    (scripts / "custom.txt").write_text("keep")
    receipt = project / ".cache/vibehelper-install/codex.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {str(p.relative_to(project)): manager.digest(p) for p in scripts.iterdir()}
        )
    )
    manager.install_usage_tracker(project, provider, quality=True)
    first = receipt.read_bytes()
    manager.install_usage_tracker(project, provider, quality=True)
    assert receipt.read_bytes() == first
    text = (scripts / "Makefile").read_text()
    assert "--mode lint --all" in text and "custom:\n\t@echo keep" in text
    assert "Makefile lint-all-ai" in text
    assert (scripts / "custom.txt").read_text() == "keep"
    (scripts / "agent-check-edited.py").write_text("custom local changes")
    with pytest.raises(ValueError, match="Preserving modified"):
        manager.install_usage_tracker(project, provider, quality=True)
    assert (scripts / "agent-check-edited.py").read_text() == "custom local changes"


@pytest.mark.parametrize(
    "deleted", ["deleted.rs", "deleted.cs", "deleted.go", "deleted.js"]
)
def test_deleted_language_is_skipped(project, deleted):
    (project / "README.md").write_text("# Docs\n")
    result = CHECKER.check_files(project, [deleted], mode="lint")
    assert result["explicitly_skipped"], result
    assert calls(project) == []


@pytest.mark.parametrize(
    "config,content",
    [
        ("Cargo.toml", "[package]\nname='app'\n"),
        ("pyproject.toml", "[project]\nname='app'\n"),
        ("go.mod", "module app\n"),
    ],
)
def test_unsupported_config_does_not_select_other_files(project, config, content):
    (project / config).write_text(content)
    (project / "README.md").write_text("# Docs\n")
    result = CHECKER.check_files(project, [config], mode="lint")
    assert result.get("explicitly_skipped"), result
    assert calls(project) == []


def test_eslint_does_not_leave_css_unchecked(project):
    (project / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}))
    for name in ("task.js", "style.css"):
        (project / name).write_text("source\n")
    result = CHECKER.check_files(project, ["task.js", "style.css"], mode="lint")
    assert result["status"] == "not_checked", result
    assert any("Biome" in line for line in result["summary"])


def test_project_local_executable_is_used(project):
    local = project / ".venv/bin/ruff"
    local.parent.mkdir(parents=True)
    local.write_bytes((project / "bin/ruff").read_bytes())
    local.chmod(0o755)
    (project / "task.py").write_text("x = 1\n")
    planned, missing, _ = CHECKER.plan_checks(project, ["task.py"], "lint")
    assert not missing
    assert all(str(local) in c[1][1][0] for c in planned)


@pytest.mark.parametrize("tool,suffix", [("ruff", ".py"), ("biome", ".js")])
def test_format_only_script_keeps_native_lint(project, tool, suffix):
    (project / "package.json").write_text(
        json.dumps({"scripts": {"format": f"{tool} format ."}})
    )
    if tool == "biome":
        local = project / "node_modules/.bin/biome"
        local.parent.mkdir(parents=True)
        local.write_bytes((project / "bin/eslint").read_bytes())
        local.chmod(0o755)
    (project / ("task" + suffix)).write_text("source\n")
    result = CHECKER.check_files(project, ["task" + suffix], mode="lint")
    assert result["status"] == "passed", result
    assert any(c["tool"] == tool and "check" in c["args"] for c in calls(project))


def test_refresh_reviewed_hash_cannot_overwrite_later_change(project):
    manager = load("managed-install")
    scripts = project / "vibe_scripts/codex"
    scripts.mkdir(parents=True)
    makefile = scripts / "Makefile"
    makefile.write_text("lint-ai:\n\t@old-lint\n")
    reviewed = manager.digest(makefile)
    receipt = project / ".cache/vibehelper-install/codex.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}")
    makefile.write_text("lint-ai:\n\t@custom-lint\n")
    with pytest.raises(ValueError, match="Preserving modified"):
        manager.install_usage_tracker(
            project, "codex", quality=True, reviewed_makefile_sha256=reviewed
        )
    assert not (scripts / "agent-check-edited.py").exists()
    assert makefile.read_text() == "lint-ai:\n\t@custom-lint\n"


def test_root_config_does_not_select_nested_package(project):
    (project / "ruff.toml").write_text("")
    nested = project / "nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("[project]\nname='nested'\n")
    (nested / "task.py").write_text("x = 1\n")
    result = CHECKER.check_files(project, ["ruff.toml"])
    assert result["explicitly_skipped"], result
    assert calls(project) == []


def test_python_without_tomllib_still_checks_non_toml_projects(project, monkeypatch):
    monkeypatch.setattr(CHECKER, "tomllib", None)
    (project / "pyproject.toml").write_text("[tool.black]\n")
    (project / "task.sh").write_text("echo ok\n")
    assert CHECKER.check_files(project, ["task.sh"], mode="lint")["status"] == "passed"
    (project / "pyproject.toml").write_text("[tool.black]\n")
    (project / "task.py").write_text("x=1\n")
    result = CHECKER.check_files(project, ["task.py"], mode="lint")
    assert result["status"] == "tool_failure" and "Python 3.11" in result["summary"][0]


@pytest.mark.parametrize(
    "selected,python_targets",
    [
        ([".markdownlint.json"], set()),
        ([".markdownlint.json", "task.py"], {"./task.py"}),
        ([".markdownlint.json", "ruff.toml"], set()),
        (["deleted.md"], set()),
    ],
)
def test_config_and_deletion_analysis_stays_with_relevant_tools(
    project, selected, python_targets
):
    (project / ".markdownlint.json").write_text("{}")
    (project / "ruff.toml").write_text("")
    (project / "README.md").write_text("# Docs\n")
    for name in ("task.py", "unrelated.py"):
        (project / name).write_text("x = 1\n")
    result = CHECKER.check_files(project, selected, mode="lint")
    assert result["status"] == "passed" or result.get("explicitly_skipped"), result
    assert all("./README.md" not in c["args"] for c in calls(project))
    observed = {
        arg
        for c in calls(project)
        if c["tool"] == "ruff"
        for arg in c["args"]
        if arg.endswith(".py")
    }
    assert observed == python_targets


@pytest.mark.parametrize(
    "config,content",
    [
        ("eslint.config.js", "export default []"),
        ("tsconfig.json", "{}"),
        ("biome.json", "{}"),
    ],
)
def test_web_config_does_not_schedule_unrelated_document_or_python_checks(
    project, config, content
):
    local = project / "node_modules/.bin/biome"
    local.parent.mkdir(parents=True)
    local.write_bytes((project / "bin/eslint").read_bytes())
    local.chmod(0o755)
    (project / config).write_text(content)
    for name in ("task.js", "README.md", "unrelated.py", "page.html"):
        (project / name).write_text("source\n")
    result = CHECKER.check_files(project, [config], mode="lint")
    assert result["status"] == "passed", result
    assert result["command_count"] > 0
    assert not any(
        c["tool"] in {"ruff", "markdownlint-cli2", "htmlhint"} for c in calls(project)
    )
    assert all("./task.js" not in c["args"] for c in calls(project))


@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_generated_policy_preserves_file_scope_and_read_only_checks(project, platform):
    source = (SOURCE.parents[1] / "CodexHelper" / platform / "part1.sh").read_text()
    hook = source.split("cat <<'EOF_PRE_HOOK'\n", 1)[1].split("\nEOF_PRE_HOOK", 1)[0]
    fixture = project / "policy_fixture.py"
    fixture.write_text(hook)
    spec = importlib.util.spec_from_file_location("policy_fixture", fixture)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = module.quality_violation
    for command in (
        "ruff check src/task.py",
        "black --check src/task.py",
        "eslint src/task.js",
    ):
        reason = policy(command)
        assert "agent-verify.sh lint" in reason and "same explicit file paths" in reason
    assert "agent-verify.sh edited" in policy("black src/task.py")
    assert not policy("make -f vibe_scripts/codex/Makefile lint-all-ai")


def test_explicit_cli_without_session_and_all_conflict(project, monkeypatch):
    (project / "task.py").write_text("x = 1\n")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    command = [sys.executable, str(SOURCE / "agent-check-edited.py"), "--mode", "lint"]
    result = subprocess.run(
        [*command, "task.py"], cwd=project, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(
        [*command, "--all", "task.py"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
