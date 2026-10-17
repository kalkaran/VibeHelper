#!/usr/bin/env python3
"""Nonblocking project memory maintenance and scoped planning context."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CODE = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".cs",
    ".scala",
    ".vue",
    ".svelte",
    ".sh",
    ".bash",
    ".html",
    ".css",
    ".scss",
    ".sql",
}
CODE.update(
    {
        ".cc",
        ".cxx",
        ".kts",
        ".lua",
        ".toc",
        ".zig",
        ".ps1",
        ".ex",
        ".exs",
        ".m",
        ".mm",
        ".jl",
        ".dart",
        ".v",
        ".sv",
    }
)
EXCLUDE = {
    ".git",
    ".cache",
    ".code-review-graph",
    "graphify-out",
    "notes",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".venv",
    "obsidian",
}
BACKENDS = {"crg": ".code-review-graph/graph.db", "graphify": "graphify-out/graph.json"}


DEADLINE = None


def remaining(maximum):
    if DEADLINE is None:
        return maximum
    left = DEADLINE - time.monotonic()
    if left <= 0:
        raise RuntimeError("memory time budget exhausted; remaining work stays pending")
    return min(maximum, left)


def run(root, args, timeout=30):
    timeout = remaining(timeout)
    # Temporary streams avoid pipe deadlocks if a backend leaves children alive.
    with (
        tempfile.TemporaryFile(mode="w+") as out,
        tempfile.TemporaryFile(mode="w+") as err,
    ):
        process = subprocess.Popen(
            args,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    try:
                        process.send_signal(sig)
                    except (ProcessLookupError, PermissionError):
                        pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args)
        out.seek(0)
        return out.read()


def digest(path, deadline=None):
    deadline = min(deadline or float("inf"), time.monotonic() + remaining(8))
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            if deadline is not None and time.monotonic() > deadline:
                raise RuntimeError(
                    "memory scan exceeded its time budget; freshness remains pending"
                )
            result.update(chunk)
    return result.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".memory-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def included(name):
    path = Path(name)
    return (
        not any(part in EXCLUDE for part in path.parts)
        and not any(part.startswith(".env") for part in path.parts)
        and ".bak." not in name
    )


def snapshot(root):
    """Content, not mtimes: catches old checkouts, shell edits and deletions."""
    deadline = time.monotonic() + remaining(8)
    paths = run(
        root, ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], 5
    ).split("\0")
    # Reviewed wiki/guidance is commonly ignored by the installers.
    for directory in ("codebase-wiki", "agent"):
        for path in (root / directory).rglob("*.md"):
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "memory scan exceeded 8 seconds; freshness remains pending"
                )
            paths.append(str(path.relative_to(root)))
    values = {}
    for name in sorted(set(paths)):
        if time.monotonic() > deadline:
            raise RuntimeError(
                "memory scan exceeded 8 seconds; previous freshness retained"
            )
        path = root / name
        if (
            not included(name)
            or path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or not path.is_file()
        ):
            continue
        values[name] = digest(path, deadline)
    return values


def identity(root):
    def git(*args):
        try:
            return run(root, ["git", *args], 5).strip()
        except subprocess.CalledProcessError:
            return "unborn"

    return {
        "root": str(root),
        "head": git("rev-parse", "HEAD"),
        "branch": git("symbolic-ref", "--short", "HEAD"),
    }


def changes(before, after):
    return sorted(
        name
        for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    )


def code_files(files):
    return {
        name: value
        for name, value in files.items()
        if Path(name).suffix.lower() in CODE
    }


def artifact(root, backend):
    path = root / BACKENDS[backend]
    return digest(path, time.monotonic() + 5) if path.is_file() else None


def prune_graphify(root, current):
    """Remove deleted/generated sources, including the last deleted code file.

    Graphify's older code rebuild keeps semantic nodes and returns early when no
    code remains. Reconcile those cases without inferring any new relationships.
    """
    path = root / BACKENDS["graphify"]
    if not path.exists():
        return
    graph = read_json(path)
    removed = set()
    for node in graph.get("nodes", []):
        source = node.get("source_file")
        if not source:
            continue
        try:
            relative = (root / source).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if (
            not included(relative)
            or not (root / relative).is_file()
            or (node.get("file_type") == "code" and relative not in current)
        ):
            removed.add(node["id"])
    if not removed:
        return
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] not in removed]
    for key in ("links", "edges"):
        if key in graph:
            graph[key] = [
                edge
                for edge in graph[key]
                if edge.get("source") not in removed
                and edge.get("target") not in removed
            ]
    for container in (graph, graph.get("graph", {})):
        if "hyperedges" in container:
            container["hyperedges"] = [
                edge
                for edge in container["hyperedges"]
                if not removed.intersection(edge.get("nodes", []))
            ]
    write_json(path, graph)
    (root / "graphify-out/GRAPH_REPORT.md").write_text(
        "# Graphify refresh\n\nDeleted or generated sources were removed from graph.json.\nRegenerate the semantic report before using it as wiki evidence.\n"
    )


def compact(status):
    return {
        key: dict(value, paths=value["paths"][:5], pending_count=len(value["paths"]))
        for key, value in status.items()
    }


def freshness(root, state, current, repo):
    result = {}
    for backend in BACKENDS:
        stamp = state.get(backend, {})
        pending = changes(stamp.get("files", {}), code_files(current))
        same = stamp.get("identity") == repo and stamp.get("artifact") == artifact(
            root, backend
        )
        result[backend] = {
            "status": "current" if stamp and same and not pending else "pending",
            "paths": pending,
            "error": state.get("errors", {}).get(backend),
        }
    semantic = {k: v for k, v in current.items() if Path(k).suffix.lower() not in CODE}
    stamp = state.get("semantic", {})
    result["semantic"] = {
        "status": "reviewed"
        if stamp
        and stamp.get("files") == semantic
        and stamp.get("code") == code_files(current)
        and stamp.get("identity") == repo
        and stamp.get("artifact") == artifact(root, "graphify")
        else "needs review",
        "paths": changes(stamp.get("files", {}), semantic),
    }
    return result


def refresh(root, state, current, repo, backends):
    for backend in backends:
        if freshness(root, state, current, repo)[backend]["status"] == "current":
            continue
        executable = shutil.which(
            "code-review-graph" if backend == "crg" else "graphify"
        )
        try:
            if not executable:
                raise RuntimeError(f"{backend} is unavailable; inspect source directly")
            before = artifact(root, backend)
            index_path = root / BACKENDS[backend]
            before_mtime = (
                index_path.stat().st_mtime_ns if index_path.exists() else None
            )
            if backend == "crg":
                # Rebuild on branch/HEAD changes and deletions: incremental diff may
                # miss a deleted untracked file or a checkout with older mtimes.
                previous = state.get(backend, {})
                deleted = set(previous.get("files", {})) - set(code_files(current))
                mode = (
                    "build"
                    if not previous or previous.get("identity") != repo or deleted
                    else "update"
                )
                output = run(root, [executable, mode, "--repo", str(root)], 40)
            else:
                # Installed Graphify 0.4.13 exposes a full code rebuild here.
                # Do not use the mtime-only incremental skill merge for checkouts.
                existing = read_json(root / BACKENDS["graphify"])
                if existing and (
                    not isinstance(existing, dict)
                    or not isinstance(existing.get("nodes"), list)
                    or not all(
                        isinstance(node, dict) and "id" in node
                        for node in existing["nodes"]
                    )
                ):
                    raise ValueError(
                        "Malformed Graphify index preserved; inspect it before rebuilding"
                    )
                if code_files(current):
                    output = run(root, [executable, "update", str(root)], 40)
                else:
                    output = "No code sources remain; pruning stale code nodes."
                    if not (root / BACKENDS["graphify"]).exists():
                        write_json(
                            root / BACKENDS["graphify"], {"nodes": [], "links": []}
                        )
                prune_graphify(root, current)
            after = artifact(root, backend)
            if (
                not after
                or (before == after and index_path.stat().st_mtime_ns == before_mtime)
                or re.search(
                    r"rebuild failed|Traceback \(most recent call last",
                    output,
                    re.IGNORECASE,
                )
            ):
                raise RuntimeError(
                    "backend did not produce a verified changed index; freshness remains pending"
                )
            latest = snapshot(root)
            if code_files(latest) != code_files(current) or identity(root) != repo:
                raise RuntimeError(
                    "files changed during refresh; retry at the next planning boundary"
                )
            current.clear()
            current.update(latest)
            state[backend] = {
                "files": code_files(current),
                "identity": repo,
                "artifact": after,
                "updated_at": time.time(),
            }
            state.setdefault("errors", {}).pop(backend, None)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as error:
            state.setdefault("errors", {})[backend] = str(error)[:500]


def context(root, task, files, status, architecture=False):
    """Evidence links and bounded queries; this output grants no edit authority."""
    words = set(re.findall(r"[a-z][a-z0-9_-]{2,}", task.lower())) - {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
    }
    selected = []
    for name in files:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("planning paths must be inside the repository")
        selected.append(str(path.relative_to(root)))
    pages = []
    for path in (root / "codebase-wiki").glob("*.md"):
        text = path.read_text(errors="replace")
        score = sum(word in text.lower() for word in words)
        if score:
            pages.append((score, str(path.relative_to(root))))
    value = {
        "task": task,
        "inspection_paths": selected,
        "freshness": compact(status),
        "wiki": [p for _, p in sorted(pages, reverse=True)[:5]],
        "instructions": "Read relevant wiki pages and cited source. Use callers/dependents/tests to plan verification. Inspection does not authorize unrelated edits or design reviews. Generated graph claims require source confirmation.",
    }
    if selected and status["crg"]["status"] == "current":
        value["code_context"] = []
        for name in selected[:5]:
            for query in ("file_summary", "importers_of", "tests_for"):
                try:
                    output = run(
                        root,
                        [
                            "code-review-graph",
                            "query",
                            query,
                            name,
                            "--repo",
                            str(root),
                        ],
                        5,
                    )
                    value["code_context"].append(
                        {"file": name, "query": query, "output": output[:2500]}
                    )
                except (OSError, subprocess.SubprocessError) as error:
                    value["code_context"].append(
                        {"file": name, "query": query, "error": str(error)[:250]}
                    )
    if (
        architecture
        and status["graphify"]["status"] != "disabled"
        and (root / BACKENDS["graphify"]).exists()
    ):
        try:
            value["architecture_context"] = run(
                root, ["graphify", "query", task, "--budget", "1500"], 15
            )[:8000]
        except (OSError, subprocess.SubprocessError) as error:
            value["architecture_context"] = str(error)[:250]
    return value


def handle(root, args, payload=None):
    global DEADLINE
    previous = DEADLINE
    DEADLINE = time.monotonic() + (55 if args.action == "hook" else 120)
    try:
        return handle_locked(root, args, payload)
    finally:
        DEADLINE = previous


def handle_locked(root, args, payload=None):
    cache = root / ".cache/project-memory"
    for name in (
        ".cache/project-memory",
        ".cache/project-memory/state.json",
        ".cache/project-memory/lock",
        "graphify-out",
        "graphify-out/needs_update",
        "graphify-out/GRAPH_REPORT.md",
        *BACKENDS.values(),
    ):
        if not (root / name).resolve().is_relative_to(root):
            raise ValueError("memory paths must stay inside the repository")
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "status": "busy",
                "message": "Memory update in progress; use source until freshness is checked.",
            }
        policy = read_json(root / f"vibe_scripts/{args.provider}/memory-policy.json")
        if args.action == "hook" and policy.get("hooks") is False:
            return {"status": "disabled"}
        state = read_json(cache / "state.json")
        current, repo = snapshot(root), identity(root)
        state["observed"] = current
        state["identity"] = repo
        backends = []
        event = (payload or {}).get("hook_event_name", "")
        if args.action in {"context", "refresh"} or event == "PreToolUse":
            backends = ["crg"]
            if (
                args.architecture
                or "graphify" in (payload or {}).get("tool_name", "").lower()
            ):
                backends.append("graphify")
        elif event == "Stop":
            backends = [
                key for key, path in BACKENDS.items() if (root / path).is_file()
            ]
        backends = [key for key in backends if policy.get(key, True)]
        refresh(root, state, current, repo, backends)
        status = freshness(root, state, current, repo)
        if (
            backends
            and policy.get("graphify", True)
            and status["semantic"]["status"] != "reviewed"
            and (root / "graphify-out").is_dir()
        ):
            (root / "graphify-out/needs_update").write_text(
                "Documents need semantic review; code refresh does not complete it.\n"
            )
        for key in BACKENDS:
            if not policy.get(key, True):
                status[key]["status"] = "disabled"
        if args.action == "accept-semantic":
            if not policy.get("graphify", True):
                raise ValueError("Graphify is disabled for this provider")
            # Bind explicit reviewed evidence to the snapshot printed by status.
            token = hashlib.sha256(
                json.dumps(current, sort_keys=True).encode()
            ).hexdigest()
            if args.snapshot != token or not artifact(root, "graphify"):
                raise ValueError(
                    "snapshot changed or Graphify output missing; review again before recording evidence"
                )
            state["semantic"] = {
                "code": code_files(current),
                "files": {
                    k: v
                    for k, v in current.items()
                    if Path(k).suffix.lower() not in CODE
                },
                "identity": repo,
                "artifact": artifact(root, "graphify"),
                "evidence": args.evidence,
            }
            status = freshness(root, state, current, repo)
            (root / "graphify-out/needs_update").unlink(missing_ok=True)
        write_json(cache / "state.json", state)
        value = {
            "freshness": status,
            "snapshot": hashlib.sha256(
                json.dumps(current, sort_keys=True).encode()
            ).hexdigest(),
        }
        if args.action == "context":
            value.update(
                context(root, args.task, args.files, status, args.architecture)
            )
        return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("hook", "status", "context", "refresh", "accept-semantic")
    )
    parser.add_argument(
        "--provider",
        choices=("codex", "claude"),
        default=Path(__file__).parent.name
        if Path(__file__).parent.name in {"codex", "claude"}
        else "codex",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--task", default="")
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--architecture", action="store_true")
    parser.add_argument("--snapshot")
    parser.add_argument("--evidence", default="")
    args = parser.parse_args()
    try:
        if args.action == "context" and not args.task.strip():
            raise ValueError("context requires --task describing the current request")
        if args.action == "accept-semantic" and not args.evidence.strip():
            raise ValueError("record the reviewed source paths with --evidence")
        payload = json.load(sys.stdin) if args.action == "hook" else {}
        root = Path(
            run(args.root, ["git", "rev-parse", "--show-toplevel"], 5).strip()
        ).resolve()
        value = handle(root, args, payload)
        if args.action == "hook":
            if value.get("status") == "disabled":
                print("{}")
                return 0
            event = payload.get("hook_event_name")
            if event in {"SessionStart", "PreToolUse"}:
                if "freshness" in value:
                    value["freshness"] = compact(value["freshness"])
                message = (
                    "Project memory: "
                    + json.dumps(value)
                    + f". Before substantial planning run python3 vibe_scripts/{args.provider}/project-memory.py context --task '<current request>' --files <relevant paths>; use --architecture for broader discovery. Source inspection remains available."
                )
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": event,
                                "additionalContext": message,
                            }
                        }
                    )
                )
            else:
                errors = {
                    key: item["error"]
                    for key, item in value.get("freshness", {}).items()
                    if item.get("error") and item["status"] != "disabled"
                }
                print(
                    json.dumps(
                        {
                            "systemMessage": "Project memory remains pending: "
                            + json.dumps(errors)
                        }
                        if errors and event == "Stop"
                        else {}
                    )
                )
        else:
            print(json.dumps(value, indent=2))
        if args.action == "refresh":
            requested = ["crg", "graphify"] if args.architecture else ["crg"]
            return int(
                any(
                    value.get("freshness", {}).get(key, {}).get("status")
                    not in {"current", "disabled"}
                    for key in requested
                )
            )
        return 0
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        if args.action == "hook":
            print(
                json.dumps(
                    {
                        "systemMessage": f"Project memory unavailable: {error}. Continue with source inspection."
                    }
                )
            )
            return 0
        print(f"Project memory unavailable: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
