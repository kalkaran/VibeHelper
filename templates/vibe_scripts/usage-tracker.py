"""Local Codex usage reports and live dashboard; no model calls or telemetry."""

import argparse
import contextlib
import errno
import hashlib
import html
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import UTC, date, datetime, timedelta
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REVISION = "39cbb737084bf4f8aaa0c17b8cd1c560f6a0a47c"
FILES = {
    "scripts/token_usage.py": "37db6277c90ee639f1283f0ae18fae4d5212bda04ec6bc62a44ae3d799611114",
    "LICENSE": "83457084cc908d87f5badda373a40e0852ce103ce3b9eb1a48890dd6ebd731c9",
}
METRICS = ("input_total", "output", "cache_read", "reasoning")
LIMIT = 4096


def runtime_dir():
    return Path.home() / ".local/share/vibehelper/token-usage"


def cache_dir():
    return Path.home() / ".cache/vibehelper/usage"


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_content():
    text = Path(__file__).with_suffix(".patch").read_text()
    # Managed installers prepend a provider/version comment and normalize newlines.
    if text.startswith(("# CodexHelper-Version:", "# ClaudeHelper-Version:")):
        text = text.split("\n", 1)[1]
    return text.rstrip("\n") + "\n"


def patch_fingerprint():
    return hashlib.sha256(patch_content().encode()).hexdigest()


def load_upstream():
    root = runtime_dir()
    receipt = json.loads((root / "installation.json").read_text())
    if (
        receipt["revision"] != REVISION
        or receipt["patch"] != patch_fingerprint()
        or receipt["script"] != fingerprint(root / "token_usage.py")
    ):
        raise ValueError(
            "Dependency differs from this helper; run usage-tracker.py install"
        )
    spec = importlib.util.spec_from_file_location(
        "vibehelper_token_usage", root / "token_usage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(source=None):
    """Verify pinned source, apply our reviewed patch, then publish a complete install."""
    root = runtime_dir()
    if root.exists():
        receipt = json.loads((root / "installation.json").read_text())
        if receipt["script"] != fingerprint(root / "token_usage.py"):
            raise ValueError(
                "Installed parser was modified; preserve it before reinstalling"
            )
        if receipt["revision"] == REVISION and receipt["patch"] == patch_fingerprint():
            return "already installed"
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".token-usage-", dir=root.parent
    ) as temporary:
        stage = Path(temporary) / "runtime"
        stage.mkdir()
        for name, expected in FILES.items():
            if source:
                content = (source / name).read_bytes()
            else:
                # This import and HTTP request are confined to explicit installation.
                from urllib.request import urlopen

                url = f"https://raw.githubusercontent.com/Wicked-Sick-Ltd/token-usage/{REVISION}/{name}"
                with urlopen(url, timeout=60) as response:
                    content = response.read()
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"Pinned upstream checksum mismatch: {name}")
            (stage / Path(name).name).write_bytes(content)
        result = subprocess.run(
            ["git", "apply", "--no-index", "-"],
            cwd=stage,
            input=patch_content(),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ValueError(f"Upstream patch failed: {result.stderr[:800]}")
        receipt = {
            "revision": REVISION,
            "patch": patch_fingerprint(),
            "script": fingerprint(stage / "token_usage.py"),
        }
        (stage / "installation.json").write_text(json.dumps(receipt) + "\n")
        previous = Path(temporary) / "previous"
        if root.exists():
            root.rename(previous)
        try:
            stage.rename(root)
        except OSError:
            if previous.exists():
                previous.rename(root)
            raise
    return "installed"


def canonical_project(value):
    return str(Path(value).expanduser().resolve()) if value else None


def sum_usage(usages):
    usages = [u for u in usages if u.get("input_total") is not None]
    if not usages:
        return dict.fromkeys(METRICS)
    return {
        field: None
        if any(u.get(field) is None for u in usages)
        else sum(u[field] for u in usages)
        for field in METRICS
    }


def weight(row):
    u = row["usage"]
    return (u.get("input_total") or 0) + (u.get("output") or 0)


def rows_by(turns, dimension):
    groups = {}
    for turn in turns:
        if dimension == "model":
            items = [
                (
                    name,
                    {
                        "usage": usage,
                        **{k: turn[k] for k in ("session_id", "measurement")},
                    },
                )
                for name, usage in turn["models"].items()
            ]
        else:
            items = [(turn[dimension], turn)]
        for key, item in items:
            groups.setdefault(key or "unknown", []).append(item)
    return sorted(
        [
            {
                "key": key,
                "usage": sum_usage(i["usage"] for i in items),
                "calls": len(items),
                "sessions": len({i["session_id"] for i in items}),
                "measurement": "partial"
                if any(i["measurement"] != "exact" for i in items)
                else "exact",
            }
            for key, items in groups.items()
        ],
        key=weight,
        reverse=True,
    )


def hook_measurements(project):
    """Reuse existing emitted-byte records; their time scope is separate from tokens."""
    records, errors = [], []
    for path in (Path(project) / ".cache/quality-sessions").glob("*.json"):
        try:
            history = json.loads(path.read_text()).get("history", [])
            if not isinstance(history, list):
                raise TypeError("invalid history")
            records.extend(history)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append(f"{path.name}: {type(exc).__name__}")
    values = [r.get("emitted_bytes") if isinstance(r, dict) else None for r in records]
    return {
        "events": len(records),
        "emitted_bytes": sum(values)
        if values and all(type(v) is int and v >= 0 for v in values)
        else None,
        "scope": "all retained hook records; not the token date window",
        "errors": errors,
    }


def collect(upstream, since, project=None):
    adapter = upstream.CodexAdapter()
    warnings, sessions, turns, metadata = [], [], [], {}
    quotas = []
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        upstream.check_codex_root(warnings)
        sources = list(adapter.all_sessions(warnings=warnings))
        for source in sources:
            try:
                meta = upstream.codex_metadata(source)
                sid = meta.get("id") or meta.get("session_id")
                metadata[sid] = meta
                cwd = canonical_project(meta.get("cwd"))
                if project and cwd != project:
                    continue
                # Parse each authoritative session once. Children retain their own cwd.
                parsed = upstream.parse_codex_session(source)
                if parsed.get("quota_snapshot"):
                    quotas.append({"session_id": sid, **parsed["quota_snapshot"]})
                selected = []
                for index, segment in enumerate(parsed["segments"]):
                    timestamp = segment["start_ts"]
                    if timestamp and timestamp[:10] < since:
                        continue
                    if not timestamp:
                        warnings.append(
                            f"{sid}: turn has no timestamp; included with unknown date"
                        )
                    models = segment["by_model"]
                    if (
                        not models
                        and not segment["prompt"]
                        and any(
                            other is not segment
                            and other.get("turn_id") == segment.get("turn_id")
                            and other["by_model"]
                            for other in parsed["segments"]
                        )
                    ):
                        continue
                    usage = (
                        sum_usage(models.values()) if models else dict.fromkeys(METRICS)
                    )
                    turn = {
                        "session_id": sid,
                        "turn_id": segment.get("turn_id"),
                        "index": index,
                        "project": cwd,
                        "day": timestamp[:10] if timestamp else None,
                        "timestamp": timestamp,
                        "label": segment["prompt"] or segment["label"],
                        "usage": usage,
                        "models": {
                            m: {k: u[k] for k in METRICS} for m, u in models.items()
                        },
                        "measurement": parsed["measurement"]
                        if models
                        else "activity_only",
                        "source": str(source),
                    }
                    selected.append(turn)
                if not selected:
                    continue
                turns.extend(selected)
                measurement = parsed["measurement"]
                if (
                    any(t["usage"]["input_total"] is None for t in selected)
                    and measurement != "activity_only"
                ):
                    measurement = "partial"
                    warnings.append(
                        f"{sid}: unmeasured turns retained; session totals are a recorded subtotal"
                    )
                sessions.append(
                    {
                        "key": sid,
                        "session_id": sid,
                        "parent_id": upstream.codex_parent(meta),
                        "project": cwd,
                        "source": str(source),
                        "measurement": measurement,
                        "label": next(
                            (
                                t["label"]
                                for t in selected
                                if t["usage"]["input_total"] is not None
                            ),
                            selected[0]["label"],
                        ),
                        "usage": sum_usage(t["usage"] for t in selected),
                        "calls": len(selected),
                        "turns": selected,
                    }
                )
                warnings.extend(
                    f"{sid}: {w}"
                    for w in parsed["warnings"]
                    if not w.startswith("Codex attribution")
                )
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                warnings.append(
                    f"{source}: incomplete/unreadable session ({type(exc).__name__})"
                )
    warnings.extend(captured.getvalue().splitlines())
    counts = {}
    for session in sessions:
        level = session["measurement"]
        counts[level] = counts.get(level, 0) + 1
        parent = session["parent_id"]
        if parent and parent not in metadata:
            warnings.append(f"{session['session_id']}: parent {parent} is unavailable")
    projects = rows_by(turns, "project")
    for row in projects:
        root = Path(row["key"])
        row["vibehelper"] = any(
            (root / f".cache/vibehelper-install/{p}.json").is_file()
            for p in ("codex", "claude")
        )
        row["hook_output"] = hook_measurements(root)
        row["calls"] = row["sessions"]
    # An unmeasured session must not erase the known subtotal from other sessions.
    measured = [t["usage"] for t in turns if t["usage"]["input_total"] is not None]
    total = sum_usage(measured) if measured else dict.fromkeys(METRICS)
    warnings.append(
        "Recorded tokens are not subscription allowance percentages. Cached input and reasoning are subsets."
    )
    if any(s["measurement"] != "exact" for s in sessions) or any(
        "incomplete" in w for w in warnings
    ):
        warnings.append(
            "Coverage is incomplete: totals are recorded subtotals, not complete consumption."
        )
    return {
        "schema": 1,
        "revision": REVISION,
        "generated_at": datetime.now(UTC).isoformat(),
        "since": since,
        "project": project,
        "codex_home": str(upstream.codex_home()),
        "usage": total,
        "measurements": counts,
        "warnings": list(dict.fromkeys(warnings)),
        "projects": projects,
        "models": rows_by(turns, "model"),
        "days": rows_by(turns, "day"),
        "sessions": sorted(sessions, key=weight, reverse=True),
        "turns": sorted(turns, key=weight, reverse=True),
        "activity": {
            "repeated_reads": None,
            "large_tool_outputs": None,
            "note": "The pinned Codex adapter does not expose tool-output sizes or repeated reads. Hook bytes are separate indicators, not token charges.",
        },
        "quota": max(quotas, key=lambda q: q["timestamp"] or "") if quotas else None,
    }


def project_view(data, project):
    if not project:
        return data
    projects = [r for r in data["projects"] if r["key"] == project]
    if not projects:
        raise KeyError(project)
    sessions = [s for s in data["sessions"] if (s["project"] or "unknown") == project]
    turns = [t for t in data["turns"] if (t["project"] or "unknown") == project]
    measurements = {}
    for session in sessions:
        level = session["measurement"]
        measurements[level] = measurements.get(level, 0) + 1
    return {
        **data,
        "project": project,
        "projects": projects,
        "sessions": sessions,
        "turns": turns,
        "usage": sum_usage(t["usage"] for t in turns),
        "measurements": measurements,
        "models": rows_by(turns, "model"),
        "days": rows_by(turns, "day"),
        "warnings": ["Collector-wide note: " + w for w in data["warnings"]],
    }


def dashboard(upstream, data, live=False):
    # Reuse upstream layout, tables and daily chart; never call its price-based history scans.
    total = data["usage"]
    payload = {
        "runtime": "codex",
        "since": data["since"],
        "project": data["project"],
        "summary": {
            "usage": total,
            "sessions": len(data["sessions"]),
            "measurement": "partial"
            if any(k != "exact" for k in data["measurements"])
            or len(data["warnings"]) > 1
            else "exact",
        },
        "measurements": data["measurements"],
        "warnings": data["warnings"],
        "top_projects": data["projects"],
        "top_models": data["models"],
        "top_commands": [
            {
                **t,
                "key": f"{t['session_id']} / {t['turn_id'] or t['index']}: {t['label']}",
                "calls": 1,
            }
            for t in data["turns"][:10]
        ],
        "by_day": sorted(data["days"], key=lambda r: r["key"]),
    }
    page = upstream.render_dashboard(payload, data["generated_at"])
    if data["quota"]:
        snapshot = html.escape(json.dumps(data["quota"], indent=2))
        page = page.replace(
            "</body>",
            "<section><h2>Recorded quota snapshot</h2><p>Historical host data, separate from token totals and shared across projects. No remaining allowance is inferred.</p><pre>"
            + snapshot
            + "</pre></section></body>",
        )
    details = [
        "<section><h2>Sessions and turns</h2><p>Each session shows its own usage. Child sessions are counted once under their recorded project.</p>"
    ]
    if live:
        ranked = []
        for session in data["sessions"][:10]:
            anchor = hashlib.sha256(session["session_id"].encode()).hexdigest()[:16]
            total = None if session["usage"]["input_total"] is None else weight(session)
            ranked.append(
                f'<tr><td><a href="#session-{anchor}">{html.escape(session["label"] or "Untitled session")}</a>'
                f"<small>{html.escape(session['session_id'])}</small></td>"
                f"<td>{upstream.fmt_tokens(total)}</td><td>{upstream.fmt_tokens(session['usage']['input_total'])}</td>"
                f"<td>{upstream.fmt_tokens(session['usage']['output'])}</td><td>{session['calls']}</td></tr>"
            )
        page = page.replace(
            "</body>",
            '<section aria-label="Largest sessions"><h2>Largest sessions</h2>'
            "<p>Ranked by recorded input + output. Open a session to inspect its turns.</p>"
            "<table><thead><tr><th>Session</th><th>Recorded tokens</th><th>Input incl. cache</th><th>Output</th><th>Turns</th></tr></thead><tbody>"
            + (
                "".join(ranked)
                or '<tr><td colspan="5">No sessions in this window.</td></tr>'
            )
            + "</tbody></table></section></body>",
        )
    for session in data["sessions"]:
        label = html.escape(
            f"{session['session_id']} — {session['label']} — {session['project'] or 'unknown project'}"
        )
        parent = html.escape(session["parent_id"] or "none recorded")
        rows = [
            {**t, "key": f"{t['turn_id'] or t['index']}: {t['label']}", "calls": 1}
            for t in session["turns"]
        ]
        details.append(
            f'<details id="session-{hashlib.sha256(session["session_id"].encode()).hexdigest()[:16]}"><summary>{label} — {upstream.fmt_tokens(None if session["usage"]["input_total"] is None else weight(session))} recorded tokens ({html.escape(session["measurement"])})</summary><p>Parent: {parent}</p>'
        )
        details.append(
            "<table><thead><tr><th>Turn</th><th>Count</th><th>Output</th><th>Input incl. cache</th><th>Cached subset</th><th>Reasoning subset</th></tr></thead><tbody>"
        )
        details.append(upstream._dashboard_table_rows(rows))
        details.append("</tbody></table></details>")
    details.append(
        "</section><section><h2>Context indicators</h2><p>"
        + html.escape(data["activity"]["note"])
        + "</p><ul>"
    )
    for row in data["projects"]:
        output = row["hook_output"]
        detail = f"{row['key']}: VibeHelper {'installed' if row['vibehelper'] else 'not identified'}; {output['events']} retained hook events, {output['emitted_bytes'] if output['emitted_bytes'] is not None else 'unknown'} emitted bytes. {output['scope']}."
        details.append("<li>" + html.escape(detail) + "</li>")
    details.append("</ul></section>")
    return page.replace("</body>", "\n".join(details) + "\n</body>")


def saved_paths(since, project):
    key = hashlib.sha256(json.dumps([since, project]).encode()).hexdigest()[:16]
    base = cache_dir() / key
    return base.with_suffix(".json"), base.with_suffix(".html")


def refresh(since, project):
    start = time.monotonic()
    upstream = load_upstream()
    data = collect(upstream, since, project)
    page = dashboard(upstream, data)
    report_path, page_path = saved_paths(since, project)
    cache_dir().mkdir(parents=True, exist_ok=True)
    upstream.atomic_write_text(report_path, json.dumps(data, ensure_ascii=False) + "\n")
    upstream.atomic_write_text(page_path, page)
    upstream.atomic_write_text(cache_dir() / "dashboard.html", page)
    return data, page_path, time.monotonic() - start


def report(data):
    u = data["usage"]

    def number(key):
        return f"{u[key]:,}" if u[key] is not None else "unknown"

    lines = [
        f"Codex usage | saved {data['generated_at']} | since {data['since']}",
        f"Project: {data['project'] or 'all local projects'}",
        f"Input {number('input_total')} (cached subset {number('cache_read')}); output {number('output')} (reasoning subset {number('reasoning')}).",
        f"Coverage: {len(data['sessions'])} sessions; {data['measurements']}; {len(data['warnings'])} notes.",
        "Largest sessions (own recorded input + output; children listed separately):",
    ]
    for session in data["sessions"][:5]:
        lines.append(
            f"- {weight(session) if session['usage']['input_total'] is not None else 'unknown'}: {session['session_id']} | {session['project']} | {' '.join(session['label'].split())[:120]}"
        )
    lines.extend(data["warnings"][:3])
    lines.append(
        "Next: inspect the largest session in the dashboard; size alone does not prove waste."
    )
    lines.append(f"Dashboard: {saved_paths(data['since'], data['project'])[1]}")
    text = "\n".join(lines)
    suffix = "\n[Report abbreviated; full details are in the saved dashboard.]\n"
    encoded = text.encode("utf-8")
    return (
        text + "\n"
        if len(encoded) + 1 <= LIMIT
        else encoded[: LIMIT - len(suffix.encode())].decode("utf-8", errors="ignore")
        + suffix
    )


def app_dir():
    return Path.home() / ".local/share/vibehelper/usage-dashboard"


def settings():
    path = app_dir() / "settings.json"
    value = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(value, dict):
        raise TypeError(f"Invalid dashboard settings: {path}")
    return value


def setup_dashboard(codex_home=None, port=None, source=None):
    saved = settings()
    home = (
        Path(
            codex_home
            or saved.get("codex_home")
            or os.environ.get("TOKEN_USAGE_CODEX_HOME")
            or os.environ.get("CODEX_HOME")
            or Path.home() / ".codex"
        )
        .expanduser()
        .resolve()
    )
    if not home.is_dir():
        raise ValueError(f"Codex data directory does not exist: {home}")
    port = port if port is not None else saved.get("port", 8766)
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Dashboard port must be between 0 and 65535")
    root = app_dir()
    launcher = Path.home() / ".local/bin/vibe-usage"
    command = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(root / "usage-tracker.py"))} serve --open "$@"\n'
    files = {
        root / "usage-tracker.py": Path(__file__).read_bytes(),
        root / "usage-tracker.patch": patch_content().encode(),
        launcher: command.encode(),
    }
    if sys.platform == "darwin":
        files[Path.home() / "Applications/VibeHelper Usage.command"] = command.encode()
    receipt_path = root / "installation.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    for path, content in files.items():
        if (
            path.exists()
            and path.read_bytes() != content
            and receipt.get(str(path)) != fingerprint(path)
        ):
            raise ValueError(f"Preserving modified or unmanaged dashboard file: {path}")
    install(source)
    upstream = load_upstream()
    root.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        upstream.atomic_write_text(path, content.decode())
        path.chmod(0o755 if path == launcher or path.suffix == ".command" else 0o600)
    upstream.atomic_write_text(
        root / "settings.json",
        json.dumps({"codex_home": str(home), "port": port}) + "\n",
    )
    (root / "settings.json").chmod(0o600)
    upstream.atomic_write_text(
        receipt_path, json.dumps({str(p): fingerprint(p) for p in files}) + "\n"
    )
    return launcher, home


class LiveDashboard:
    """One cached view; only changed rollout files are reparsed by upstream."""

    def __init__(self, upstream, since=None, project=None):
        self.upstream, self.since, self.project = upstream, since, project
        self.lock = threading.Lock()
        self.busy = False
        self.error = None
        self.version = 0
        self.data = None
        self.page = ""
        self.views = {}
        self.signature = None
        self.next_check = 0
        self.caches = []
        for name in ("codex_metadata", "parse_codex_session"):
            reader = getattr(upstream, name)
            cache = {}
            self.caches.append(cache)

            def cached(path, reader=reader, cache=cache):
                path = Path(path)
                info = path.stat()
                stamp = (info.st_mtime_ns, info.st_ctime_ns, info.st_size)
                if path not in cache or cache[path][0] != stamp:
                    cache[path] = (stamp, reader(path))
                return cache[path][1]

            setattr(upstream, name, cached)

    def refresh(self):
        try:
            home = self.upstream.codex_home()
            if not home.is_dir():
                raise ValueError(f"Codex data directory is unavailable: {home}")
            paths = [
                p
                for folder in ("sessions", "archived_sessions")
                for p in (home / folder).rglob("*.jsonl")
            ]
            stamps = {}
            for path in paths:
                info = path.stat()
                stamps[str(path)] = (info.st_mtime_ns, info.st_ctime_ns, info.st_size)
            since = self.since or (date.today() - timedelta(days=30)).isoformat()
            signature = (since, stamps)
            if signature != self.signature:
                for cache in self.caches:
                    for path in set(cache) - set(paths):
                        del cache[path]
                data = collect(self.upstream, since, self.project)
                page = dashboard(self.upstream, data, live=True)
                with self.lock:
                    self.data, self.page = data, page
                    self.views = {}
                    self.version += 1
                    self.signature = signature
            with self.lock:
                self.error = None
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
        ) as exc:
            with self.lock:
                self.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        finally:
            with self.lock:
                self.busy = False

    def poll(self):
        with self.lock:
            if not self.busy and time.monotonic() >= self.next_check:
                self.busy = True
                self.next_check = time.monotonic() + 5
                threading.Thread(target=self.refresh, daemon=True).start()
            return {
                "version": self.version,
                "busy": self.busy,
                "error": self.error,
                "updated": self.data["generated_at"] if self.data else None,
                "since": self.data["since"] if self.data else self.since,
                "scope": self.project,
                "projects": sorted(r["key"] for r in self.data["projects"])
                if self.data
                else [],
            }

    def view(self, project):
        with self.lock:
            if not project:
                return self.page, self.version
            if project in self.views:
                return self.views[project], self.version
            data, version = self.data, self.version
        if data is None:
            raise KeyError(project)
        page = dashboard(self.upstream, project_view(data, project), live=True)
        with self.lock:
            if self.version == version:
                self.views[project] = page
        return page, version


def live_page(home):
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live Codex usage</title><style id="report-style"></style><style>
body {font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#171717;background:#fff}
h1 {font-size:1.8rem;letter-spacing:-.03em;margin-bottom:.35rem}
.intro {color:#444;margin-top:0}.project-control {margin:1.5rem 0 1rem}
.project-control label {display:block;font-weight:650;margin-bottom:.4rem}
select {font:inherit;min-height:44px;width:min(100%,36rem);border:1px solid #777;border-radius:4px;background:#fff;color:#171717;padding:.55rem 2rem .55rem .7rem}
#project-path {font-size:.85rem;color:#444;overflow-wrap:anywhere;margin:.5rem 0}
.live-status {display:flex;flex-wrap:wrap;gap:.4rem 1.5rem;align-items:baseline;border-bottom:1px solid #ccc;padding-bottom:.8rem;margin-bottom:1.5rem}
.live-status p {margin:0;font-size:.85rem}.live-status code {overflow-wrap:anywhere}
#live-state {font-weight:600}.live-status .source {color:#444}
:focus-visible {outline:2px solid #245b91;outline-offset:3px}
#report .cards {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;border-block:1px solid #ddd}
#report .card {border:0;border-radius:0;background:transparent;padding:1rem .75rem}
#report section {margin-top:2rem}#report small {display:block;color:#555;font-size:.75rem;margin-top:.25rem;overflow-wrap:anywhere}
#report a {color:#164d83;text-underline-offset:.15em}#report td:first-child {min-width:15rem;max-width:36rem;overflow-wrap:anywhere}
.table-scroll {overflow-x:auto}#report table {min-width:620px}#report pre {overflow-x:auto}
@media(max-width:600px) {body{margin:1rem auto}#report .cards{grid-template-columns:repeat(2,minmax(0,1fr))}h1{font-size:1.5rem}}
</style></head><body><header><h1>Codex token usage</h1>
<p class="intro">Find the projects, sessions and turns using the most tokens.</p>
<div class="project-control"><label for="project">Project</label>
<select id="project" aria-describedby="project-path"><option value="">All projects</option></select>
<p id="project-path">All recorded projects in the selected window.</p></div>
<aside class="live-status" aria-label="Live updates">
<p id="live-state" role="status">Loading usage…</p><p id="window"></p><p class="source">Local logs: <code>"""
        + html.escape(str(home))
        + """</code></p></aside></header>
<main id="report" aria-busy="true"><p>Reading local Codex logs. The first scan may take a few seconds.</p></main>
<script>
let version = 0;
let pending = false;
let renderedProject = null;
let catalog = '';
const selector = document.getElementById('project');
const report = document.getElementById('report');
let project = new URLSearchParams(location.search).get('project') || '';
function setProjects(projects, scope) {
  const signature = JSON.stringify([projects, project, scope]);
  if (signature === catalog) return;
  catalog = signature;
  const basename = path => path === 'unknown' ? 'Unknown project' : path.split('/').filter(Boolean).pop() || path;
  const names = projects.map(basename);
  selector.replaceChildren(new Option(scope ? 'All projects in server scope' : 'All projects', ''));
  projects.forEach(path => {
    const name = basename(path);
    selector.add(new Option(names.filter(n => n === name).length > 1 ? path : name, path));
  });
  if (project && !projects.includes(project)) selector.add(new Option(project + ' (not in this window)', project));
  selector.value = project;
  document.getElementById('project-path').textContent = project === 'unknown' ? 'Sessions without a recorded project path.'
    : project || (scope ? 'Server scope: ' + scope : 'All recorded projects in the selected window.');
}
async function update() {
  if (document.hidden || pending) return;
  pending = true;
  const requestedProject = project;
  const state = document.getElementById('live-state');
  try {
    const response = await fetch('/api/status', {cache:'no-store'});
    if (!response.ok) throw new Error('Dashboard unavailable');
    const status = await response.json();
    if (project !== requestedProject) return;
    setProjects(status.projects, status.scope);
    document.getElementById('window').textContent = status.since ? 'Since ' + status.since : '';
    if (status.version && requestedProject && !status.projects.includes(requestedProject)) {
      report.textContent = 'No recorded usage for this project in the current window. Select another project or All projects.';
      report.setAttribute('aria-busy', 'false');
      state.textContent = 'Project unavailable';
      return;
    }
    if (status.version && (status.version !== version || renderedProject !== requestedProject)) {
      const view = await fetch('/api/view?' + new URLSearchParams({project:requestedProject}), {cache:'no-store'});
      if (!view.ok) throw new Error('Could not load updated usage');
      const parsed = new DOMParser().parseFromString(await view.text(), 'text/html');
      if (project !== requestedProject) return;
      const opened = new Set(Array.from(document.querySelectorAll('details[open]'), el => el.id));
      const focused = document.activeElement.closest('details')?.id;
      const position = window.scrollY;
      parsed.querySelector('h1')?.remove();
      parsed.querySelector('.meta')?.remove();
      if (parsed.body.firstElementChild?.textContent.startsWith('Project filter:')) parsed.body.firstElementChild.remove();
      if (requestedProject) parsed.querySelector('[aria-label="Top projects"]')?.remove();
      const cards = parsed.querySelector('.cards');
      const sections = ['Top projects', 'Largest sessions', 'Largest turns'].map(label => parsed.querySelector('[aria-label="' + label + '"]')).filter(Boolean);
      cards?.after(...sections);
      parsed.querySelectorAll('table').forEach(table => {
        const wrapper = parsed.createElement('div');
        wrapper.className = 'table-scroll';
        wrapper.tabIndex = 0;
        wrapper.setAttribute('role', 'region');
        wrapper.setAttribute('aria-label', (table.closest('section')?.querySelector('h2')?.textContent || 'Usage') + ' table');
        table.before(wrapper);
        wrapper.append(table);
      });
      document.getElementById('report-style').textContent = parsed.querySelector('style')?.textContent || '';
      report.replaceChildren(...parsed.body.childNodes);
      report.setAttribute('aria-busy', 'false');
      document.querySelectorAll('details').forEach(el => {el.open = opened.has(el.id)});
      if (focused) document.getElementById(focused)?.querySelector('summary')?.focus({preventScroll:true});
      window.scrollTo(0, position);
      version = Number(view.headers.get('X-Usage-Version'));
      renderedProject = requestedProject;
    }
    const time = status.updated ? new Date(status.updated).toLocaleTimeString() : '';
    state.textContent = status.error ? 'Refresh failed — ' + status.error + (time ? ' · Showing ' + time : '')
      : status.busy ? (time ? 'Updating · Last updated ' + time : 'Reading local logs…')
      : 'Live · Updated ' + time;
  } catch (error) {
    if (project === requestedProject) {
      state.textContent = 'Could not update — open VibeHelper Usage to reconnect. Retrying every five seconds.';
      report.setAttribute('aria-busy', 'false');
      if (renderedProject !== project) report.textContent = 'Usage could not be loaded for this project.';
    }
  } finally {
    pending = false;
    if (project !== requestedProject) update();
  }
}
selector.addEventListener('change', () => {
  project = selector.value;
  const url = new URL(location.href);
  if (project) url.searchParams.set('project', project); else url.searchParams.delete('project');
  history.replaceState(null, '', url);
  report.textContent = 'Loading project usage…';
  report.setAttribute('aria-busy', 'true');
  renderedProject = null;
  document.getElementById('live-state').textContent = 'Loading project usage…';
  update();
});
report.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#session-"]');
  if (link) {
    const details = document.getElementById(link.hash.slice(1));
    if (details) {
      event.preventDefault();
      details.open = true;
      details.querySelector('summary').focus({preventScroll:true});
      details.scrollIntoView({block:'start'});
    }
  }
});
update();
setInterval(update, 5000);
document.addEventListener('visibilitychange', update);
</script></body></html>"""
    )


def dashboard_server(app, port):
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Dashboard port must be between 0 and 65535")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Polls are not useful logs; errors stay visible in dashboard status.

        def do_GET(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (
                self.headers.get("Host") != origin.removeprefix("http://")
                or self.headers.get("Origin", origin) != origin
            ):
                self.send_error(403)
                return
            kind = "application/json"
            url = urlsplit(self.path)
            if url.path == "/api/health":
                body = json.dumps(
                    {
                        "app": "vibehelper-usage",
                        "home": str(app.upstream.codex_home()),
                        "since": app.since,
                        "project": app.project,
                    }
                )
            elif url.path == "/api/status":
                body = json.dumps(app.poll())
            elif url.path == "/api/view":
                project = parse_qs(url.query, keep_blank_values=True).get(
                    "project", [""]
                )[0]
                try:
                    body, version = app.view(project)
                except KeyError:
                    self.send_error(404, "Project not present in the current window")
                    return
                kind = "text/html"
            elif url.path == "/":
                body, kind = live_page(app.upstream.codex_home()), "text/html"
            else:
                self.send_error(404)
                return
            content = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if url.path == "/api/view":
                self.send_header("X-Usage-Version", str(version))
            self.end_headers()
            self.wfile.write(content)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_dashboard(
    codex_home=None, port=None, since=None, project=None, open_browser=False
):
    saved = settings()
    home = codex_home or saved.get("codex_home")
    if home:
        os.environ["TOKEN_USAGE_CODEX_HOME"] = str(Path(home).expanduser().resolve())
    port = port if port is not None else saved.get("port", 8766)
    upstream = load_upstream()
    app = LiveDashboard(upstream, since, project)
    try:
        server = dashboard_server(app, port)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        connection = HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            existing = json.loads(response.read(2048))
            if response.status != 200 or existing != {
                "app": "vibehelper-usage",
                "home": str(upstream.codex_home()),
                "since": since,
                "project": project,
            }:
                raise ValueError(
                    f"Port {port} is in use; close the existing dashboard or choose --port 0"
                )
        except (OSError, HTTPException, json.JSONDecodeError) as error:
            raise ValueError(
                f"Port {port} is unavailable; choose --port 0 or another port"
            ) from error
        finally:
            connection.close()
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        print(f"Dashboard already running: http://127.0.0.1:{port}/")
        return
    url = f"http://127.0.0.1:{server.server_port}/"
    print(
        f"Live dashboard: {url}\nReading: {upstream.codex_home()}\nClose this terminal or press Ctrl-C to stop.",
        flush=True,
    )
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("install", "setup", "serve", "refresh", "dashboard", "report"),
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="Install from a local pinned upstream checkout (checksums still enforced)",
    )
    parser.add_argument(
        "--project", help="Exact project directory; default: all local projects"
    )
    parser.add_argument(
        "--since",
        help="YYYY-MM-DD; default: 30 days ago",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        help="Codex log directory to save with setup or use with serve",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Local dashboard port; default: 8766; 0 chooses a free port",
    )
    parser.add_argument(
        "--open", action="store_true", help="Open the live dashboard in your browser"
    )
    args = parser.parse_args()
    try:
        since = date.fromisoformat(args.since).isoformat() if args.since else None
        args.since = since or (date.today() - timedelta(days=30)).isoformat()
        project = canonical_project(args.project)
        if args.command == "setup":
            launcher, home = setup_dashboard(args.codex_home, args.port, args.source)
            print(f"Dashboard installed. Reading: {home}\nOpen: {launcher}")
            if sys.platform == "darwin":
                print(
                    f"Or double-click: {Path.home() / 'Applications/VibeHelper Usage.command'}"
                )
        elif args.command == "serve":
            serve_dashboard(args.codex_home, args.port, since, project, args.open)
        elif args.command == "install":
            print(
                f"token-usage {install(args.source)}: {runtime_dir()} ({REVISION[:12]})"
            )
        elif args.command == "report":
            path, _ = saved_paths(args.since, project)
            if not path.is_file():
                raise ValueError(
                    "No saved report for these filters. Run usage-tracker.py refresh with the same --project/--since options."
                )
            print(report(json.loads(path.read_text())), end="")
        else:
            data, path, duration = refresh(args.since, project)
            print(
                f"Refreshed {len(data['sessions'])} sessions in {duration:.2f}s; {len(data['warnings'])} coverage notes.\nDashboard: {path}\nLatest: {cache_dir() / 'dashboard.html'}"
            )
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            f"Usage tracker failed: {str(exc)[:900]}\nNext: verify the local installation and requested paths; install with usage-tracker.py install.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
