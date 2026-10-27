"""Pinned upstream integration: synthetic accounting and offline reporting."""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "templates/vibe_scripts/usage-tracker.py"
SPEC = importlib.util.spec_from_file_location("usage_tracker", SCRIPT)
TRACKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRACKER)


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    # Install the pinned dependency once with the documented install command.
    # An absent dependency fails visibly instead of skipping accounting tests.
    installed = TRACKER.runtime_dir()
    upstream = TRACKER.load_upstream()
    runtime = tmp_path / "runtime"
    shutil.copytree(installed, runtime)
    monkeypatch.setattr(TRACKER, "runtime_dir", lambda: runtime)
    monkeypatch.setattr(TRACKER, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setenv("TOKEN_USAGE_CODEX_HOME", str(tmp_path / "codex"))
    return TRACKER, upstream


def event(kind, payload, day="2026-10-01", second=0):
    return {"type": kind, "timestamp": f"{day}T10:00:{second:02}Z", "payload": payload}


def usage(inp=100, cached=40, out=20, reasoning=7):
    return {
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "output_tokens": out,
        "reasoning_output_tokens": reasoning,
    }


def rollout(
    upstream,
    project,
    sid="parent",
    parent=None,
    counters=None,
    native=True,
    model="model-a",
    day="2026-10-01",
):
    path = upstream.codex_home() / "sessions" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"id": sid, "cwd": str(project)}
    if parent:
        meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
    values = usage() if counters is None else counters
    rows = [
        event("session_meta", meta, day),
        event("event_msg", {"type": "task_started", "turn_id": "turn-1"}, day),
        event("turn_context", {"model": model, "turn_id": "turn-1"}, day),
        event(
            "response_item",
            {
                "type": "message",
                "role": "user",
                "content": [{"text": "Fix <script>alert(1)</script>"}],
            },
            day,
        ),
    ]
    if native:
        rows.extend(
            [
                event(
                    "token_usage_record",
                    {
                        "thread_id": sid,
                        "response_id": "r1",
                        "turn_id": "turn-1",
                        "usage": values,
                    },
                    day,
                )
            ]
            * 2
        )
    rows.extend(
        [
            event(
                "event_msg",
                {"type": "token_count", "info": {"total_token_usage": values}},
                day,
            )
        ]
        * 2
    )
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def append(path, *rows):
    with path.open("a") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def test_live_cache_only_reparses_changed_rollouts(tracker, tmp_path, monkeypatch):
    tracker, upstream = tracker
    first = rollout(upstream, tmp_path, sid="first")
    second = rollout(upstream, tmp_path, sid="second")
    parsed = []
    original = upstream.parse_codex_session

    def parse(path):
        parsed.append(path)
        return original(path)

    monkeypatch.setattr(upstream, "parse_codex_session", parse)
    app = tracker.LiveDashboard(upstream, "2026-10-01")
    app.refresh()
    assert app.data["usage"]["input_total"] == 200
    assert len(parsed) == 2
    version = app.version
    app.refresh()
    assert app.version == version and len(parsed) == 2
    first.write_text(
        first.read_text().replace('"input_tokens": 100', '"input_tokens": 200')
    )
    app.refresh()
    assert app.data["usage"]["input_total"] == 300
    assert parsed[2:] == [first]
    second.unlink()
    app.refresh()
    assert app.data["usage"]["input_total"] == 200
    assert len(parsed) == 3
    archived = upstream.codex_home() / "archived_sessions/first.jsonl"
    archived.parent.mkdir()
    first.rename(archived)
    app.refresh()
    assert app.data["usage"]["input_total"] == 200
    assert parsed[-1] == archived


def test_live_refresh_error_keeps_last_good_view(tracker, tmp_path, monkeypatch):
    tracker, upstream = tracker
    path = rollout(upstream, tmp_path)
    app = tracker.LiveDashboard(upstream, "2026-10-01")
    app.refresh()
    before = app.page, app.version, app.data
    path.write_text(path.read_text() + "\n")

    def broken(*args):
        raise OSError("unreadable " + "x" * 1000)

    monkeypatch.setattr(tracker, "collect", broken)
    app.refresh()
    assert (app.page, app.version, app.data) == before
    assert "unreadable" in app.error and len(app.error) < 340
    assert app.busy is False


def test_project_views_use_cached_rows_and_own_child_projects(
    tracker, tmp_path, monkeypatch
):
    tracker, upstream = tracker
    first, second = str(tmp_path / "a/repo"), str(tmp_path / "b/repo")
    rollout(upstream, first, sid="first")
    rollout(
        upstream,
        second,
        sid="second",
        counters=usage(400),
        model="model-b",
        day="2026-10-02",
    )
    rollout(upstream, first, sid="child", parent="second", counters=usage(80))
    unknown = rollout(upstream, tmp_path, sid="unknown")
    unknown.write_text(unknown.read_text().replace(json.dumps(str(tmp_path)), "null"))
    app = tracker.LiveDashboard(upstream, "2026-10-01")
    app.refresh()

    def no_scan(*args):
        pytest.fail("Project selection must not scan logs")

    monkeypatch.setattr(tracker, "collect", no_scan)
    monkeypatch.setattr(upstream, "parse_codex_session", no_scan)
    view = tracker.project_view(app.data, first)
    assert view["usage"] == {
        "input_total": 180,
        "output": 40,
        "cache_read": 80,
        "reasoning": 14,
    }
    assert [s["session_id"] for s in view["sessions"]] == ["first", "child"]
    assert view["measurements"] == {"exact": 2}
    assert [r["key"] for r in view["models"]] == ["model-a"]
    assert [r["key"] for r in view["days"]] == ["2026-10-01"]
    assert all(t["project"] == first for t in view["turns"])
    assert all(w.startswith("Collector-wide note:") for w in view["warnings"])
    assert view["quota"] == app.data["quota"]
    assert tracker.project_view(app.data, "unknown")["usage"]["input_total"] == 100
    assert tracker.project_view(app.data, "") is app.data
    page_a, version_a = app.view(first)
    page_b, version_b = app.view(second)
    assert page_a != page_b and version_a == version_b == app.version
    assert app.view(first)[0] is page_a
    assert app.project is None and len(app.data["sessions"]) == 4
    assert "Largest sessions" in page_a and "&lt;script&gt;" in page_a
    with pytest.raises(KeyError):
        app.view("/not/a/project")


def test_project_view_preserves_unknown_counters_and_cache_refresh(tracker, tmp_path):
    tracker, upstream = tracker
    project = str(tmp_path)
    path = rollout(
        upstream, project, counters={"input_tokens": 100, "output_tokens": 20}
    )
    app = tracker.LiveDashboard(upstream, "2026-10-01", project)
    rollout(upstream, tmp_path / "excluded", sid="excluded", counters=usage(1000))
    app.refresh()
    view = tracker.project_view(app.data, project)
    assert view["usage"] == {
        "input_total": 100,
        "output": 20,
        "cache_read": None,
        "reasoning": None,
    }
    assert [r["key"] for r in app.data["projects"]] == [project]
    before, version = app.view(project)
    path.write_text(
        path.read_text().replace('"input_tokens": 100', '"input_tokens": 200')
    )
    app.refresh()
    after, updated_version = app.view(project)
    assert updated_version > version and before != after
    assert tracker.project_view(app.data, project)["usage"]["input_total"] == 200
    with pytest.raises(KeyError):
        app.view(str(tmp_path / "excluded"))


def test_live_poll_does_not_start_overlapping_scans(tracker, tmp_path, monkeypatch):
    tracker, upstream = tracker
    rollout(upstream, tmp_path)
    app = tracker.LiveDashboard(upstream, "2026-10-01")
    entered, release = threading.Event(), threading.Event()
    original = tracker.collect
    calls = []

    def collect(*args):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(tracker, "collect", collect)
    app.poll()
    assert entered.wait(3)
    try:
        for _ in range(10):
            app.poll()
        assert calls == [1]
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while app.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not app.busy


def test_live_http_routes_are_local_and_keep_untrusted_text_escaped(
    tracker, tmp_path, monkeypatch
):
    tracker, upstream = tracker
    rollout(upstream, tmp_path)
    second = str(tmp_path / "other & <project>")
    rollout(upstream, second, sid="second", counters=usage(700))
    app = tracker.LiveDashboard(upstream, "2026-10-01")
    app.refresh()
    app.next_check = time.monotonic() + 60
    server = tracker.dashboard_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, headers=None):
        client = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            client.request("GET", path, headers=headers or {})
            reply = client.getresponse()
            return reply.status, dict(reply.getheaders()), reply.read().decode()
        finally:
            client.close()

    try:
        assert server.server_address[0] == "127.0.0.1"
        status, _, page = request("/")
        assert status == 200 and "setInterval(update, 5000)" in page
        status, headers, body = request("/api/view")
        assert status == 200 and headers["Cache-Control"] == "no-store"
        assert "&lt;script&gt;" in body and "<script>" not in body
        assert json.loads(request("/api/status")[2])["version"] == app.version
        assert json.loads(request("/api/status")[2])["projects"] == sorted(
            [str(tmp_path), second]
        )
        first_view = request("/api/view?" + urlencode({"project": str(tmp_path)}))[2]
        second_status, second_headers, second_view = request(
            "/api/view?" + urlencode({"project": second})
        )
        assert second_status == 200 and first_view != second_view
        assert second_headers["X-Usage-Version"] == headers["X-Usage-Version"]
        assert "other &amp; &lt;project&gt;" in second_view
        assert request("/api/view")[2] == body and app.project is None
        assert request("/?" + urlencode({"project": second}))[0] == 200
        assert request("/api/view?project=/../../auth.json")[0] == 404
        assert request("/api/view", {"Origin": "https://example.com"})[0] == 403
        assert request("/api/view", {"Host": "example.com"})[0] == 403
        assert request("/../../auth.json")[0] == 404
        assert "Access-Control-Allow-Origin" not in headers
        opened = []
        monkeypatch.setattr(tracker.webbrowser, "open", opened.append)
        tracker.serve_dashboard(
            upstream.codex_home(), server.server_port, "2026-10-01", open_browser=True
        )
        assert opened == [f"http://127.0.0.1:{server.server_port}/"]
        with pytest.raises(ValueError, match="in use"):
            tracker.serve_dashboard(
                upstream.codex_home(),
                server.server_port,
                "2026-10-01",
                project=str(tmp_path / "different"),
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_dashboard_setup_is_repeatable_and_independent_of_repo(
    tracker, tmp_path, monkeypatch
):
    tracker, upstream = tracker
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    upstream.codex_home().mkdir(parents=True)
    launcher, source = tracker.setup_dashboard(upstream.codex_home(), 8766)
    assert source == upstream.codex_home()
    assert str(ROOT) not in launcher.read_text()
    assert "serve --open" in launcher.read_text()
    assert os.access(launcher, os.X_OK)
    assert (tracker.app_dir() / "usage-tracker.py").read_bytes() == SCRIPT.read_bytes()
    assert tracker.settings()["codex_home"] == str(source)
    assert tracker.setup_dashboard() == (launcher, source)
    launcher.write_text("custom launcher")
    with pytest.raises(ValueError, match="Preserving modified"):
        tracker.setup_dashboard()
    assert launcher.read_text() == "custom launcher"


@pytest.mark.parametrize("native", [True, False])
def test_native_and_cumulative_dedup_preserve_subsets(tracker, tmp_path, native):
    tracker, upstream = tracker
    rollout(upstream, tmp_path / "repo", native=native)
    data = tracker.collect(upstream, "2026-10-01")
    assert data["usage"] == {
        "input_total": 100,
        "output": 20,
        "cache_read": 40,
        "reasoning": 7,
    }
    assert len(data["turns"]) == 1
    assert data["turns"][0]["turn_id"] == "turn-1"
    assert data["models"][0]["key"] == "model-a"
    assert data["quota"] is None


def test_children_archives_growth_and_exact_project(tracker, tmp_path):
    tracker, upstream = tracker
    repo = tmp_path / "repo"
    parent = rollout(upstream, repo)
    child = rollout(
        upstream, repo, sid="child", parent="parent", counters=usage(50, 10, 10, 3)
    )
    rollout(upstream, tmp_path / "repo-other", sid="other-child", parent="parent")
    archive = upstream.codex_home() / "archived_sessions"
    archive.mkdir()
    shutil.copy(parent, archive / "copy.jsonl")
    scoped = tracker.collect(upstream, "2026-10-01", str(repo))
    assert scoped["usage"]["input_total"] == 150
    assert len(scoped["sessions"]) == 2
    assert tracker.collect(upstream, "2026-10-01")["usage"]["input_total"] == 250
    append(
        child,
        event(
            "token_usage_record",
            {
                "thread_id": "child",
                "response_id": "r2",
                "turn_id": "turn-1",
                "usage": usage(30, 10, 10, 1),
            },
        ),
    )
    assert (
        tracker.collect(upstream, "2026-10-01", str(repo))["usage"]["input_total"]
        == 180
    )
    assert (
        tracker.collect(upstream, "2026-10-01", str(tmp_path / "rep"))["sessions"] == []
    )


def test_cumulative_models_dates_and_reset(tracker, tmp_path):
    tracker, upstream = tracker
    path = rollout(upstream, tmp_path, native=False, day="2026-09-30")
    append(
        path,
        event("event_msg", {"type": "task_started", "turn_id": "turn-2"}),
        event("turn_context", {"model": "model-b", "turn_id": "turn-2"}),
        event(
            "event_msg",
            {
                "type": "token_count",
                "info": {"total_token_usage": usage(180, 60, 40, 12)},
            },
        ),
    )
    data = tracker.collect(upstream, "2026-10-01")
    assert data["usage"] == {
        "input_total": 80,
        "output": 20,
        "cache_read": 20,
        "reasoning": 5,
    }
    assert [row["key"] for row in data["models"]] == ["model-b"]
    append(
        path,
        event(
            "event_msg",
            {
                "type": "token_count",
                "info": {"total_token_usage": usage(100, 40, 20, 7)},
            },
        ),
    )
    data = tracker.collect(upstream, "2026-10-01")
    assert data["usage"]["input_total"] == 80
    assert data["measurements"] == {"partial": 1}


def test_unknown_fields_orphans_and_broken_records(tracker, tmp_path):
    tracker, upstream = tracker
    path = rollout(
        upstream,
        tmp_path,
        parent="missing",
        counters={"input_tokens": 100, "output_tokens": 20},
    )
    data = tracker.collect(upstream, "2026-10-01")
    assert data["usage"]["reasoning"] is None
    assert data["usage"]["cache_read"] is None
    assert any("unavailable" in w for w in data["warnings"])
    assert "unknown" in tracker.dashboard(upstream, data)
    with path.open("a") as stream:
        stream.write('{"broken":\n')
    data = tracker.collect(upstream, "2026-10-01")
    assert any("malformed" in w for w in data["warnings"])
    assert any("Coverage is incomplete" in w for w in data["warnings"])


def test_offline_runtime_bounded_reports_and_html(tracker, tmp_path, monkeypatch):
    tracker, upstream = tracker
    rollout(upstream, tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "network or subprocess invoked during collection/rendering"
        )

    # Positive control: the interception detects an attempted connection.
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    with pytest.raises(AssertionError):
        socket.socket().connect(("127.0.0.1", 1))
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    data, page, _ = tracker.refresh("2026-10-01", None)
    rendered = page.read_text()
    assert "&lt;script&gt;" in rendered
    for external in (
        "<script",
        "<iframe",
        'src="http',
        'href="http',
        "url(http",
        "Estimated cost",
        "Est. cost",
    ):
        assert external not in rendered
    assert "Reasoning subset" in rendered
    assert "Daily recorded tokens" in rendered
    assert data["activity"]["repeated_reads"] is None
    assert data["activity"]["large_tool_outputs"] is None
    data["sessions"][0]["label"] = "界" * 10000
    data["warnings"] = ["界" * 10000]
    assert len(tracker.report(data).encode()) <= 4096
    assert "abbreviated" in tracker.report(data)
    monkeypatch.setattr(tracker, "collect", forbidden)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "report", "--since", "2026-10-01"])
    assert tracker.main() == 0


def test_install_idempotence_and_modified_source_protection(tracker):
    tracker, _ = tracker
    assert tracker.install() == "already installed"
    source = tracker.runtime_dir() / "token_usage.py"
    source.write_text(source.read_text() + "\n# local edit\n")
    with pytest.raises(ValueError, match="modified"):
        tracker.install()


def test_hook_bytes_are_separate_and_receipts_identify_projects(tracker, tmp_path):
    tracker, upstream = tracker
    rollout(upstream, tmp_path)
    hooks = tmp_path / ".cache/quality-sessions"
    hooks.mkdir(parents=True)
    (hooks / "state.json").write_text(
        json.dumps({"history": [{"emitted_bytes": 300}, {"emitted_bytes": 400}]})
    )
    receipt = tmp_path / ".cache/vibehelper-install/codex.json"
    receipt.parent.mkdir()
    receipt.write_text("{}")
    data = tracker.collect(upstream, "2026-10-01")
    assert data["projects"][0]["vibehelper"]
    assert data["projects"][0]["hook_output"]["emitted_bytes"] == 700
    assert data["usage"]["input_total"] == 100


def test_empty_corpus_is_unknown_and_refresh_failure_preserves_report(
    tracker, monkeypatch
):
    tracker, _ = tracker
    data, page, _ = tracker.refresh("2026-10-01", None)
    assert data["usage"]["input_total"] is None
    before = page.read_bytes()

    def fail(*args):
        raise OSError("fixture read failure")

    monkeypatch.setattr(tracker, "collect", fail)
    with pytest.raises(OSError):
        tracker.refresh("2026-10-01", None)
    assert page.read_bytes() == before


def test_unmeasured_turns_and_days_remain_visible(tracker, tmp_path):
    tracker, upstream = tracker
    path = rollout(upstream, tmp_path)
    append(
        path,
        event(
            "event_msg",
            {"type": "task_started", "turn_id": "unmeasured"},
            day="2026-10-02",
        ),
        event(
            "response_item",
            {
                "type": "message",
                "role": "user",
                "content": [{"text": "A pending task"}],
            },
            day="2026-10-02",
        ),
    )
    data = tracker.collect(upstream, "2026-10-01")
    assert len(data["turns"]) == 2
    assert data["measurements"] == {"partial": 1}
    assert data["usage"]["input_total"] == 100
    assert data["sessions"][0]["usage"]["input_total"] == 100
    rendered = tracker.dashboard(upstream, data)
    assert "2026-10-02: unknown" in rendered
    assert "A pending task" in rendered


def test_quota_snapshot_is_separate_and_timestamped(tracker, tmp_path):
    tracker, upstream = tracker
    path = rollout(upstream, tmp_path)
    limits = {"primary": {"used_percent": 12.5, "window_minutes": 300}}
    append(
        path,
        event(
            "event_msg", {"type": "token_count", "info": None, "rate_limits": limits}
        ),
    )
    data = tracker.collect(upstream, "2026-10-01")
    assert data["quota"]["rate_limits"] == limits
    assert data["quota"]["timestamp"] == "2026-10-01T10:00:00Z"
    assert data["usage"]["input_total"] == 100
    assert "Historical host data" in tracker.dashboard(upstream, data)


def test_latest_user_message_identifies_turn(tracker, tmp_path):
    tracker, upstream = tracker
    path = rollout(upstream, tmp_path)
    append(
        path,
        event(
            "response_item",
            {
                "type": "message",
                "role": "user",
                "content": [{"text": "Actual task after injected instructions"}],
            },
        ),
    )
    data = tracker.collect(upstream, "2026-10-01")
    assert data["turns"][0]["label"] == "Actual task after injected instructions"


def test_scoped_refresh_preserves_other_assets_and_customizations(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "managed_usage_install", ROOT / "templates/vibe_scripts/managed-install.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = tmp_path / ".cache/vibehelper-install/codex.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}")
    other = tmp_path / "AGENTS.md"
    other.write_text("user instructions\n")
    module.install_usage_tracker(tmp_path, "codex")
    target = tmp_path / "vibe_scripts/codex/usage-tracker.py"
    assert target.read_bytes() == SCRIPT.read_bytes()
    module.install_usage_tracker(tmp_path, "codex")
    assert other.read_text() == "user instructions\n"
    target.write_text("custom implementation")
    with pytest.raises(ValueError, match="Preserving modified"):
        module.install_usage_tracker(tmp_path, "codex")
    assert target.read_text() == "custom implementation"


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("platform", ["MacOS", "WSL"])
def test_installer_distributes_assets_without_installing_runtime(
    tmp_path, provider, platform
):
    repo, home, tools = (tmp_path / name for name in ("repo", "home", "bin"))
    for directory in (repo, home, tools):
        directory.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    fake = tools / "npx"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env = os.environ | {
        "HOME": str(home),
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
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
    installer = ROOT / f"{provider.title()}Helper" / platform / "part1.sh"
    for extra in ([], ["--refresh-managed"]):
        result = subprocess.run(
            ["bash", str(installer), *options, *extra],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    installed = repo / f"vibe_scripts/{provider}/usage-tracker.py"
    spec = importlib.util.spec_from_file_location("installed_usage_tracker", installed)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.patch_content() == TRACKER.patch_content()
    assert installed.with_suffix(".md").is_file()
    assert not (home / ".local/share/vibehelper/token-usage").exists()
    receipt = json.loads(
        (repo / f".cache/vibehelper-install/{provider}.json").read_text()
    )
    assert receipt[f"vibe_scripts/{provider}/usage-tracker.py"] == TRACKER.fingerprint(
        installed
    )
