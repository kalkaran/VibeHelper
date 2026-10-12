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
import subprocess
import sys
import time
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
                state = {
                    "baseline": snapshot(root),
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument(
        "--event", choices=("SessionStart", "PostToolUse", "Stop"), required=True
    )
    args = parser.parse_args()
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
