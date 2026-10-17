"""Design review is a scoped completion hook, independent of ordinary lint."""

import importlib.util
import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "templates/vibe_scripts/quality-hook.py"
SPEC = importlib.util.spec_from_file_location("design_quality_hook", SOURCE)
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)
HOOK.ensure_helpers()


@pytest.fixture
def project(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".cache/\n.agents/\n.codex/\n.claude/\n")
    monkeypatch.setattr(
        HOOK.CHECKER,
        "check_files",
        lambda *a, **kw: HOOK.QUALITY.result("passed", "ordinary lint", []),
    )
    monkeypatch.setattr(
        HOOK.CHECKER, "local_impeccable_command", lambda root: ["impeccable"]
    )
    return tmp_path


@pytest.mark.parametrize(
    "name,before,after,design",
    [
        (
            "page.html",
            '<h1>Hello</h1><script>track("a")</script>',
            '<h1>Hello</h1><script>track("b")</script>',
            False,
        ),
        (
            "Widget.tsx",
            'const id="a"; export const UI=()=> <button onClick={() => track("a")}>Send</button>',
            'const id="b"; export const UI=()=> <button onClick={() => { track("b"); done(); }}>Send</button>',
            False,
        ),
        (
            "page.html",
            '<button data-track-id="a" onclick="track()">Send</button>',
            '<button data-track-id="b" onclick="trackMore()">Send</button>',
            False,
        ),
        (
            "tracker.ts",
            "const id = <T>(v:T) => v; if (a < b && c > d) track(1);",
            "const id = <T>(v:T) => v; if (a < b && c > d) track(2);",
            False,
        ),
        ("page.html", "<h1>Hello</h1>", "<h1>Welcome</h1>", True),
        (
            "Widget.tsx",
            '<button className="small">Send</button>',
            '<button className="large">Send</button>',
            True,
        ),
        (
            "Widget.tsx",
            '<button aria-label="Send" />',
            '<button aria-label="Submit" />',
            True,
        ),
        ("theme.css", "body { color: black; }", "body { color: red; }", True),
        (
            "Widget.vue",
            "<template><h1>Hello</h1></template><script>track(1)</script>",
            "<template><h1>Hello</h1></template><script>track(2)</script>",
            False,
        ),
        (
            "Widget.svelte",
            "<style>h1 {color:red}</style><h1>Hello</h1>",
            "<style>h1 {color:blue}</style><h1>Hello</h1>",
            True,
        ),
    ],
)
def test_design_projection(project, name, before, after, design):
    path = project / name
    path.write_text(before)
    original = HOOK.design_fingerprint(path)
    path.write_text(after)
    assert (HOOK.design_fingerprint(path) != original) is design


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_completion_only_scopes_and_deduplicates_design(project, monkeypatch, provider):
    (project / "theme.css").write_text("body {color:black}")
    (project / "untouched.html").write_text('<h1 class="old">Existing dirty UI</h1>')
    (project / "tracking.ts").write_text("export const track = 1;")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return HOOK.QUALITY.result("passed", kwargs["label"], [], command_count=1)

    monkeypatch.setattr(HOOK.QUALITY, "run_quality", run)
    payload = {"session_id": "design-session"}
    assert HOOK.handle(project, provider, "SessionStart", payload) == {}
    (project / "tracking.ts").write_text("export const track = 2;")
    HOOK.handle(
        project,
        provider,
        "PostToolUse",
        payload | {"tool_input": {"path": "tracking.ts"}},
    )
    assert HOOK.handle(project, provider, "Stop", payload) == {}
    assert not calls
    (project / "theme.css").write_text("body {color:red}")
    HOOK.handle(
        project,
        provider,
        "PostToolUse",
        payload | {"tool_input": {"path": "theme.css"}},
    )
    assert not calls
    assert HOOK.handle(project, provider, "Stop", payload) == {}
    assert calls == [["impeccable", "detect", "theme.css"]]
    assert HOOK.handle(project, provider, "Stop", payload) == {}
    assert len(calls) == 1
    (project / "theme.css").write_text("body {color:blue}")
    HOOK.handle(project, provider, "Stop", payload)
    assert len(calls) == 2


def test_new_logic_file_does_not_become_design_work(project, monkeypatch):
    calls = []
    monkeypatch.setattr(HOOK.QUALITY, "run_quality", lambda *a, **kw: calls.append(a))
    payload = {"session_id": "new-backend"}
    HOOK.handle(project, "codex", "SessionStart", payload)
    (project / "tracking.ts").write_text("export const track = () => sendEvent();")
    assert HOOK.handle(project, "codex", "Stop", payload) == {}
    assert not calls


@pytest.mark.parametrize("status", ["findings", "tool_failure"])
def test_failed_review_stays_pending(project, monkeypatch, status):
    path = project / "page.html"
    path.write_text("<h1>Before</h1>")
    payload = {"session_id": "pending"}
    HOOK.handle(project, "codex", "SessionStart", payload)
    path.write_text("<h1>After</h1>")
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return HOOK.QUALITY.result(status, "UI design review", ["review failed"])

    monkeypatch.setattr(HOOK.QUALITY, "run_quality", run)
    for _ in range(2):
        result = HOOK.handle(project, "codex", "Stop", payload)
        assert result["decision"] == "block"
        assert "UI design review" in result["reason"]
    assert len(calls) == 2


@pytest.mark.parametrize("options", [{"enabled": False}, {"consent": "declined"}])
def test_existing_disable_and_consent_are_preserved(project, options):
    (project / ".impeccable").mkdir()
    config = project / ".impeccable/config.local.json"
    config.write_text(json.dumps({"hook": options}))
    assert not HOOK.design_enabled(project)
    assert json.loads(config.read_text()) == {"hook": options}


def test_missing_baseline_does_not_guess_scope(project):
    (project / "page.html").write_text("<h1>Existing</h1>")
    result = HOOK.review_design(project, ["page.html"], {}, 30)
    assert result["status"] == "not_checked"
    assert "baseline unavailable" in result["summary"][0]


def test_lint_never_invokes_or_requires_impeccable(project, monkeypatch):
    checker = HOOK.CHECKER
    (project / ".agents/skills/impeccable").mkdir(parents=True)
    monkeypatch.setattr(
        checker,
        "local_impeccable_command",
        lambda root: pytest.fail("lint queried design tool"),
    )
    groups = checker.split_by_ext(["page.html", "Widget.tsx", "theme.css"])
    commands = [
        command
        for phase in checker.build_commands(project, groups)
        for command in phase
    ]
    assert not any("impeccable" in str(command).lower() for command in commands)
    assert not any(
        "Impeccable" in message for message in checker.missing_tools(project, groups)
    )


@pytest.mark.parametrize(
    "provider,extension", [("codex", "toml"), ("codex", "json"), ("claude", "json")]
)
def test_native_hook_migration_preserves_custom_entries(project, provider, extension):
    runtime = project / f"vibe_scripts/{provider}/quality-hook.py"
    runtime.parent.mkdir(parents=True)
    shutil.copy2(SOURCE, runtime)
    shutil.copy2(
        SOURCE.with_name("agent-check-edited.py"),
        runtime.with_name("agent-check-edited.py"),
    )
    adapter = project / f".{provider}/hooks/stop_edited_check.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(HOOK.stop_adapter(provider))
    path = (
        project
        / f".{provider}"
        / (
            "config.toml"
            if extension == "toml"
            else "hooks.json"
            if provider == "codex"
            else "settings.local.json"
        )
    )
    native = min(HOOK.native_design_commands(provider))
    managed = HOOK.stop_command(provider)
    custom = "echo custom impeccable audit"
    if extension == "json":
        path.write_text(
            json.dumps(
                {
                    "other": {"keep": 1},
                    "hooks": {
                        "SessionStart": [
                            {"hooks": [{"command": HOOK.session_command(provider)}]}
                        ],
                        "Stop": [
                            {
                                "hooks": [
                                    {"command": native},
                                    {"command": custom},
                                    {"command": managed},
                                ]
                            }
                        ],
                    },
                }
            )
        )
    else:
        path.write_text(
            'model = "keep"\n# Preserve this comment\n[[hooks.Stop]]\nhooks = [{command = '
            + json.dumps(native)
            + "}, {command = "
            + json.dumps(custom)
            + "}, {command = "
            + json.dumps(managed)
            + "}]\n"
            + "[[hooks.SessionStart]]\nhooks = [{command = "
            + json.dumps(HOOK.session_command(provider))
            + "}]\n"
        )
    HOOK.install_design_scope(project, provider)
    after = path.read_text()
    data = json.loads(after) if extension == "json" else tomllib.loads(after)
    commands = [
        hook["command"] for group in data["hooks"]["Stop"] for hook in group["hooks"]
    ]
    assert commands == [custom, managed]
    assert custom in after and "keep" in after and "stop_edited_check.py" in after
    assert len(list(path.parent.glob(path.name + ".bak.*"))) == 1
    HOOK.install_design_scope(project, provider)
    assert path.read_text() == after
    assert len(list(path.parent.glob(path.name + ".bak.*"))) == 1


def test_migration_preserves_native_hooks_without_replacement(project):
    runtime = project / "vibe_scripts/codex/quality-hook.py"
    runtime.parent.mkdir(parents=True)
    shutil.copy2(SOURCE, runtime)
    shutil.copy2(
        SOURCE.with_name("agent-check-edited.py"),
        runtime.with_name("agent-check-edited.py"),
    )
    path = project / ".codex/hooks.json"
    path.parent.mkdir()
    before = json.dumps(
        {
            "hooks": {
                "Stop": [
                    {"hooks": [{"command": min(HOOK.native_design_commands("codex"))}]}
                ]
            }
        }
    )
    path.write_text(before)
    with pytest.raises(ValueError, match="Stop adapter"):
        HOOK.install_design_scope(project, "codex")
    assert path.read_text() == before


def test_migration_preserves_old_runtime(project):
    runtime = project / "vibe_scripts/codex/quality-hook.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("# custom old runtime\n")
    HOOK.install_design_scope(project, "codex")  # No native registrations to migrate.
    assert runtime.read_text() == "# custom old runtime\n"


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_adapter_and_registration_match_delivered_templates(provider, platform):
    installer = (
        ROOT / (provider.title() + "Helper") / platform / "part1.sh"
    ).read_text()
    assert HOOK.stop_adapter(provider).strip() in installer
    # Claude embeds JSON; Codex embeds a literal TOML command.
    command = HOOK.stop_command(provider)
    assert (json.dumps(command)[1:-1] if provider == "claude" else command) in installer
    command = HOOK.session_command(provider)
    assert (json.dumps(command)[1:-1] if provider == "claude" else command) in installer


def test_invalid_design_config_does_not_affect_tracking_work(project):
    (project / ".impeccable").mkdir()
    (project / ".impeccable/config.json").write_text("broken config")
    path = project / "Widget.tsx"
    path.write_text("const id=1; export const UI=()=> <h1>Hello</h1>")
    state = {"design_baseline": HOOK.design_snapshot(project, ["Widget.tsx"])}
    path.write_text("const id=2; export const UI=()=> <h1>Hello</h1>")
    assert HOOK.review_design(project, ["Widget.tsx"], state, 30) is None


def test_native_detector_in_completion_hook(project, monkeypatch):
    executable = ROOT / ".agents/skills/impeccable/scripts/bin/darwin-arm64/impeccable"
    if not executable.is_file():
        pytest.skip("Native Impeccable binary is unavailable on this host")
    monkeypatch.setattr(
        HOOK.CHECKER, "local_impeccable_command", lambda root: [str(executable)]
    )
    path = project / "page.html"
    path.write_text('<html><body><p style="font-size:16px">Hello</p></body></html>')
    state = {"design_baseline": HOOK.design_snapshot(project, ["page.html"])}
    path.write_text('<html><body><p style="font-size:10px">Hello</p></body></html>')
    result = HOOK.review_design(project, ["page.html"], state, 30)
    assert result["status"] == "findings", result
    assert "undersized-ui-text" in Path(result["log"]).read_text()


def test_svelte_tracking_callbacks_do_not_trigger_review(project):
    path = project / "Widget.svelte"
    path.write_text('<button on:click|preventDefault={() => track("a")}>Send</button>')
    before = HOOK.design_fingerprint(path)
    path.write_text('<button on:click|preventDefault={() => track("b")}>Send</button>')
    assert HOOK.design_fingerprint(path) == before


@pytest.mark.parametrize(
    "before,after",
    [
        (
            "const ok = count < input && enabled > 0;",
            "const ok = count < input && tracking > 0;",
        ),
        ('track("<button>Before</button>");', 'track("<button>After</button>");'),
        ("const f = <T>(v:T) => v; track(1);", "const f = <U>(v:U) => v; track(2);"),
    ],
)
def test_js_comparisons_and_telemetry_strings_are_not_markup(project, before, after):
    path = project / "tracking.tsx"
    path.write_text(before)
    original = HOOK.design_fingerprint(path)
    path.write_text(after)
    assert HOOK.design_fingerprint(path) == original


def test_jsx_fragment_copy_changes_are_design_work(project):
    path = project / "Widget.tsx"
    path.write_text("const UI = () => <>Before</>;")
    original = HOOK.design_fingerprint(path)
    path.write_text("const UI = () => <>After</>;")
    assert HOOK.design_fingerprint(path) != original


def test_unconfigured_design_hook_does_not_require_baseline_or_large_file_review(
    project, monkeypatch
):
    monkeypatch.setattr(HOOK.CHECKER, "local_impeccable_command", lambda root: None)
    (project / "large.ts").write_text("x" * 131073)
    assert HOOK.review_design(project, ["large.ts"], {}, 30) is None


def test_fingerprinting_time_is_deducted_from_detector_budget(project, monkeypatch):
    path = project / "page.html"
    path.write_text("<h1>After</h1>")
    clock = [0]
    monkeypatch.setattr(HOOK.time, "monotonic", lambda: clock[0])

    def snapshot(*args):
        clock[0] = 7
        return {"page.html": "changed"}

    monkeypatch.setattr(HOOK, "design_snapshot", snapshot)

    def run(*args, **kwargs):
        assert kwargs["timeout"] == 2
        return HOOK.QUALITY.result("passed", "UI design review", [])

    monkeypatch.setattr(HOOK.QUALITY, "run_quality", run)
    assert (
        HOOK.review_design(project, ["page.html"], {"design_baseline": {}}, 10)[
            "status"
        ]
        == "passed"
    )


def test_design_limits_report_unavailable_only_for_selected_files(project):
    large = project / "large.tsx"
    large.write_text("x" * 131073)
    normal = project / "page.html"
    normal.write_text("<h1>Hello</h1>")
    baseline = HOOK.design_snapshot(project, ["large.tsx", "page.html"])
    assert baseline["large.tsx"] is None
    assert (
        HOOK.review_design(project, ["page.html"], {"design_baseline": baseline}, 30)
        is None
    )
    result = HOOK.review_design(
        project, ["large.tsx"], {"design_baseline": baseline}, 30
    )
    assert result["status"] == "not_checked"
    assert HOOK.design_snapshot(project, ["page.html"], remaining=0) == {
        "page.html": None
    }
    (project / ".impeccable").mkdir()
    (project / ".impeccable/config.json").write_text(
        json.dumps({"hook": {"limits": {"maxFileBytes": 150000}}})
    )
    assert HOOK.design_snapshot(project, ["large.tsx"])["large.tsx"] is not None


def test_migration_requires_session_start_registration(project):
    runtime = project / "vibe_scripts/codex/quality-hook.py"
    runtime.parent.mkdir(parents=True)
    for filename in ("quality-hook.py", "agent-check-edited.py"):
        shutil.copy2(SOURCE.with_name(filename), runtime.with_name(filename))
    adapter = project / ".codex/hooks/stop_edited_check.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(HOOK.stop_adapter("codex"))
    config = project / ".codex/hooks.json"
    original = json.dumps(
        {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {"command": HOOK.stop_command("codex")},
                            {"command": min(HOOK.native_design_commands("codex"))},
                        ]
                    }
                ]
            }
        }
    )
    config.write_text(original)
    with pytest.raises(ValueError, match="SessionStart/Stop"):
        HOOK.install_design_scope(project, "codex")
    assert config.read_text() == original
