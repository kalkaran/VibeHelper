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
import traceback
from collections import Counter
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

HOOK_ERROR_LOG_BYTES = 65536
HOOK_ERROR_RECENT_RECORDS = 5
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
    ".graphify",
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


class MemoryTimeout(RuntimeError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def timeout_message(root, error):
    if not isinstance(error, (MemoryTimeout, subprocess.TimeoutExpired)):
        return str(error)
    if not hasattr(error, "timeout_log"):
        try:
            root = root.resolve()
            directory = root / ".cache/project-memory"
            if not directory.resolve().is_relative_to(root.resolve()):
                raise OSError("timeout log directory leaves repository")
            directory.mkdir(parents=True, exist_ok=True)
            record = {
                "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "root": str(root),
                "error": str(error),
                "details": getattr(error, "details", {}),
                "traceback": "".join(traceback.format_exception(error)),
            }
            secrets = [
                value
                for key, value in os.environ.items()
                if value
                and re.search(
                    r"token|secret|password|api.?key|credential", key, re.IGNORECASE
                )
            ]

            def redact(value):
                if isinstance(value, dict):
                    return {key: redact(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [redact(item) for item in value]
                if not isinstance(value, str):
                    return value
                for secret in secrets:
                    value = value.replace(secret, "[REDACTED]")
                value = re.sub(r"(?i)(Bearer\s+)\S+", r"\1[REDACTED]", value)
                value = re.sub(
                    r"(?i)((?:token|secret|password|api[_-]?key)[\w-]*[\"']?\s*[=:]\s*)([\"'])(.*?)\2",
                    r"\1[REDACTED]",
                    value,
                )
                return re.sub(
                    r"(?i)((?:token|secret|password|api[_-]?key)[\w-]*[\"']?[ =:]+[\"']?)([^\s\"',]+)",
                    r"\1[REDACTED]",
                    value,
                )

            text = json.dumps(redact(record), indent=2)
            fd, name = tempfile.mkstemp(
                prefix="timeout-", suffix=".json", dir=directory
            )
            with os.fdopen(fd, "w") as stream:
                stream.write(text + "\n")
            error.timeout_log = str(Path(name).relative_to(root))
        except OSError as failure:
            error.timeout_log = f"unavailable ({failure})"
    reason = (
        f"Command timed out after {error.timeout:g} seconds"
        if isinstance(error, subprocess.TimeoutExpired)
        else str(error)
    )
    return f"Timeout log: {error.timeout_log}. {reason}"


def remaining(maximum):
    if DEADLINE is None:
        return maximum
    left = DEADLINE - time.monotonic()
    if left <= 0:
        raise MemoryTimeout(
            "memory time budget exhausted; remaining work stays pending",
            operation="total budget",
            deadline=DEADLINE,
            overdue_seconds=-left,
        )
    return min(maximum, left)


def run(root, args, timeout=30):
    started = time.monotonic()
    try:
        timeout = remaining(timeout)
    except MemoryTimeout as error:
        error.details["command"] = args
        timeout_message(root, error)
        raise
    env = os.environ.copy()
    if Path(args[0]).name == "graphify":
        output = backend_path(root, "graphify").parent
        if output.is_dir():
            env["GRAPHIFY_OUT"] = str(output)
    # Temporary streams avoid pipe deadlocks if a backend leaves children alive.
    with (
        tempfile.TemporaryFile(mode="w+", errors="replace") as out,
        tempfile.TemporaryFile(mode="w+", errors="replace") as err,
    ):
        process = subprocess.Popen(
            args,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
            env=env,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
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
            out.seek(0)
            err.seek(0)
            error.details = {
                "operation": "subprocess",
                "command": args,
                "budget_seconds": timeout,
                "elapsed_seconds": time.monotonic() - started,
                "stdout": out.read(),
                "stderr": err.read(),
            }
            timeout_message(root, error)
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args)
        out.seek(0)
        return out.read()


def file_stamp(path):
    info = path.stat()
    return f"stat:{info.st_mtime_ns}:{info.st_ctime_ns}:{info.st_size}:{info.st_ino}"


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
    """Compare file metadata without reading source or asset contents."""
    started = time.monotonic()
    budget = remaining(8)
    deadline = started + budget
    paths = run(
        root, ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], 5
    ).split("\0")
    # Reviewed wiki/guidance is commonly ignored by the installers.
    for directory in ("codebase-wiki", "agent"):
        for path in (root / directory).rglob("*.md"):
            if time.monotonic() > deadline:
                raise MemoryTimeout(
                    "memory scan exceeded 8 seconds; freshness remains pending",
                    operation="guidance traversal",
                    path=str(path),
                    budget_seconds=budget,
                    elapsed_seconds=time.monotonic() - started,
                )
            paths.append(str(path.relative_to(root)))
    values = {}
    for name in sorted(set(paths)):
        if not name:
            continue
        if time.monotonic() > deadline:
            raise MemoryTimeout(
                "memory scan exceeded 8 seconds; previous freshness retained",
                operation="source scan",
                path=name,
                files_hashed=len(values),
                budget_seconds=budget,
                elapsed_seconds=time.monotonic() - started,
            )
        path = root / name
        if (
            not included(name)
            or path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or not path.is_file()
        ):
            continue
        values[name] = file_stamp(path)
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


def backend_path(root, backend):
    if backend == "graphify":
        for directory in (".graphify", "graphify-out"):
            path = root / directory / "graph.json"
            if path.is_file():
                return path
        if (root / ".graphify").is_dir():
            return root / ".graphify/graph.json"
    return root / BACKENDS[backend]


def artifact(root, backend):
    path = backend_path(root, backend)
    return file_stamp(path) if path.is_file() else None


def prune_graphify(root, current):
    """Remove deleted/generated sources, including the last deleted code file.

    Graphify's older code rebuild keeps semantic nodes and returns early when no
    code remains. Reconcile those cases without inferring any new relationships.
    """
    path = backend_path(root, "graphify")
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
    path.with_name("GRAPH_REPORT.md").write_text(
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
            index_path = backend_path(root, backend)
            before_path = index_path
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
                output = run(root, [executable, mode, "--repo", str(root)], 80)
            else:
                # Installed Graphify 0.4.13 exposes a full code rebuild here.
                # Do not use the mtime-only incremental skill merge for checkouts.
                existing = read_json(index_path)
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
                    output = run(root, [executable, "update", str(root)], 80)
                else:
                    output = "No code sources remain; pruning stale code nodes."
                    if not index_path.exists():
                        write_json(index_path, {"nodes": [], "links": []})
                    prune_graphify(root, current)
            index_path = backend_path(root, backend)
            after = artifact(root, backend)
            if (
                not after
                or (
                    before_path == index_path
                    and before == after
                    and index_path.stat().st_mtime_ns == before_mtime
                )
                or re.search(
                    r"rebuild failed|Traceback \(most recent call last",
                    output,
                    re.IGNORECASE,
                )
            ):
                raise RuntimeError(
                    "backend did not produce a verified changed index; freshness remains pending"
                )
            if backend == "graphify" and code_files(current):
                prune_graphify(root, current)
                after = artifact(root, backend)
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
            state.setdefault("errors", {})[backend] = timeout_message(root, error)[:500]


def query_brief(output, words):
    """Keep source locations and uncertainty; the complete response stays on disk."""
    try:
        data = json.loads(output)
        if not isinstance(data, dict):
            return output[:1000]
    except ValueError:
        return output[:1000]
    lines = [str(data.get("summary", data.get("status", "Graph query")))[:400]]
    if data.get("error"):
        lines.append(str(data["error"])[:500])
    if data.get("confidence"):
        lines.append(str(data["confidence"])[:500])
    rows = data.get("results", [])
    if not isinstance(rows, list):
        rows = []
    rows = [row for row in rows if isinstance(row, dict)]
    rows.sort(key=lambda row: -sum(word in str(row).lower() for word in words))
    for row in rows[:5]:
        lines.append(
            json.dumps(
                {
                    key: row[key]
                    for key in (
                        "name",
                        "file_path",
                        "line_start",
                        "call_site",
                        "importer",
                        "file",
                        "via_package",
                        "indirect",
                        "inferred_by",
                    )
                    if key in row
                },
                ensure_ascii=True,
            )
        )
    count = data.get("result_count")
    if type(count) is not int or count < len(rows):
        count = len(rows)
    omitted = max(0, count - min(5, len(rows)))
    if omitted:
        lines.append(
            f"{omitted} more results; inspect the full evidence before concluding absence."
        )
    return "\n".join(lines)


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
        evidence_dir = root / ".cache/project-memory/context"
        evidence_dir.mkdir(parents=True, exist_ok=True)
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
                    evidence = evidence_dir / (
                        hashlib.sha256(output.encode()).hexdigest() + ".txt"
                    )
                    evidence.write_text(output, encoding="utf-8")
                    value["code_context"].append(
                        {
                            "file": name,
                            "query": query,
                            "output": query_brief(output, words),
                            "evidence": str(evidence.relative_to(root)),
                            "raw_chars": len(output),
                        }
                    )
                except (OSError, subprocess.SubprocessError) as error:
                    value["code_context"].append(
                        {
                            "file": name,
                            "query": query,
                            "error": timeout_message(root, error)[:250],
                        }
                    )
    if (
        architecture
        and status["graphify"]["status"] != "disabled"
        and backend_path(root, "graphify").exists()
    ):
        try:
            value["architecture_context"] = run(
                root,
                [
                    "graphify",
                    "query",
                    task,
                    "--budget",
                    "1500",
                    "--graph",
                    str(backend_path(root, "graphify")),
                ],
                15,
            )[:8000]
        except (OSError, subprocess.SubprocessError) as error:
            value["architecture_context"] = timeout_message(root, error)[:250]
    records = value.get("code_context", [])
    metrics = {
        "query_count": len(records),
        "raw_chars": sum(item.get("raw_chars", 0) for item in records),
        "context_payload_chars": len(json.dumps(value)),
        "measured_tokens": None,
    }
    metrics_path = root / ".cache/project-memory/context-usage.jsonl"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(metrics) + "\n")
    return value


def handle(root, args, payload=None):
    if (
        args.action == "hook"
        and (payload or {}).get("hook_event_name") == "PostToolUse"
    ):
        return {}
    global DEADLINE
    previous = DEADLINE
    DEADLINE = time.monotonic() + (110 if args.action == "hook" else 120)
    try:
        return handle_locked(root, args, payload)
    except MemoryTimeout as error:
        timeout_message(root, error)
        raise
    finally:
        DEADLINE = previous


def handle_locked(root, args, payload=None):
    cache = root / ".cache/project-memory"
    for name in (
        ".cache/project-memory",
        ".cache/project-memory/state.json",
        ".cache/project-memory/lock",
        ".cache/project-memory/context",
        ".cache/project-memory/context-usage.jsonl",
        "graphify-out",
        "graphify-out/needs_update",
        "graphify-out/GRAPH_REPORT.md",
        ".graphify",
        ".graphify/graph.json",
        ".graphify/needs_update",
        ".graphify/GRAPH_REPORT.md",
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
        if args.action in {"context", "refresh"}:
            backends = ["crg"]
            if (
                args.architecture
                or "graphify" in (payload or {}).get("tool_name", "").lower()
            ):
                backends.append("graphify")
        backends = [key for key in backends if policy.get(key, True)]
        refresh(root, state, current, repo, backends)
        status = freshness(root, state, current, repo)
        if (
            backends
            and policy.get("graphify", True)
            and status["semantic"]["status"] != "reviewed"
            and backend_path(root, "graphify").parent.is_dir()
        ):
            backend_path(root, "graphify").with_name("needs_update").write_text(
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
            backend_path(root, "graphify").with_name("needs_update").unlink(
                missing_ok=True
            )
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


def usage_report(root):
    """Local measurements, never a conversion from text size to weekly allowance."""
    instructions = {
        str(path.relative_to(root)): len(path.read_text(encoding="utf-8"))
        for path in (root / "AGENTS.md", root / "CLAUDE.md")
        if path.is_file()
    }
    hooks = []
    errors = []
    for path in (root / ".cache/quality-sessions").glob("*.json"):
        try:
            records = json.loads(path.read_text()).get("history", [])
            if not isinstance(records, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("event"), str)
                or not isinstance(item.get("status"), str)
                for item in records
            ):
                errors.append(f"{path.name}: invalid history")
                continue
            hooks.extend(records)
        except (OSError, ValueError, AttributeError) as error:
            errors.append(f"{path.name}: {type(error).__name__}")
    retrieval = []
    path = root / ".cache/project-memory/context-usage.jsonl"
    if path.is_file():
        try:
            retrieval = [json.loads(line) for line in path.read_text().splitlines()]
            if any(not isinstance(item, dict) for item in retrieval):
                errors.append("context usage: invalid records")
                retrieval = []
        except (OSError, ValueError) as error:
            errors.append(f"context usage: {type(error).__name__}")
    groups = {}
    for item in hooks:
        key = f"{item['event']}:{item['status']}"
        group = groups.setdefault(
            key, {"events": 0, "emitted_bytes": 0, "command_count": 0}
        )
        group["events"] += 1
        for field in ("emitted_bytes", "command_count"):
            number = item.get(field)
            if type(number) is int and number >= 0 and group[field] is not None:
                group[field] += number
            else:
                group[field] = None
    for group in groups.values():
        group["bytes_per_event"] = (
            round(group["emitted_bytes"] / group["events"], 1)
            if group["emitted_bytes"] is not None
            else None
        )
    retrieval_totals = {}
    for field in ("raw_chars", "context_payload_chars"):
        numbers = [item.get(field) for item in retrieval]
        retrieval_totals[field] = (
            sum(numbers)
            if numbers
            and all(type(number) is int and number >= 0 for number in numbers)
            else None
        )
    return {
        "schema": 1,
        "instruction_chars": instructions,
        "workflow_chars": sum(
            len(path.read_text()) for path in (root / "agent").glob("*/*.md")
        ),
        "hook_events": groups,
        "retrieval": {
            "requests": len(retrieval),
            **retrieval_totals,
        }
        if retrieval
        else None,
        "main_agent_tokens": None,
        "weekly_allowance_saved": None,
        "errors": errors,
        "note": "Local text measurements only; no combined token or weekly allowance estimate. Compare identical workloads and fresh session records.",
    }


def hook_errors_report(root):
    """Summarize recent hook failures without exposing raw hook input."""
    path = root / ".cache/hook-errors.log"
    if not path.is_file():
        return "No hook errors recorded."

    try:
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - HOOK_ERROR_LOG_BYTES)
            stream.seek(start)
            data = stream.read(min(size - start, HOOK_ERROR_LOG_BYTES))
    except OSError as error:
        return f"Hook error log unavailable: {type(error).__name__}."

    if start:
        _, separator, data = data.partition(b"\n")
        if not separator:
            data = b""

    counts = Counter()
    records = []
    malformed = 0
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            malformed += 1
            continue
        if not isinstance(record, dict):
            malformed += 1
            continue
        hook = safe_label(record.get("hook"), 48)
        reason = safe_label(record.get("reason"), 64)
        if not hook or not reason:
            malformed += 1
            continue
        counts[(hook, reason)] += 1
        input_bytes = record.get("input_bytes")
        records.append(
            (
                safe_label(record.get("ts"), 32) or "time unknown",
                hook,
                reason,
                input_bytes if type(input_bytes) is int and input_bytes >= 0 else None,
                "error_type" in record or "error" in record,
            )
        )

    lines = [
        f"Hook errors: {len(records)} record(s) in the latest {len(data):,} bytes; raw input omitted."
    ]
    if start:
        lines[0] += " Earlier entries not included."
    if malformed:
        lines.append(f"Malformed records: {malformed}.")
    if counts:
        lines.append("Counts by hook and reason:")
        for (hook, reason), count in counts.most_common(5):
            lines.append(f"  {hook} / {reason}: {count}")
        if len(counts) > 5:
            lines.append(f"  ... {len(counts) - 5} other group(s)")
        lines.append("Recent records:")
        for timestamp, hook, reason, input_bytes, has_error in records[
            -HOOK_ERROR_RECENT_RECORDS:
        ]:
            detail = f"; input {input_bytes:,} bytes" if input_bytes is not None else ""
            if has_error:
                detail += "; exception recorded"
            lines.append(f"  {timestamp} {hook} / {reason}{detail}")
    elif not malformed:
        lines.append("No valid hook error records found.")
    return "\n".join(lines)


def safe_label(value, limit):
    if not isinstance(value, str):
        return ""
    return "".join(char if char.isprintable() else " " for char in value)[
        :limit
    ].strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "hook",
            "status",
            "context",
            "refresh",
            "accept-semantic",
            "usage",
            "hook-errors",
        ),
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
    root = args.root.resolve()
    try:
        if args.action == "usage":
            value = usage_report(root)
            print(json.dumps(value, indent=2))
            return 1 if value["errors"] else 0
        if args.action == "hook-errors":
            print(hook_errors_report(root))
            return 0
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
        message = timeout_message(root, error)
        if args.action == "hook":
            print(
                json.dumps(
                    {
                        "systemMessage": f"Project memory unavailable: {message}. Continue with source inspection."
                    }
                )
            )
            return 0
        print(f"Project memory unavailable: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
