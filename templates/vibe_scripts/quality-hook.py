#!/usr/bin/env python3
"""Provider adapters for session-scoped, structured edited-file quality checks."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path


def checker_module():
    spec = importlib.util.spec_from_file_location(
        "edited_checker", Path(__file__).with_name("agent-check-edited.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = None
QUALITY = None


def ensure_helpers():
    global CHECKER, QUALITY
    if CHECKER is None:
        CHECKER = checker_module()
        QUALITY = CHECKER.QUALITY


def project_path(root: Path, value: str) -> str | None:
    ensure_helpers()
    path = Path(value)
    try:
        relative = (root / path).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    name = relative.as_posix()
    return None if CHECKER.is_excluded(name) else name


def payload_paths(root: Path, payload: dict) -> list[str]:
    """Read edit metadata and apply_patch bodies, never arbitrary shell words."""
    names = set()

    def visit(value, key=""):
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str):
            if key in {"file_path", "path", "old_path", "new_path", "destination"}:
                names.add(value)
            if (
                key in {"patch", "input", "command", "cmd", "changes"}
                or "*** Begin Patch" in value
            ):
                names.update(
                    re.findall(
                        r"(?m)^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$",
                        value,
                    )
                )

    visit(payload.get("tool_input", payload.get("input", {})))
    return sorted({name for value in names if (name := project_path(root, value))})


def snapshot(root: Path) -> dict[str, str]:
    ensure_helpers()
    deadline = time.monotonic() + 8
    paths = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=5,
    )
    if paths.returncode:
        raise OSError(
            "Cannot read the Git session baseline; initialize or repair the repository."
        )
    values = {}
    for raw in paths.stdout.split(b"\0"):
        if time.monotonic() >= deadline:
            raise OSError(
                "Session snapshot exceeded 8 seconds; use explicit file verification and inspect repository size."
            )
        if not raw:
            continue
        name = os.fsdecode(raw)
        if CHECKER.is_excluded(name):
            continue
        path = root / name
        if path.is_symlink():
            values[name] = "symlink:" + os.readlink(path)
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    if time.monotonic() >= deadline:
                        raise OSError(
                            "Session snapshot exceeded 8 seconds; use explicit file verification and inspect large files."
                        )
                    digest.update(chunk)
            values[name] = digest.hexdigest()
        else:
            values[name] = "absent"
    return values


def changed_since(baseline: dict[str, str], current: dict[str, str]) -> list[str]:
    return sorted(
        name
        for name in baseline.keys() | current.keys()
        if baseline.get(name, "absent") != current.get(name, "absent")
    )


def without_handlers(text: str) -> str:
    """Ignore event callbacks and tracking metadata, including balanced JSX values."""
    pattern = r"(?:\bon[\w:|]+|\bdata-(?:track|analytics|gtm)[\w-]*|@[\w:.-]+)\s*=\s*"
    for match in reversed(list(re.finditer(pattern, text))):
        start = end = match.end()
        if start >= len(text):
            continue
        quote, depth = None, 0
        if text[start] in "\"'`{":
            for end in range(start, len(text)):
                char = text[end]
                if quote:
                    if char == quote and (end == 0 or text[end - 1] != "\\"):
                        quote = None
                elif char in "\"'`":
                    quote = char
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                if quote is None and depth == 0:
                    end += 1
                    break
        else:
            while end < len(text) and not text[end].isspace() and text[end] != ">":
                end += 1
        text = text[: match.start()] + text[end:]
    return text


class DesignContent(HTMLParser):
    """Source-level markup projection, not a browser or a JavaScript evaluator."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.depth = 0
        self.tags = set()

    def handle_starttag(self, tag, attrs):
        if tag not in self.tags:
            return
        self.parts.append((tag, sorted(attrs, key=lambda item: item[0])))
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        self.parts.append((tag, sorted(attrs, key=lambda item: item[0])))

    def handle_endtag(self, tag):
        if tag not in self.tags:
            return
        self.parts.append(("/" + tag, []))
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.depth and data.strip():
            self.parts.append(" ".join(data.split()))


def jsx_markup(text: str, deadline: float) -> str:
    """Extract JSX roots in expression positions; skip JS strings and comparisons."""
    parts = []
    index = 0
    while index < len(text):
        if time.monotonic() >= deadline:
            raise OSError("JSX source inspection exceeded the hook time budget")
        if text[index] in "\"'`":
            quote = text[index]
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                    continue
                index += 1
                if text[index - 1] == quote:
                    break
            continue
        if text[index] != "<" or (
            text[:index].strip()
            and not re.search(
                r"(?:\breturn|=>|=|\?|:|\(|\[|,|&&|\|\|)\s*$", text[:index]
            )
        ):
            index += 1
            continue
        opening = re.match(r"<([A-Za-z][\w:.-]*)(?=[\s/>])", text[index:])
        fragment = text.startswith("<>", index)
        if not opening and not fragment:
            index += 1
            continue
        tag = "" if fragment else opening[1]
        tokens = re.finditer(
            r"</?" + re.escape(tag) + (r"(?=[\s/>])[^>]*>" if tag else r">"),
            text[index:],
        )
        depth = 0
        end = None
        for token in tokens:
            if time.monotonic() >= deadline:
                raise OSError("JSX source inspection exceeded the hook time budget")
            if token[0].startswith("</"):
                depth -= 1
            elif not token[0].endswith("/>"):
                depth += 1
            if depth == 0:
                end = index + token.end()
                break
        if end is None:
            index += 1
            continue
        parts.append(
            text[index:end].replace("<>", "<fragment>").replace("</>", "</fragment>")
        )
        index = end
    return "\n".join(parts)


def design_fingerprint(
    path: Path, deadline: float | None = None, max_bytes: int = 131072
) -> str:
    deadline = deadline if deadline is not None else time.monotonic() + 8
    content = bytearray()
    with path.open("rb") as stream:
        while True:
            if time.monotonic() >= deadline:
                raise OSError("Design source inspection exceeded the hook time budget")
            chunk = stream.read(min(65536, max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > max_bytes:
                raise OSError("Design source exceeds Impeccable hook maxFileBytes")
    text = content.decode(errors="replace")
    if path.suffix.lower() in {".css", ".scss", ".sass", ".less"}:
        parts = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL).strip() or []
    else:
        text = re.sub(
            r"<script\b[^>]*>.*?</script\s*>", "", text, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(r"<!--.*?-->|/\*.*?\*/", "", text, flags=re.DOTALL)
        if path.suffix.lower() in {".js", ".ts", ".jsx", ".tsx"}:
            text = re.sub(r"(?m)^\s*//.*$", "", text)
            text = jsx_markup(without_handlers(text), deadline)
        parser = DesignContent()
        parser.tags = set(re.findall(r"</([\w:.-]+)\s*>", text.lower())) | {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
        parser.feed(without_handlers(text))
        parts = parser.parts
    if time.monotonic() >= deadline:
        raise OSError("Design source inspection exceeded the hook time budget")
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def design_snapshot(
    root: Path, paths: list[str], remaining: float = 8
) -> dict[str, str | None]:
    deadline = time.monotonic() + min(8, max(0, remaining))
    max_bytes = 131072  # Impeccable's existing automatic-hook limit.
    for filename in ("config.json", "config.local.json"):
        try:
            options = json.loads((root / ".impeccable" / filename).read_text())
            configured = (
                options.get("hook", {}).get("limits", {}).get("maxFileBytes", max_bytes)
            )
            if type(configured) is int and configured > 0:
                max_bytes = configured
        except (OSError, ValueError, AttributeError):
            pass  # Malformed configuration is reported only when design work needs review.
    values = {}
    for name in paths:
        path = root / name
        if (
            path.suffix.lower() not in CHECKER.FRONTEND_DESIGN_EXTS
            or not path.is_file()
            or path.is_symlink()
        ):
            continue
        try:
            values[name] = design_fingerprint(path, deadline, max_bytes)
        except OSError:
            values[name] = None
    return values


def design_enabled(root: Path) -> bool:
    if os.environ.get("IMPECCABLE_HOOK_DISABLED", "").lower() in {"1", "true", "yes"}:
        return False
    options = {}
    for name in ("config.json", "config.local.json"):
        path = root / ".impeccable" / name
        if path.is_file():
            options.update(json.loads(path.read_text()).get("hook", {}))
    return (
        options.get("enabled") is not False
        and options.get("consent", "accepted") == "accepted"
    )


def review_design(root: Path, paths: list[str], state: dict, remaining: float):
    started = time.monotonic()
    executable = CHECKER.local_impeccable_command(root)
    if not executable:
        for scope in (".agents", ".claude", ".codex"):
            launcher = root / scope / "skills/impeccable/scripts/impeccable"
            if launcher.is_file() and os.access(launcher, os.X_OK):
                executable = [str(launcher)]
                break
    if not executable and not any(
        (root / scope / "skills/impeccable").exists()
        for scope in (".agents", ".claude", ".codex")
    ):
        return None
    try:
        if not design_enabled(root):
            return None
    except (OSError, ValueError, TypeError, AttributeError):
        pass  # Report malformed settings below only if UI work requires review.
    current = design_snapshot(root, paths, remaining - 2)
    if not current:
        return None
    baseline = state.get("design_baseline")
    if not isinstance(baseline, dict):
        if all(
            digest == hashlib.sha256(b"[]").hexdigest() for digest in current.values()
        ) or not design_enabled(root):
            return None
        return QUALITY.result(
            "not_checked",
            "UI design review",
            [
                "Design baseline unavailable; start a new session before automatic design review. No whole-project scan was run."
            ],
        )
    unavailable = [
        name
        for name, digest in current.items()
        if digest is None or (name in baseline and baseline[name] is None)
    ]
    if unavailable and design_enabled(root):
        return QUALITY.result(
            "not_checked",
            "UI design review",
            [
                "Design source inspection unavailable within the hook budget/maxFileBytes; review these files explicitly: "
                + ", ".join(unavailable)
            ],
        )
    reviewed = state.setdefault("design_reviewed", {})
    selected = [
        name
        for name, digest in current.items()
        if digest != baseline.get(name, hashlib.sha256(b"[]").hexdigest())
        and digest != reviewed.get(name)
    ]
    if not selected:
        return None
    if not design_enabled(root):
        return None
    if not executable:
        return QUALITY.result(
            "not_checked",
            "UI design review",
            [
                "UI markup/styles changed, but Impeccable is unavailable. Review these UI changes explicitly: "
                + ", ".join(selected)
            ],
        )
    remaining -= time.monotonic() - started
    if remaining <= 2:
        return QUALITY.result(
            "not_checked",
            "UI design review",
            [
                "Design review deferred: hook time budget exhausted. Recheck before completing."
            ],
        )
    value = QUALITY.run_quality(
        [*executable, "detect", *selected],
        cwd=root,
        label="UI design review",
        timeout=min(remaining - 1, 30),
        max_lines=25,
    )
    if value["status"] == "passed":
        reviewed.update({name: current[name] for name in selected})
    return value


def merge_review(value: dict, review: dict | None) -> dict:
    if review is None:
        return value
    priority = {"passed": 0, "findings": 1, "not_checked": 2, "tool_failure": 3}
    value["status"] = max((value["status"], review["status"]), key=priority.get)
    value["summary"].extend(
        [f"UI design review: {review['status']}", *review["summary"]]
    )
    if review.get("log"):
        value["summary"].append(f"Full log: {review['log']}")
    for metric in ("command_count", "duration_seconds", "output_bytes"):
        value[metric] += review[metric]
    value["diagnostic_signature"] = hashlib.sha256(
        json.dumps(
            [
                value.get("diagnostic_signature"),
                review.get("diagnostic_signature"),
                review["status"],
            ]
        ).encode()
    ).hexdigest()
    return value


def response(provider: str, event: str, value: dict, attempts: int = 0) -> dict:
    ensure_helpers()
    message = QUALITY.render_result(value, 22)
    if event == "PostToolUse":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": message,
            }
        }
    if event == "Stop" and value["status"] != "passed":
        if value.get("explicitly_skipped"):
            return {}
        if attempts >= 3:
            message += (
                f"\nReassessment after {attempts} unsuccessful final rechecks: the diagnostic signature is unchanged. "
                f"Likely category: {value['status']}. Inspect the first failing command's full log and its tool prerequisites before another edit. "
                "Use compaction only for context loss; use an agent for a separable investigation. DNS, authentication and permissions require fixing the environment."
            )
        if attempts >= 4:
            # End the automatic loop explicitly without turning unresolved work into success.
            return {
                "continue": False,
                "stopReason": "Verification remains blocked/unverified.\n" + message,
            }
        return {
            "decision": "block",
            "reason": "Correct the findings and recheck before completing.\n" + message,
        }
    return {}


def acquire_lock(stream, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise OSError(
                    "Another hook is checking this session; wait for it before rechecking."
                )
            time.sleep(0.05)


def unavailable_response(event: str, message: str, repeated: bool = False) -> dict:
    if event == "Stop":
        if repeated:
            return {
                "continue": False,
                "stopReason": "Verification remains blocked/unverified.\n" + message,
            }
        return {"decision": "block", "reason": message}
    return {
        "hookSpecificOutput": {"hookEventName": event, "additionalContext": message}
    }


def check_deletions(
    root: Path, provider: str, paths: list[str], value: dict, remaining: float
) -> dict:
    deleted = [name for name in paths if not (root / name).exists()]
    if not deleted:
        return value
    makefile = root / "vibe_scripts" / provider / "Makefile"
    if not makefile.exists():
        makefile = root / "Makefile"
    if (
        not makefile.is_file()
        or not re.search(r"(?m)^test-ai\s*:", makefile.read_text())
        or remaining <= 2
    ):
        tests = QUALITY.result(
            "not_checked",
            "deleted-file tests",
            [
                "Deleted files require tests, but the controlled test-ai target or hook time budget is unavailable. Run the project tests explicitly and recheck."
            ],
        )
    else:
        tests = QUALITY.run_quality(
            ["make", "-f", str(makefile), "test-ai"],
            cwd=root,
            label="deleted-file tests",
            timeout=min(remaining - 1, 45),
        )
    if not CHECKER.existing_project_files(root, paths):
        return tests
    priority = {"passed": 0, "findings": 1, "not_checked": 2, "tool_failure": 3}
    value["status"] = max((value["status"], tests["status"]), key=priority.get)
    value["summary"].extend(
        [f"Deleted-file tests: {tests['status']}", *tests["summary"]]
    )
    if tests.get("log"):
        value["summary"].append(f"Full log: {tests['log']}")
    for metric in ("command_count", "duration_seconds", "output_bytes"):
        value[metric] += tests[metric]
    value["diagnostic_signature"] = hashlib.sha256(
        json.dumps(
            [
                value.get("diagnostic_signature"),
                tests.get("diagnostic_signature"),
                tests["status"],
            ]
        ).encode()
    ).hexdigest()
    return value


def handle(root: Path, provider: str, event: str, payload: dict) -> dict:
    ensure_helpers()
    started = time.monotonic()
    session = payload.get("session_id") or payload.get("thread_id")
    if not isinstance(session, str) or not session:
        return unavailable_response(
            event,
            "Hook payload has no session ID; baseline ownership cannot be established. Start a new trusted session and run the controlled verifier.",
            payload.get("stop_hook_active") is True,
        )
    cache = root / ".cache" / "quality-sessions"
    cache.mkdir(parents=True, exist_ok=True)
    key = provider + "-" + hashlib.sha256(session.encode()).hexdigest()[:24]
    state_path = cache / (key + ".json")
    with (cache / (key + ".lock")).open("a") as lock:
        acquire_lock(lock)
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("paths", []), list)
            or not all(isinstance(path, str) for path in state.get("paths", []))
            or not isinstance(state.get("baseline", {}), dict)
            or not isinstance(state.get("history", []), list)
            or not isinstance(state.get("attempts", 0), int)
        ):
            raise ValueError(
                "Malformed session state; preserve its file for diagnosis and start a new session."
            )
        if event == "SessionStart":
            if not state:
                baseline = snapshot(root)
                state = {
                    "baseline": baseline,
                    "design_baseline": design_snapshot(root, list(baseline)),
                    "paths": [],
                    "attempts": 0,
                    "history": [],
                }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            return {}
        explicit = payload_paths(root, payload)
        if event == "PostToolUse" and not explicit:
            return {}
        paths = sorted(set(state.get("paths", [])) | set(explicit))
        if event == "Stop" and "baseline" in state:
            paths = sorted(
                set(paths) | set(changed_since(state["baseline"], snapshot(root)))
            )
        paths = CHECKER.project_paths(root, paths)
        baseline_missing = "baseline" not in state
        if not paths:
            value = QUALITY.result(
                "not_checked",
                "session quality",
                [
                    "Explicitly skipped: no files changed since SessionStart; no quality checks ran."
                    if not baseline_missing
                    else "SessionStart baseline is missing; shell changes cannot be attributed safely. Activate hooks, start a new session and verify explicitly."
                ],
                explicitly_skipped=not baseline_missing,
            )
        else:
            value = CHECKER.check_files(
                root, paths, timeout=max(1, min(45, 55 - (time.monotonic() - started)))
            )
            if baseline_missing and event == "Stop" and value["status"] == "passed":
                value["status"] = "not_checked"
                value["summary"].insert(
                    0,
                    "Recorded edits passed, but SessionStart baseline is missing; shell changes remain unverified.",
                )
            if event == "Stop":
                value = check_deletions(
                    root, provider, paths, value, 55 - (time.monotonic() - started)
                )
                value = merge_review(
                    value,
                    review_design(
                        root, paths, state, 55 - (time.monotonic() - started)
                    ),
                )
                if baseline_missing and value["status"] == "passed":
                    value["status"] = "not_checked"
                    value["summary"].insert(
                        0,
                        "Recorded checks passed, but the missing SessionStart baseline leaves shell changes unverified.",
                    )
        signature = (
            value.get("diagnostic_signature")
            or hashlib.sha256(json.dumps(value["summary"]).encode()).hexdigest()
        )
        attempts = state.get("attempts", 0)
        previous = state.get("stop_signature")
        if value["status"] == "passed":
            attempts = 0
        elif event == "Stop":
            attempts = attempts + 1 if signature == previous else 1
        if event == "Stop":
            state["stop_signature"] = signature
        state.update(
            paths=paths, signature=signature, attempts=attempts, status=value["status"]
        )
        history = state.setdefault("history", [])
        history.append(
            {
                "event": event,
                "status": value["status"],
                "signature": signature,
                "edited_paths": explicit,
                "duration_seconds": value["duration_seconds"],
                "command_count": value["command_count"],
                "output_bytes": value["output_bytes"],
                "emitted_bytes": 0,
                "repair_outcome": "resolved"
                if value["status"] == "passed" and previous
                else "unresolved",
                "measured_tokens": None,
                "estimated_tokens": None,
                "delegation_overhead": "not measured by quality hooks",
            }
        )
        if attempts >= 3:
            edits = [
                path
                for record in history
                if isinstance(record, dict)
                for path in record.get("edited_paths", [])
            ]
            value["summary"].insert(
                0,
                "Attempted repair paths: "
                + (
                    ", ".join(sorted(set(edits))[:10])
                    or "none recorded; repeated checks alone are not repair attempts"
                ),
            )
        result = response(provider, event, value, attempts)
        user_summary = QUALITY.render_result(value, 8)
        history[-1]["emitted_bytes"] = len(
            (json.dumps(result) + "\n" + user_summary + "\n").encode()
        )
        # The lock serializes reads/writes; replacement protects interrupted writes.
        temporary = state_path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(state_path)
        print(user_summary, file=sys.stderr)
        return result


def native_design_commands(provider: str) -> set[str]:
    folder = ".agents" if provider == "codex" else ".claude"
    base = folder + "/skills/impeccable/scripts/"
    commands = set()
    for prefix in ("", "${CLAUDE_PROJECT_DIR}/"):
        path = prefix + base
        commands.update(
            {
                f'node "{path}hook.mjs"',
                f'[ ! -f "{path}hook.mjs" ] || node "{path}hook.mjs"',
                f'[ ! -f "{path}impeccable" ] || "{path}impeccable" hook',
            }
        )
    commands.add(
        'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; hook="$root/'
        + base
        + 'hook.mjs"; [ -f "$hook" ] || exit 0; exec node "$hook"'
    )
    return commands


def stop_adapter(provider: str) -> str:
    return f'''#!/usr/bin/env python3
"""Provider adapter for the shared quality-result implementation."""
import os
import sys
from pathlib import Path

helper = Path(__file__).resolve().parents[2] / "vibe_scripts" / "{provider}" / "quality-hook.py"
os.execv(sys.executable, [sys.executable, str(helper), "--provider", "{provider}", "--event", "Stop"])
'''


def stop_command(provider: str) -> str:
    prefix = 'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; '
    if provider == "claude":
        prefix = 'root="${CLAUDE_PROJECT_DIR:-}"; [ -n "$root" ] || root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"; '
    return (
        prefix
        + f'hook="$root/.{provider}/hooks/stop_edited_check.py"; [ -f "$hook" ] || exit 0; exec python3 "$hook"'
    )


def session_command(provider: str) -> str:
    prefix = stop_command(provider).split("hook=")[0]
    if provider == "codex":
        prefix = prefix.replace("exit 0;", "exit 1;")
    return (
        prefix
        + f'exec python3 "$root/vibe_scripts/{provider}/quality-hook.py" --provider {provider} --event SessionStart'
    )


def install_design_scope(root: Path, provider: str) -> None:
    """Replace known duplicate native scans only after the shared runtime is current."""

    def unversioned(text):
        return re.sub(r"(?m)^# (?:Codex|Claude)Helper-Version:.*\n", "", text)

    commands = native_design_commands(provider)
    configs = (
        [root / ".codex/config.toml", root / ".codex/hooks.json"]
        if provider == "codex"
        else [root / ".claude/settings.json", root / ".claude/settings.local.json"]
    )

    def filter_groups(groups):
        result = []
        for group in groups:
            kept = [
                h for h in group.get("hooks", []) if h.get("command") not in commands
            ]
            if kept == group.get("hooks", []):
                result.append(group)
            elif kept:
                result.append(dict(group, hooks=kept))
        return result

    def confirm_replacement():
        for filename in ("quality-hook.py", "agent-check-edited.py"):
            runtime = root / "vibe_scripts" / provider / filename
            if not runtime.is_file() or unversioned(runtime.read_text()) != unversioned(
                Path(__file__).with_name(filename).read_text()
            ):
                raise ValueError(
                    f"Preserved older quality hook/checker ({filename}); refresh managed files before migrating design hooks"
                )
        adapter = root / f".{provider}/hooks/stop_edited_check.py"
        if (
            not adapter.is_file()
            or unversioned(adapter.read_text()).strip()
            != stop_adapter(provider).strip()
        ):
            raise ValueError(
                "Preserved native design hooks: shared Stop adapter is unavailable; run the full installer"
            )
        registered = set()
        for config in configs:
            if not config.is_file():
                continue
            if config.suffix == ".json":
                document = json.loads(config.read_text())
            else:
                import tomllib

                document = tomllib.loads(config.read_text())
            for event, command in (
                ("Stop", stop_command(provider)),
                ("SessionStart", session_command(provider)),
            ):
                for group in document.get("hooks", {}).get(event, []):
                    if any(
                        hook.get("command") == command
                        for hook in group.get("hooks", [])
                    ):
                        registered.add(event)
        if registered != {"SessionStart", "Stop"}:
            raise ValueError(
                "Preserved native design hooks: shared SessionStart/Stop registration is unavailable; run the full installer"
            )

    for path in configs:
        if not path.is_file():
            continue
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError(f"Preserved symlinked hook config: {path}")
        original = path.read_text()
        if path.suffix == ".json":
            data = json.loads(original)
            changed = False
            for event in ("PostToolUse", "Stop"):
                groups = data.get("hooks", {}).get(event, [])
                filtered = filter_groups(groups)
                if filtered != groups:
                    data["hooks"][event] = filtered
                    changed = True
            text = json.dumps(data, indent=2) + "\n" if changed else original
        else:
            import copy
            import tomllib

            expected = copy.deepcopy(tomllib.loads(original))
            for event in ("PostToolUse", "Stop"):
                if event in expected.get("hooks", {}):
                    expected["hooks"][event] = filter_groups(expected["hooks"][event])

            def toml_value(value):
                if isinstance(value, dict):
                    return (
                        "{ "
                        + ", ".join(
                            json.dumps(k) + " = " + toml_value(v)
                            for k, v in value.items()
                        )
                        + " }"
                    )
                if isinstance(value, list):
                    return "[" + ", ".join(toml_value(v) for v in value) + "]"
                return json.dumps(value)

            def replace(match):
                event = match[1] or match[2]
                groups = tomllib.loads(match[0])["hooks"][event]
                filtered = filter_groups(groups)
                if filtered == groups:
                    return match[0]
                return "".join(
                    "[[hooks."
                    + json.dumps(event)
                    + "]]\n"
                    + "\n".join(
                        json.dumps(k) + " = " + toml_value(v) for k, v in g.items()
                    )
                    + "\n\n"
                    for g in filtered
                )

            pattern = r'(?m)^\[\[hooks\.(?:"(PostToolUse|Stop)"|(PostToolUse|Stop))\]\][^\n]*\n.*?(?=^\[(?!\[?hooks\.(?:"[^"\n]+"|[\w]+)\.hooks)|\Z)'
            text = re.sub(pattern, replace, original, flags=re.DOTALL)
            parsed = tomllib.loads(text)
            for data in (parsed, expected):
                if "hooks" in data:
                    data["hooks"] = {k: v for k, v in data["hooks"].items() if v}
            if parsed != expected:
                raise ValueError(f"Unsupported hook layout; preserved {path}")
        if text != original:
            confirm_replacement()
            shutil.copy2(path, str(path) + f".bak.{time.time_ns()}")
            path.write_text(text)
            print(
                f"Configured scoped design review; removed duplicate native scans from {path.relative_to(root)}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--install-design-scope", action="store_true")
    parser.add_argument("--event", choices=("SessionStart", "PostToolUse", "Stop"))
    args = parser.parse_args()
    if args.install_design_scope:
        try:
            install_design_scope(Path.cwd().resolve(), args.provider)
            return 0
        except (OSError, ValueError, TypeError, KeyError) as error:
            print(f"Design hook migration incomplete: {error}", file=sys.stderr)
            return 1
    if args.event is None:
        parser.error("--event is required for hook execution")
    payload = {}
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise TypeError("hook input must be a JSON object")
        ensure_helpers()
        root = Path(payload.get("cwd") or os.getcwd()).resolve()
        located = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if located.returncode == 0:
            root = Path(located.stdout.strip()).resolve()
        result = handle(root, args.provider, args.event, payload)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ImportError,
        SyntaxError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        message = f"Quality hook tool failure: {type(error).__name__}: {error}. Check required helper files, session state and cache permissions; verification remains incomplete."
        print(message, file=sys.stderr)
        result = unavailable_response(
            args.event,
            message,
            isinstance(payload, dict) and payload.get("stop_hook_active") is True,
        )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
