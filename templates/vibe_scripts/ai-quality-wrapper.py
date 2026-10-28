#!/usr/bin/env python3
"""Run a quality command with AI-safe output."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

RTK_FILTERS = {
    "rtk:cargo-test": "cargo-test",
    "rtk:go-test": "go-test",
    "rtk:mypy": "mypy",
    "rtk:pytest": "pytest",
    "rtk:ruff-check": "ruff-check",
    "rtk:ruff-format": "ruff-format",
}


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return value.strip("-") or "quality-command"


def parse_text_diagnostics(
    output: str, returncode: int, first_line: int = 2
) -> list[str]:
    """Select diagnostic records, never console headers or arbitrary log tails."""
    records = []
    seen = set()
    unknown = []
    source = ""
    for number, raw in enumerate(output.splitlines(), first_line):
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
        if not line:
            continue
        if re.fullmatch(
            r"(?:Ruff(?: check)?:\s*)?All checks passed[.!]?", line, re.IGNORECASE
        ):
            records.append("PASS")
            continue
        if re.match(r"^[=_─━]{3}", line):
            if re.search(r"\d+ (?:passed|failed|skipped|errors?)", line):
                records.append(f"log:{number}: {line.strip('= ')}")
            continue
        if line.startswith("PHP_CodeSniffer version "):
            records.append(line)
            continue
        if re.fullmatch(r"\S+\.(?:js|jsx|ts|tsx|vue|svelte|py|go|rs|php|sh|md)", line):
            source = line
            continue
        if source and re.match(r"\d+:\d+\s", line):
            line = source + ":" + line
        counts = re.search(
            r"\b\d+ (?:passed|failed|skipped|errors?|warnings?|files? (?:left unchanged|reformatted))\b",
            line,
        )
        diagnostic = (
            re.match(
                r"(?:FAILED |ERROR |E\s+|error(?:\[|:)|warning(?:\[|:)|panic:)", line
            )
            or re.search(r"(?:^|\s)[\w./-]+:\d+(?::\d+)?:?\s+\S", line)
            or re.match(r"(?:[A-Z][A-Za-z]*)?(?:Error|Exception):", line)
            or re.match(r"[A-Z]+\d{2,}\s", line)
            or re.fullmatch(r"(?:-->\s+)?\S+:\d+(?::\d+)?", line)
            or re.match(r"(?:Expected|Received|Actual|Caused by):", line, re.IGNORECASE)
            or re.match(r"(?:FAIL|FAILURES|error|UNPARSED|REVIEW)\s*[: ]\s*\S", line)
        )
        if (diagnostic or counts) and line not in seen:
            seen.add(line)
            records.append(f"log:{number}: {line}")
        elif not (diagnostic or counts) and not (
            raw.startswith(">")
            or re.match(
                r"[.FsEx%\d \[\]]+$|(?:platform |rootdir:|plugins:|collected |tests/|tmp_path =|source =|exit_code=)",
                line,
            )
        ):
            unknown.append(number)
    if unknown:
        kind = "FAILURE" if returncode else "OUTPUT"
        records.append(
            f"UNPARSED {kind}: {len(unknown)} lines; first at log:{unknown[0]}. Discuss the format or request targeted context."
        )
    if records:
        return records
    return [
        "PASS"
        if returncode == 0
        else "UNPARSED FAILURE: discuss the output format before reading a targeted log section."
    ]


def summarize_known_success(output: str, returncode: int) -> list[str] | None:
    if returncode != 0:
        return None
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if lines and all(
        line.startswith("No syntax errors detected in ") for line in lines
    ):
        return [f"PHP syntax check passed for {len(lines)} files."]
    return None


def json_documents(output: str) -> tuple[list[object], list[str]]:
    decoder = json.JSONDecoder()
    documents: list[object] = []
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(output):
        while cursor < len(output) and output[cursor].isspace():
            cursor += 1
        if cursor < len(output) and output[cursor] in "[{":
            start = cursor
        else:
            match = re.search(r"(?m)^[ \t]*(?=[\[{])", output[cursor:])
            if match is None:
                break
            start = cursor + match.end()
        try:
            document, end = decoder.raw_decode(output, start)
        except json.JSONDecodeError:
            newline = output.find("\n", start)
            cursor = len(output) if newline < 0 else newline + 1
            continue
        documents.append(document)
        spans.append((start, end))
        cursor = end

    residue_parts: list[str] = []
    previous = 0
    for start, end in spans:
        residue_parts.append(output[previous:start])
        previous = end
    residue_parts.append(output[previous:])
    residue = [
        line for part in residue_parts for line in part.splitlines() if line.strip()
    ]
    return documents, residue


def integer(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def one_line(value: object) -> str:
    return " ".join(str(value).split())


def text_value(value: object) -> str:
    if isinstance(value, str):
        return one_line(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def diagnostic(
    path: object,
    line: object,
    column: object,
    severity: object,
    code: object,
    message: object,
    detail: str = "",
) -> str:
    location = one_line(path or "<unknown>")
    if line is not None:
        location += f":{one_line(line)}"
        if column is not None:
            location += f":{one_line(column)}"
    metadata = " ".join(
        part
        for part in (
            one_line(severity or "issue").lower(),
            one_line(code or ""),
            one_line(detail),
        )
        if part
    )
    return f"{location} [{metadata}] {text_value(message)}"


def parse_phpcs(documents: list[object]) -> tuple[str, list[str]] | None:
    errors = warnings = fixable = 0
    files_seen = 0
    diagnostics: list[str] = []
    for document in documents:
        if not isinstance(document, dict):
            return None
        totals = document.get("totals")
        files = document.get("files")
        if not isinstance(totals, dict) or not isinstance(files, dict):
            return None
        errors += integer(totals.get("errors"))
        warnings += integer(totals.get("warnings"))
        fixable += integer(totals.get("fixable"))
        files_seen += len(files)
        for path, report in sorted(files.items()):
            if not isinstance(report, dict) or not isinstance(
                report.get("messages", []), list
            ):
                return None
            for item in report.get("messages", []):
                if not isinstance(item, dict):
                    return None
                detail = f"severity={item.get('severity', 0)}"
                if item.get("fixable"):
                    detail += " fixable"
                diagnostics.append(
                    diagnostic(
                        path,
                        item.get("line"),
                        item.get("column"),
                        item.get("type"),
                        item.get("source"),
                        item.get("message", "PHPCS issue"),
                        detail,
                    )
                )
    return (
        f"PHPCS: {errors} errors, {warnings} warnings, {fixable} fixable across {files_seen} files.",
        diagnostics,
    )


def parse_phpstan(documents: list[object]) -> tuple[str, list[str]] | None:
    global_errors = file_errors = files_seen = 0
    diagnostics: list[str] = []
    for document in documents:
        if not isinstance(document, dict):
            return None
        totals = document.get("totals")
        files = document.get("files")
        errors = document.get("errors", [])
        if (
            not isinstance(totals, dict)
            or not isinstance(files, dict)
            or not isinstance(errors, list)
        ):
            return None
        global_errors += integer(totals.get("errors"))
        file_errors += integer(totals.get("file_errors"))
        for error in errors:
            diagnostics.append(
                diagnostic("<phpstan>", None, None, "error", "global", error)
            )
        files_seen += len(files)
        for path, report in sorted(files.items()):
            if not isinstance(report, dict) or not isinstance(
                report.get("messages", []), list
            ):
                return None
            for item in report.get("messages", []):
                if not isinstance(item, dict):
                    return None
                diagnostics.append(
                    diagnostic(
                        path,
                        item.get("line"),
                        None,
                        "error",
                        item.get("identifier") or "phpstan",
                        item.get("message", "PHPStan error"),
                    )
                )
    return (
        f"PHPStan: {global_errors} global errors, {file_errors} file errors across {files_seen} files.",
        diagnostics,
    )


def parse_shellcheck(documents: list[object]) -> tuple[str, list[str]] | None:
    diagnostics: list[str] = []
    levels: dict[str, int] = {}
    for document in documents:
        if isinstance(document, list):
            comments = document
        elif isinstance(document, dict) and isinstance(document.get("comments"), list):
            comments = document["comments"]
        else:
            return None
        for item in comments:
            if not isinstance(item, dict):
                return None
            level = str(item.get("level") or "issue").lower()
            levels[level] = levels.get(level, 0) + 1
            code = item.get("code")
            diagnostics.append(
                diagnostic(
                    item.get("file"),
                    item.get("line"),
                    item.get("column"),
                    level,
                    f"SC{code}" if code is not None else "shellcheck",
                    item.get("message", "ShellCheck issue"),
                )
            )
    counts = ", ".join(f"{count} {level}" for level, count in sorted(levels.items()))
    return (
        f"ShellCheck: {len(diagnostics)} issues{f' ({counts})' if counts else ''}.",
        diagnostics,
    )


def parse_pyright(documents: list[object]) -> tuple[str, list[str]] | None:
    diagnostics: list[str] = []
    errors = warnings = information = files = 0
    fatal_flags = 0
    for document in documents:
        if not isinstance(document, dict) or not isinstance(
            document.get("generalDiagnostics"), list
        ):
            return None
        summary = document.get("summary", {})
        if not isinstance(summary, dict):
            return None
        errors += integer(summary.get("errorCount"))
        warnings += integer(summary.get("warningCount"))
        information += integer(summary.get("informationCount"))
        files += integer(summary.get("filesAnalyzed"))
        for flag, message in (
            ("fatalErrorOccurred", "Pyright reported a fatal error"),
            ("configParseErrorOccurred", "Pyright could not parse its configuration"),
        ):
            if document.get(flag):
                fatal_flags += 1
                diagnostics.append(
                    diagnostic("<pyright>", None, None, "error", flag, message)
                )
        for item in document["generalDiagnostics"]:
            if not isinstance(item, dict):
                return None
            location = item.get("range", {})
            if not isinstance(location, dict):
                location = {}
            start = location.get("start", {})
            if not isinstance(start, dict):
                start = {}
            line = integer(start.get("line")) + 1
            column = integer(start.get("character")) + 1
            diagnostics.append(
                diagnostic(
                    item.get("file"),
                    line,
                    column,
                    item.get("severity"),
                    item.get("rule") or "pyright",
                    item.get("message", "Pyright issue"),
                )
            )
    return (
        f"Pyright: {errors} errors, {warnings} warnings, {information} informational across {files} files; {fatal_flags} fatal/config errors.",
        diagnostics,
    )


NATIVE_ADAPTERS = {
    "native:phpcs-json": parse_phpcs,
    "native:phpstan-json": parse_phpstan,
    "native:pyright-json": parse_pyright,
    "native:shellcheck-json1": parse_shellcheck,
}


def diagnostic_priority(line: str) -> int:
    match = re.search(r" \[([a-z]+)\b", line, re.IGNORECASE)
    level = match.group(1).lower() if match else ""
    if "fatal" in level:
        return 0
    return {
        "error": 1,
        "warning": 2,
        "information": 3,
        "info": 3,
        "style": 3,
    }.get(level, 4)


def render_native(
    summary: str, diagnostics: list[str], residue: list[str], max_lines: int
) -> list[str]:
    if residue:
        summary += f" {len(residue)} non-JSON output lines also emitted."
    lines = [summary]
    lines.extend(sorted(diagnostics, key=diagnostic_priority))
    if residue:
        lines.append(
            "REVIEW NEEDED: unrecognized non-JSON output is retained in the log; agree on a targeted section before reading it."
        )
    return lines


def adapter_summary(
    adapter: str, output: str, returncode: int, max_lines: int
) -> tuple[list[str] | None, str | None]:
    if adapter in RTK_FILTERS:
        if not output:
            return None, "the tool produced no output"
        rtk = shutil.which("rtk")
        if not rtk:
            return None, "rtk is not installed"
        try:
            completed = subprocess.run(
                [rtk, "pipe", "--filter", RTK_FILTERS[adapter]],
                input=output,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "rtk timed out after 5 seconds"
        except OSError as error:
            return None, f"rtk could not run ({one_line(error)})"
        if completed.returncode != 0:
            return None, f"rtk exited {completed.returncode}"
        if not completed.stdout.strip():
            return None, "rtk returned no summary"
        if (
            adapter == "rtk:go-test"
            and returncode != 0
            and re.fullmatch(
                r"\s*(?:go test:\s*)?no tests found[.!]?\s*",
                completed.stdout,
                re.IGNORECASE,
            )
        ):
            return None, "rtk reported no tests for a failed Go test run"
        if (
            adapter == "rtk:pytest"
            and returncode != 0
            and re.search(
                r"no tests (?:collected|found)", completed.stdout, re.IGNORECASE
            )
        ):
            return None, "rtk reported no tests for a failed pytest run"
        if returncode != 0:
            # Filter summaries alone can discard the assertion/file needed for repair.
            return parse_text_diagnostics(output, returncode), None
        filtered = parse_text_diagnostics(completed.stdout, returncode)
        if any("UNPARSED" in line for line in filtered):
            return parse_text_diagnostics(output, returncode), None
        return [re.sub(r"^log:\d+: ", "", line) for line in filtered], None

    parser = NATIVE_ADAPTERS.get(adapter)
    if parser is not None:
        try:
            documents, residue = json_documents(output)
            if not documents:
                return None, "no complete JSON document was found"
            parsed = parser(documents)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            OverflowError,
            RecursionError,
        ) as error:
            return None, f"adapter parser failed ({type(error).__name__})"
        if parsed is None:
            return None, "the JSON schema was not recognized"
        summary, diagnostics = parsed
        if returncode != 0 and not diagnostics:
            return None, "the tool failed without any parsed diagnostics"
        document_line = 2
        for match in re.finditer(r"[\[{]", output):
            try:
                json.JSONDecoder().raw_decode(output, match.start())
            except ValueError:
                continue
            document_line += output[: match.start()].count("\n")
            break
        diagnostics = [
            f"log:{document_line} (JSON output starts): {line}" for line in diagnostics
        ]
        return render_native(summary, diagnostics, residue, max_lines), None

    if adapter == "generic":
        return None, None
    return None, f"unknown adapter {adapter!r}"


def result(status: str, label: str, summary: list[str], **values: object) -> dict:
    return {
        "schema": 1,
        "status": status,
        "label": label,
        "summary": summary,
        "command_count": 0,
        "duration_seconds": 0,
        "output_bytes": 0,
        **values,
    }


def terminate_group(process: subprocess.Popen) -> tuple[int, str | None]:
    """Bound cleanup even when grandchildren retain the output pipe."""
    descendants = set()
    limitation = None
    try:
        listing = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            check=False,
            timeout=0.3,
        )
        if listing.returncode:
            limitation = "Child discovery unavailable: " + one_line(listing.stderr)
        pairs = [
            tuple(map(int, line.split()))
            for line in listing.stdout.splitlines()
            if len(line.split()) == 2
        ]
        parents = {process.pid}
        while parents:
            children = {pid for pid, parent in pairs if parent in parents} - descendants
            descendants.update(children)
            parents = children
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        limitation = "Child discovery unavailable: " + one_line(error)
    for pid in descendants:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as error:
            if not isinstance(error, ProcessLookupError):
                limitation = "Child termination failed: " + one_line(error)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.4)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for pid in descendants:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=0.4)
    except subprocess.TimeoutExpired:
        limitation = "The command did not exit after forced termination."
    if limitation:
        limitation += (
            " Detached children may remain; inspect the process tree before retrying."
        )
    return 1, limitation


def run_quality(
    command: list[str],
    *,
    label: str,
    cwd: Path | None = None,
    shell: bool = False,
    adapter: str = "generic",
    max_lines: int = 24,
    timeout: float = 90,
    log_dir: Path | str = ".cache/ai-quality",
    success_codes: tuple[int, ...] = (0,),
) -> dict:
    started = time.monotonic()
    log_path = Path(log_dir) / (
        f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{os.getpid()}-{slugify(label)}.log"
    )
    if cwd and not log_path.is_absolute():
        log_path = cwd / log_path
    output = bytearray()
    process = None
    failure = None
    code = 125
    count = 0

    def cleanup() -> None:
        nonlocal count, failure
        cleanup_commands, limitation = terminate_group(process)
        count += cleanup_commands
        if limitation:
            failure = (failure + " " if failure else "") + limitation

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("xb", buffering=0) as log:
            log.write(("$ " + " ".join(command) + "\n").encode())
            env = os.environ.copy()
            env.setdefault("NO_COLOR", "1")
            process = subprocess.Popen(
                command[0] if shell else command,
                shell=shell,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            count = 1
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = started + timeout
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        failure = f"Timed out after {timeout:g}s; inspect partial log and check for a hung child or unavailable service."
                        cleanup()
                        # Drain buffered output after the entire process group has stopped.
                        os.set_blocking(process.stdout.fileno(), False)
                        drain_deadline = time.monotonic() + 0.2
                        while time.monotonic() < drain_deadline:
                            try:
                                chunk = os.read(process.stdout.fileno(), 65536)
                            except BlockingIOError:
                                break
                            if not chunk:
                                break
                            output.extend(chunk)
                            log.write(chunk)
                        break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            log.write(chunk)
                if failure:
                    code = 124
                else:
                    try:
                        code = process.wait(
                            timeout=max(0.01, deadline - time.monotonic())
                        )
                    except subprocess.TimeoutExpired:
                        failure = f"Timed out after {timeout:g}s; inspect the log and check the process."
                        cleanup()
                        code = 124
            log.write(f"\nexit_code={code}\n".encode())
    except (OSError, ValueError) as error:
        failure = f"Unable to run or log {label}: {one_line(error)}. Check tool installation, permissions and available disk space."
        if process is not None:
            cleanup()
    finally:
        if process is not None and process.stdout is not None:
            process.stdout.close()
    text = output.decode("utf-8", errors="replace")
    if adapter in RTK_FILTERS and text and shutil.which("rtk"):
        count += 1
    parsed, fallback = adapter_summary(adapter, text, code, max_lines)
    if parsed is None:
        parsed = summarize_known_success(text, code)
    if parsed is None:
        parsed = parse_text_diagnostics(text, code)
    status = "passed" if code in success_codes else "findings"
    if (
        failure
        or code < 0
        or code in (124, 125, 126, 127)
        or re.search(
            r"(?i)(command not found|permission denied|no such file or directory|no module named|could not resolve|ENOTFOUND|unauthorized|authentication failed|Traceback \(most recent call last\))",
            text,
        )
        or (adapter in NATIVE_ADAPTERS and fallback)
        or (code != 0 and not text.strip())
    ):
        status = "tool_failure"
    if adapter in NATIVE_ADAPTERS and not fallback:
        documents, _ = json_documents(text)
        if any(
            isinstance(document, dict)
            and (
                document.get("fatalErrorOccurred")
                or document.get("configParseErrorOccurred")
                or (adapter == "native:phpstan-json" and document.get("errors"))
            )
            for document in documents
        ):
            status = "tool_failure"
        if adapter == "native:shellcheck-json1" and code > 1:
            status = "tool_failure"
    if adapter == "rtk:pytest" and code == 5:
        status = "not_checked"
        parsed.insert(
            0,
            "No tests were collected; select valid tests before claiming verification.",
        )
    if fallback:
        parsed.insert(
            0, f"Diagnostic adapter: {fallback}; inspect raw log for complete output."
        )
    if failure:
        parsed.insert(0, failure)
    if not parsed:
        parsed = [
            "Command completed with no output."
            if status == "passed"
            else "Command failed without diagnostics; inspect its installation and log."
        ]
    stable_diagnostics = re.sub(
        r"\b\d+(?:\.\d+)?\s*(?:s|seconds)\b", "<duration>", "\n".join(parsed)
    )
    signature = hashlib.sha256(stable_diagnostics.encode()).hexdigest()
    value = result(
        status,
        label,
        parsed,
        returncode=code,
        execution_error=failure,
        log=str(log_path) if log_path.is_file() else None,
        command_count=count,
        duration_seconds=round(time.monotonic() - started, 4),
        output_bytes=len(output),
        diagnostic_signature=signature,
    )
    save_summary(value, root=cwd or Path.cwd(), max_lines=max_lines)
    return value


def save_summary(value: dict, *, root: Path, max_lines: int = 30) -> None:
    """Keep a bounded entry point beside complete diagnostic evidence."""
    if value.get("summary_log"):
        path = Path(value["summary_log"])
    elif value.get("log"):
        path = Path(value["log"]).with_suffix(".summary.log")
    elif value.get("evidence"):
        path = (root / value["evidence"]).with_suffix(".summary.log")
    else:
        path = (
            root
            / ".cache/ai-quality"
            / f"{time.time_ns()}-{os.getpid()}-{slugify(value['label'])}.summary.log"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        value["summary_log"] = str(path)
        if not value.get("evidence"):
            evidence = path.with_suffix(".json")
            value["evidence"] = str(evidence)
            evidence.write_text(json.dumps(value), encoding="utf-8")
        bounded = dict(value)
        bounded["summary"] = value["summary"]
        path.write_text(render_result(bounded, max_lines) + "\n", encoding="utf-8")
    except OSError as exc:
        value.pop("summary_log", None)
        value["status"] = "tool_failure"
        value.pop("explicitly_skipped", None)
        value["summary"].insert(
            0,
            f"Cannot save summary log: {one_line(exc)}. Check disk space and permissions.",
        )


def bound_output(text: str, max_lines: int = 30, max_bytes: int = 4096) -> str:
    """Bound model-facing text after metadata; complete evidence remains on disk."""
    if len(text.splitlines()) <= max_lines and len(text.encode()) <= max_bytes:
        return text
    lines = text.splitlines()
    references = [
        line
        for line in lines
        if line.startswith(
            (
                "Full result:",
                "[ai-quality] summary log",
                "[ai-quality] full log",
                "[ai-quality] duration",
            )
        )
    ]
    example = next(
        (
            line
            for line in lines[1:]
            if re.search(r"(?:log:|\S+\.\w+:)\d+", line) and len(line.encode()) <= 500
        ),
        None,
    )
    return "\n".join(
        [
            lines[0],
            *([example] if example else []),
            "REVIEW NEEDED: diagnostic report exceeds the display budget; select a failure group from saved evidence. No raw output returned.",
            *references,
        ]
    )


def render_result(value: dict, max_lines: int = 30) -> str:
    lines = [f"[ai-quality] {value['label']}: {value['status']}"]
    details = value.get("summary", [])
    if value["status"] == "passed":
        details = [
            line
            for line in details
            if re.search(
                r"\b\d+ (?:passed|tests?|issues|errors|files?)\b|^PASS:|warning|skipped|REVIEW|UNPARSED",
                line,
                re.IGNORECASE,
            )
        ] or ["PASS"]
    lines.extend(details)
    lines.append(
        f"[ai-quality] duration: {value.get('duration_seconds', 0)}s; exit code: {value.get('returncode', 'not available')}"
    )
    if value.get("summary_log"):
        lines.append(f"[ai-quality] summary log (read first): {value['summary_log']}")
    if value.get("log"):
        lines.append(f"[ai-quality] full log: {value['log']}")
    if value.get("evidence"):
        lines.append(f"Full result: {value['evidence']}")
    return bound_output("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a quality command with bounded diagnostics and a full streamed log."
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--adapter", default="generic")
    parser.add_argument("--log-dir", default=".cache/ai-quality")
    parser.add_argument("--max-lines", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report-log", type=Path)
    parser.add_argument("--context-line", type=int)
    parser.add_argument("--shell", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.context_line is not None and (args.context_line < 1 or not args.report_log):
        parser.error("--context-line requires --report-log and a positive line number")
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if (
        (not args.command and not args.report_log)
        or args.max_lines < 4
        or args.timeout <= 0
    ):
        parser.error("provide a command, --max-lines >= 4 and a positive --timeout")
    return args


@contextlib.contextmanager
def receipt_guard(receipt: Path):
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.with_suffix(".guard").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        yield


def main() -> int:
    args = parse_args()
    if args.report_log:
        if args.context_line:
            start = max(1, args.context_line - 5)
            context = []
            with args.report_log.open(errors="replace") as stream:
                for number, line in enumerate(stream, 1):
                    if start <= number <= args.context_line + 5:
                        context.append(f"{number}: {line.rstrip()}")
                    if number > args.context_line + 5:
                        break
            text = "\n".join(context)
            if len(text.encode()) > 4096:
                print(
                    "REVIEW NEEDED: requested error section exceeds 4096 bytes; agree on a narrower field or record before reading it."
                )
            else:
                print(text)
            return 0
        destination = Path(args.log_dir) / f"{time.time_ns()}-reported.log"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.report_log, destination)
        value = result(
            "findings",
            "saved failure",
            parse_text_diagnostics(destination.read_text(errors="replace"), 1, 1),
            log=str(destination),
            returncode=1,
        )
        save_summary(value, root=Path.cwd())
        print(render_result(value))
        return 0
    if args.label.startswith("test-"):
        receipt = (
            Path.cwd()
            / ".cache"
            / "quality-tests"
            / (Path(__file__).parent.name + ".json")
        )
        with receipt_guard(receipt):
            try:
                current = json.loads(receipt.read_text())
            except (OSError, ValueError):
                current = {}
            if (
                not isinstance(current, dict)
                or not os.environ.get("VIBEHELPER_TEST_RUN")
                or current.get("token") != os.environ.get("VIBEHELPER_TEST_RUN")
            ):
                receipt.unlink(missing_ok=True)
    value = run_quality(
        args.command,
        label=args.label,
        shell=args.shell,
        adapter=args.adapter,
        max_lines=args.max_lines,
        timeout=args.timeout,
        log_dir=args.log_dir,
    )
    print(json.dumps(value) if args.json else render_result(value, args.max_lines))
    return (
        0
        if value["status"] == "passed"
        else (value["returncode"] if 0 < value["returncode"] < 256 else 2)
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as error:
        print(f"REVIEW NEEDED: reporting failed: {error}", file=sys.stderr)
        sys.exit(2)
