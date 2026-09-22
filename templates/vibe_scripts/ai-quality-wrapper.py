#!/usr/bin/env python3
"""Run a quality command with AI-safe output."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return value.strip("-") or "quality-command"


def trim_lines(text: str, max_lines: int) -> tuple[list[str], bool]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return lines, False
    head_count = max_lines // 2
    tail_count = max_lines - head_count
    return lines[:head_count] + ["... output truncated ..."] + lines[-tail_count:], True


def summarize_known_success(output: str, returncode: int) -> list[str] | None:
    if returncode != 0:
        return None
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if lines and all(
        line.startswith("No syntax errors detected in ") for line in lines
    ):
        return [f"PHP syntax check passed for {len(lines)} files."]
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a command and print a capped AI-safe summary."
    )
    parser.add_argument("--label", required=True)
    parser.add_argument("--log-dir", default=".cache/ai-quality")
    parser.add_argument("--max-lines", type=int, default=30)
    parser.add_argument("--shell", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing command after --")
    if args.max_lines < 4:
        parser.error("--max-lines must be at least 4")
    return args


def main() -> int:
    args = parse_args()
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (
        log_dir
        / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(args.label)}.log"
    )
    if args.shell:
        command_display = args.command[0]
        run_command: str | list[str] = args.command[0]
    else:
        command_display = " ".join(args.command)
        run_command = args.command
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    completed = subprocess.run(
        run_command,
        shell=args.shell,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        check=False,
    )
    output = completed.stdout or ""
    log_path.write_text(
        f"$ {command_display}\nexit_code={completed.returncode}\n\n{output}",
        encoding="utf-8",
    )
    status = "ok" if completed.returncode == 0 else f"failed ({completed.returncode})"
    print(f"[ai-quality] {args.label}: {status}")
    print(f"[ai-quality] full log: {log_path}")
    known_success = summarize_known_success(output, completed.returncode)
    if known_success is not None:
        print("[ai-quality] summary:")
        for line in known_success:
            print(line)
        return completed.returncode
    summary_lines, truncated = trim_lines(output, args.max_lines)
    if summary_lines:
        print(f"[ai-quality] capped output ({len(summary_lines)} lines):")
        for line in summary_lines:
            print(line)
    else:
        print("[ai-quality] no output")
    if truncated:
        print(
            f"[ai-quality] output was truncated for the AI transcript; inspect {log_path} for the full output."
        )
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
