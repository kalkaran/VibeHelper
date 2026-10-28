#!/usr/bin/env python3
"""Nonblocking project memory maintenance and scoped planning context."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shlex
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
# Source formats from both indexers; knowledge/media take precedence over AST support.
CODE.update(
    [
        ".mts",
        ".cts",
        ".mjs",
        ".cjs",
        ".ejs",
        ".ets",
        ".vb",
        ".cbl",
        ".cob",
        ".cobol",
        ".cpy",
        ".groovy",
        ".gradle",
        ".cu",
        ".cuh",
        ".metal",
        ".rake",
        ".luau",
        ".psm1",
        ".psd1",
        ".ml",
        ".mli",
        ".astro",
        ".svh",
        ".vh",
        ".r",
        ".f",
        ".f90",
        ".f95",
        ".f03",
        ".f08",
        ".pas",
        ".pp",
        ".dpr",
        ".dpk",
        ".lpr",
        ".inc",
        ".dfm",
        ".lfm",
        ".lpk",
        ".tf",
        ".tfvars",
        ".hcl",
        ".dm",
        ".dme",
        ".dmm",
        ".dmf",
        ".razor",
        ".cshtml",
        ".cls",
        ".trigger",
        ".lisp",
        ".cl",
        ".lsp",
        ".asd",
        ".robot",
        ".resource",
        ".sol",
        ".erl",
        ".hrl",
        ".escript",
        ".pl",
        ".pm",
        ".xs",
        ".res",
        ".resi",
        ".gd",
        ".nix",
        ".ipynb",
        ".zsh",
    ]
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
    """Inventory working files, descending into Gitlinks and untracked repositories.

    A failed Git command or stat aborts the scan; callers keep the old state.
    """
    started = time.monotonic()
    budget = remaining(8)

    def check_scan(path, operation):
        if time.monotonic() - started > budget:
            raise MemoryTimeout(
                "memory scan exceeded 8 seconds; previous freshness retained",
                operation=operation,
                path=str(path),
                budget_seconds=budget,
            )

    paths = set()
    for directory in ("codebase-wiki", "agent"):
        for path in (root / directory).rglob("*.md"):
            check_scan(path, "guidance traversal")
            if not path.is_symlink() and path.resolve().is_relative_to(root):
                paths.add((path.relative_to(root).as_posix(), file_stamp(path)))
    pending = [root]
    visited = set()
    while pending:
        directory = pending.pop()
        if directory in visited:
            continue
        visited.add(directory)
        names = run(
            directory,
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            30,
        ).split("\0")
        for name in names:
            if not name:
                continue
            path = directory / name
            check_scan(path, "source scan")
            relative = path.relative_to(root).as_posix().rstrip("/")
            if (
                not included(relative)
                or path.is_symlink()
                or not path.resolve().is_relative_to(root)
            ):
                continue
            if path.is_dir():
                if (path / ".git").exists():
                    pending.append(path)
                continue
            # A tracked deletion is confirmed by lstat, not a failed scan.
            try:
                paths.add((relative, file_stamp(path)))
            except FileNotFoundError:
                if path.is_symlink():
                    continue
                # ls-files includes tracked deletions; untracked races are retried.
                tracked = run(
                    directory, ["git", "ls-files", "--error-unmatch", "--", name], 5
                )
                if not tracked:
                    raise
    return dict(sorted(paths))


def classification(root, files):
    result = {key: [] for key in ("code", "knowledge", "media", "unsupported")}
    media = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
        ".tiff",
        ".ico",
        ".dmi",
        ".mp4",
        ".mov",
        ".webm",
        ".mkv",
        ".avi",
        ".m4v",
        ".mp3",
        ".wav",
        ".m4a",
        ".ogg",
    }
    for name in files:
        suffix = Path(name).suffix.lower()
        if suffix in media:
            kind = "media"
        elif suffix in {".pdf", ".docx", ".pptx", ".xlsx", ".epub"}:
            kind = "knowledge"
        else:
            with (root / name).open("rb") as stream:
                data = stream.read(8192)
            if b"\0" in data:
                kind = "unsupported"
            elif suffix in CODE or (not suffix and data.startswith(b"#!")):
                kind = "code"
            else:
                kind = "knowledge"
        result[kind].append(name)
    return result


def sibling(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tool_python(executable):
    with Path(executable).open() as stream:
        header = stream.readline().strip()
    command = shlex.split(header[2:]) if header.startswith("#!") else []
    if len(command) == 2 and Path(command[0]).name == "env":
        command = [shutil.which(command[1])]
    if len(command) != 1 or not command[0] or "python" not in Path(command[0]).name:
        raise RuntimeError(f"Cannot identify Python environment for {executable}")
    return command[0]


def index_backend(root, backend, current):
    executable = shutil.which("code-review-graph" if backend == "crg" else "graphify")
    if not executable:
        raise RuntimeError(f"{backend} is unavailable; inspect source directly")
    cache = root / ".cache/project-memory"
    request = cache / (backend + "-request.json")
    result = cache / (backend + "-coverage.json")
    write_json(
        request,
        {
            "files": sorted(code_files(current, root)),
            "inventory": sorted(current),
            "output": str(backend_path(root, backend)),
        },
    )
    result.unlink(missing_ok=True)
    log = cache / (backend + "-refresh.log")
    command = [
        tool_python(executable),
        str(Path(__file__).with_name("project-memory-index.py")),
        backend,
        str(root),
        str(request),
        str(result),
    ]
    started = time.monotonic()
    with log.open("w") as stream:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            process.wait(timeout=remaining(600))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
            raise RuntimeError(f"{backend} timed out; log: {log}") from None
    if process.returncode or not result.is_file():
        raise RuntimeError(f"{backend} failed (exit {process.returncode}); log: {log}")
    coverage = read_json(result)
    coverage.update(log=str(log), duration_seconds=round(time.monotonic() - started, 3))
    return coverage


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


def code_files(files, root=None):
    if root is not None:
        return {name: files[name] for name in classification(root, files)["code"]}
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


def rules_stamp(root, current):
    names = {
        name
        for name in current
        if Path(name).name
        in {".gitignore", ".graphifyignore", ".graphifyrc", ".crgignore"}
    }
    names.update(
        {
            ".code-review-graph/languages.toml",
            ".git/info/exclude",
            ".graphifyignore",
            ".graphifyrc",
            ".crgignore",
        }
    )
    return {
        name: file_stamp(root / name)
        for name in sorted(names)
        if (root / name).is_file()
    }


def freshness(root, state, current, repo):
    result = {}
    sources = code_files(current, root)
    for backend in BACKENDS:
        stamp = state.get(backend, {})
        pending = changes(stamp.get("files", {}), sources)
        coverage = state.get("coverage", {}).get(backend, {})
        accounted = set(coverage.get("unsupported", [])) | set(
            coverage.get("excluded", [])
        )
        pending = [
            name
            for name in pending
            if name not in accounted
            or stamp.get("accounted", {}).get(name) != current.get(name)
        ]
        same = (
            stamp.get("identity") == repo
            and stamp.get("artifact") == artifact(root, backend)
            and stamp.get("rules") == rules_stamp(root, current)
        )
        result[backend] = {
            "status": "current"
            if stamp
            and same
            and not pending
            and not state.get("errors", {}).get(backend)
            else "pending",
            "paths": pending,
            "error": state.get("errors", {}).get(backend),
            "coverage": coverage,
            "last_successful_refresh": state.get("last_successful_refresh", {}).get(
                backend
            ),
        }
    semantic = {k: v for k, v in current.items() if k not in sources}
    stamp = state.get("semantic", {})
    result["semantic"] = {
        "status": "reviewed"
        if stamp
        and stamp.get("files") == semantic
        and stamp.get("code") == sources
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
        try:
            expected_sources = code_files(current, root)
            before_rules = rules_stamp(root, current)
            coverage = index_backend(root, backend, current)
            state.setdefault("coverage", {})[backend] = coverage
            latest = snapshot(root)
            if (
                code_files(latest, root) != expected_sources
                or identity(root) != repo
                or rules_stamp(root, latest) != before_rules
            ):
                raise RuntimeError("files changed during refresh; queued for retry")
            indexed = {
                name: current[name] for name in coverage["indexed"] if name in current
            }
            state[backend] = {
                "files": indexed,
                "accounted": {
                    name: current[name]
                    for name in [*coverage["unsupported"], *coverage["excluded"]]
                    if name in current
                },
                "rules": rules_stamp(root, current),
                "identity": repo,
                "artifact": artifact(root, backend),
                "updated_at": time.time(),
            }
            if coverage["failed"] or coverage["missing"]:
                raise RuntimeError(
                    f"Incomplete {backend} coverage; inspect {coverage['log']}"
                )
            state.setdefault("errors", {}).pop(backend, None)
            state.setdefault("last_successful_refresh", {})[backend] = time.time()
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
    DEADLINE = time.monotonic() + (110 if args.action == "hook" else 1800)
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
            sources = code_files(current, root)
            state["semantic"] = {
                "code": sources,
                "files": {k: v for k, v in current.items() if k not in sources},
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
            "classification": classification(root, current),
            "watcher": sibling("project-memory-watch").status(root),
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
            "watch",
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
        if args.action == "watch":
            return sibling("project-memory-watch").watch(root, args.provider)
        if args.action == "hook" and payload.get("hook_event_name") in {
            "SessionStart",
            "SessionEnd",
        }:
            value = sibling("project-memory-watch").session(
                root, args.provider, payload
            )
            if value.get("status") == "unavailable":
                raise RuntimeError(
                    value.get("error", "Project memory watcher unavailable")
                )
            print("{}")
            return 0
        if args.action == "hook":
            print("{}")
            return 0
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
            if args.action in {"status", "refresh", "context"}:
                report = root / ".cache/project-memory" / (args.action + "-report.json")
                write_json(report, value)
                value = dict(value, report=str(report))
                if "classification" in value:
                    value["classification"] = {
                        key: len(paths)
                        for key, paths in value["classification"].items()
                    }
                if "freshness" in value:
                    value["freshness"] = {
                        key: {
                            **row,
                            "paths": len(row.get("paths", [])),
                            "coverage": {
                                name: len(item)
                                if isinstance(item, (list, dict)) and name != "failed"
                                else item
                                for name, item in row.get("coverage", {}).items()
                            },
                        }
                        for key, row in value["freshness"].items()
                    }
            rendered = json.dumps(value, indent=2)
            if (
                args.action in {"status", "refresh", "context"}
                and len(rendered.encode()) > 4096
            ):
                print(
                    json.dumps(
                        {
                            "status": "review_needed",
                            "report": str(report),
                            "message": "Parsed report exceeds the display budget; select an error or context group from saved evidence.",
                        }
                    )
                )
            else:
                print(rendered)
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
