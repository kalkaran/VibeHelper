#!/usr/bin/env python3
"""Format and verify files edited by the agent with capped AI output."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

EXCLUDED_DIRS = {
    ".agents",
    ".cache",
    ".claude",
    ".codex",
    ".git",
    ".venv",
    ".unlazy",
    "build",
    "coverage",
    "dist",
    ".graphify",
    "graphify-out",
    "node_modules",
    "obsidian",
    "vendor",
}
EXCLUDED_FILES = {".mcp.json"}
EDITED_FILE_LISTS = (
    ".cache/claude-edited-files.txt",
    ".cache/codex-edited-files.txt",
)
BIOME_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".css", ".json", ".jsonc"}
HTML_EXTS = {".html", ".htm"}
MARKDOWN_EXTS = {".md", ".markdown"}
PYTHON_EXTS = {".py", ".pyi"}
GO_EXTS = {".go"}
FRONTEND_DESIGN_EXTS = {
    ".astro",
    ".css",
    ".htm",
    ".html",
    ".js",
    ".jsx",
    ".less",
    ".sass",
    ".scss",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
}
PHP_EXTS = {".php"}
SHELL_EXTS = {".sh", ".bash", ".zsh"}


def run_capture(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout


def repo_root() -> Path:
    output = run_capture(["git", "rev-parse", "--show-toplevel"], Path.cwd()).strip()
    return Path(output) if output else Path.cwd()


def recorded_edited_files(root: Path) -> list[str]:
    provider = Path(__file__).resolve().parent.name
    changed = subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if changed.returncode:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "-z"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    untracked = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    git_files = {
        os.fsdecode(name)
        for name in (changed.stdout + untracked.stdout).split(b"\0")
        if name
    }
    lists = (
        (f".cache/{provider}-edited-files.txt",)
        if provider in {"codex", "claude"}
        else ()
    )
    return sorted(
        git_files
        | {
            line.strip()
            for relative in lists
            if (path := root / relative).is_file()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    )


def is_excluded(path: str) -> bool:
    parts = Path(path).parts
    return path in EXCLUDED_FILES or any(
        part in EXCLUDED_DIRS or part == "skills" or part.endswith("-skills")
        for part in parts
    )


def project_paths(root: Path, files: list[str]) -> list[str]:
    root = root.resolve()
    candidates = {}
    for file in files:
        path = root / file
        try:
            relative = Path(os.path.relpath(path, root)).as_posix()
            resolved = path.resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            continue
        if not is_excluded(relative) and not is_excluded(resolved):
            candidates[relative] = resolved
    if not candidates:
        return []
    try:
        repository = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        if (root / ".git").exists():
            raise
        return sorted(candidates)
    if repository.returncode and (root / ".git").exists():
        raise OSError(
            "Cannot determine repository: " + os.fsdecode(repository.stderr).strip()
        )
    if repository.returncode == 0:
        ignored = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            input=b"".join(
                os.fsencode(name) + b"\0"
                for name in sorted(set(candidates) | set(candidates.values()))
            ),
            cwd=root,
            capture_output=True,
            check=False,
        )
        if ignored.returncode not in (0, 1):
            raise OSError(
                "Cannot determine ignored paths: " + os.fsdecode(ignored.stderr).strip()
            )
        excluded = {os.fsdecode(name) for name in ignored.stdout.split(b"\0") if name}
        candidates = {
            name: resolved
            for name, resolved in candidates.items()
            if name not in excluded and resolved not in excluded
        }
    return sorted(candidates)


def existing_project_files(root: Path, files: list[str]) -> list[str]:
    return [name for name in project_paths(root, files) if (root / name).is_file()]


def all_project_files(root: Path) -> list[str]:
    discovered = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if discovered.returncode == 0:
        files = [os.fsdecode(name) for name in discovered.stdout.split(b"\0") if name]
    else:
        if (root / ".git").exists():
            raise OSError(
                "Cannot discover project files: "
                + os.fsdecode(discovered.stderr).strip()
            )
        files = []
        for directory, dirs, names in os.walk(root):
            dirs[:] = [name for name in dirs if not is_excluded(name)]
            files.extend(
                str((Path(directory) / name).relative_to(root)) for name in names
            )
    return existing_project_files(root, files)


def split_by_ext(files: list[str]) -> dict[str, list[str]]:
    groups = {
        "biome": [],
        "go": [],
        "html": [],
        "markdown": [],
        "php": [],
        "python": [],
        "shell": [],
    }
    for file in files:
        suffix = Path(file).suffix.lower()
        if suffix in BIOME_EXTS:
            groups["biome"].append(file)
        if suffix in GO_EXTS:
            groups["go"].append(file)
        if suffix in HTML_EXTS:
            groups["html"].append(file)
        if suffix in MARKDOWN_EXTS:
            groups["markdown"].append(file)
        if suffix in PHP_EXTS:
            groups["php"].append(file)
        if suffix in PYTHON_EXTS:
            groups["python"].append(file)
        if suffix in SHELL_EXTS:
            groups["shell"].append(file)
    return groups


def have_path(root: Path, path: str) -> bool:
    return (root / path).exists()


def have_command(command: str, root: Path) -> bool:
    return shutil.which(command) is not None


def local_impeccable_command(root: Path) -> list[str] | None:
    local_bin = root / "node_modules" / ".bin" / "impeccable"
    if local_bin.exists():
        return ["npx", "--no-install", "impeccable"]
    executable = shutil.which("impeccable")
    if executable:
        return [executable]
    return None


def markdownlint_command(root: Path) -> list[str] | None:
    local_bin = root / "node_modules" / ".bin" / "markdownlint-cli2"
    if local_bin.exists():
        return ["npx", "--no-install", "markdownlint-cli2"]
    executable = shutil.which("markdownlint-cli2")
    if executable:
        return [executable]
    return None


def quote_files(files: list[str]) -> str:
    return " ".join(shlex.quote(file) for file in files)


def quality_module():
    spec = importlib.util.spec_from_file_location(
        "quality_result", Path(__file__).with_name("ai-quality-wrapper.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


QUALITY = quality_module()


def run_wrapped(
    root: Path,
    label: str,
    command: list[str],
    *,
    adapter: str = "generic",
    shell: bool = False,
    max_lines: int = 24,
) -> int:
    value = QUALITY.run_quality(
        command,
        label=label,
        cwd=root,
        adapter=adapter,
        shell=shell,
        max_lines=max_lines,
    )
    print(QUALITY.render_result(value))
    return 0 if value["status"] == "passed" else 1


def run_phase(
    root: Path, phase: str, commands: list[tuple[str, list[str], bool, int, str]]
) -> int:
    failed = 0
    for label, command, shell, limit, adapter in commands:
        failed |= run_wrapped(
            root, label, command, shell=shell, max_lines=limit, adapter=adapter
        )
    return failed


def build_commands(
    root: Path, groups: dict[str, list[str]]
) -> tuple[
    list[tuple[str, list[str], bool, int, str]],
    list[tuple[str, list[str], bool, int, str]],
    list[tuple[str, list[str], bool, int, str]],
]:
    format_cmds: list[tuple[str, list[str], bool, int, str]] = []
    lint_cmds: list[tuple[str, list[str], bool, int, str]] = []
    type_cmds: list[tuple[str, list[str], bool, int, str]] = []

    if groups["biome"] and have_path(root, "node_modules/.bin/biome"):
        files = quote_files(groups["biome"])
        format_cmds.append(
            (
                "format-biome-edited",
                [f"npx biome check --write --max-diagnostics=none {files}"],
                True,
                20,
                "generic",
            )
        )
        lint_cmds.append(
            (
                "lint-biome-edited",
                [f"npx biome check --colors=off --max-diagnostics=none {files}"],
                True,
                24,
                "generic",
            )
        )
    if groups["html"] and have_path(root, "node_modules/.bin/htmlhint"):
        files = quote_files(groups["html"])
        lint_cmds.append(
            (
                "lint-html-edited",
                [f"npx htmlhint --nocolor --format compact {files}"],
                True,
                24,
                "generic",
            )
        )
    markdownlint = markdownlint_command(root)
    if groups["markdown"] and markdownlint:
        files = quote_files(groups["markdown"])
        command = " ".join(shlex.quote(part) for part in markdownlint)
        format_cmds.append(
            (
                "format-markdownlint-edited",
                [f"{command} --fix {files}"],
                True,
                20,
                "generic",
            )
        )
        lint_cmds.append(
            ("lint-markdown-edited", [f"{command} {files}"], True, 24, "generic")
        )
    if groups["php"]:
        files = quote_files(groups["php"])
        syntax_loop = (
            "failed=0; for file in "
            + files
            + '; do php -l "$file" || failed=1; done; exit "$failed"'
        )
        if have_command("php", root):
            lint_cmds.append(
                ("lint-php-syntax-edited", [syntax_loop], True, 20, "generic")
            )
        if have_path(root, "vendor/bin/phpcbf"):
            format_cmds.append(
                (
                    "format-phpcbf-edited",
                    [f"vendor/bin/phpcbf --standard=PSR12 --extensions=php {files}"],
                    True,
                    20,
                    "generic",
                )
            )
        if have_path(root, "vendor/bin/phpcs"):
            lint_cmds.append(
                (
                    "lint-phpcs-edited",
                    [
                        f"vendor/bin/phpcs --standard=PSR12 --extensions=php --report=json -q {files}"
                    ],
                    True,
                    24,
                    "native:phpcs-json",
                )
            )
        if have_path(root, "vendor/bin/phpstan"):
            type_cmds.append(
                (
                    "type-phpstan-edited",
                    [
                        f"vendor/bin/phpstan analyse --memory-limit=1G --no-progress --error-format=json -- {files}"
                    ],
                    True,
                    30,
                    "native:phpstan-json",
                )
            )
    if groups["python"] and have_command("ruff", root):
        files = quote_files(groups["python"])
        format_cmds.append(
            (
                "format-ruff-edited",
                [f"ruff format --force-exclude {files}"],
                True,
                20,
                "rtk:ruff-format",
            )
        )
        lint_cmds.append(
            (
                "lint-ruff-edited",
                [f"ruff check --force-exclude {files}"],
                True,
                24,
                "rtk:ruff-check",
            )
        )
    if groups["go"] and have_command("gofmt", root):
        files = quote_files(groups["go"])
        format_cmds.append(
            ("format-gofmt-edited", [f"gofmt -w {files}"], True, 20, "generic")
        )
    if groups["shell"]:
        files = quote_files(groups["shell"])
        if have_command("shfmt", root):
            format_cmds.append(
                ("format-shfmt-edited", [f"shfmt -w {files}"], True, 20, "generic")
            )
        if have_command("shellcheck", root):
            lint_cmds.append(
                (
                    "lint-shellcheck-edited",
                    [f"shellcheck -f json1 {files}"],
                    True,
                    24,
                    "native:shellcheck-json1",
                )
            )
    return format_cmds, lint_cmds, type_cmds


def missing_tools(root: Path, groups: dict[str, list[str]]) -> list[str]:
    required = {
        "biome": (have_path(root, "node_modules/.bin/biome"), "project Biome"),
        "html": (have_path(root, "node_modules/.bin/htmlhint"), "project HTMLHint"),
        "markdown": (bool(markdownlint_command(root)), "markdownlint-cli2"),
        "php": (have_command("php", root), "PHP"),
        "python": (have_command("ruff", root), "Ruff"),
        "go": (have_command("gofmt", root), "gofmt"),
        "shell": (
            have_command("shfmt", root) and have_command("shellcheck", root),
            "shfmt and ShellCheck",
        ),
    }
    return [
        f"Required {tool} is unavailable for edited {kind} files; restore the configured tool and recheck."
        for kind, (available, tool) in required.items()
        if groups[kind] and not available
    ]


def check_files(root: Path, selected: list[str], *, timeout: float = 100) -> dict:
    started = time.monotonic()
    try:
        files = existing_project_files(root, selected)
    except OSError as exc:
        return QUALITY.result("tool_failure", "file selection", [str(exc)])
    if not files:
        return QUALITY.result(
            "not_checked",
            "edited files",
            [
                "Selected paths are absent or excluded; verify deletions or select source files."
            ],
        )
    groups = split_by_ext(files)
    commands = [command for phase in build_commands(root, groups) for command in phase]
    missing = missing_tools(root, groups)
    results = []
    for label, command, shell, max_lines, adapter in commands:
        remaining = timeout - (time.monotonic() - started) - 2
        if remaining <= 0:
            results.append(
                QUALITY.result(
                    "not_checked",
                    label,
                    ["Check budget exhausted; rerun the controlled verifier."],
                )
            )
            break
        success_codes = (0,)
        formatter_major = None
        if label == "format-phpcbf-edited":
            version = QUALITY.run_quality(
                ["vendor/bin/phpcbf", "--version"],
                label="phpcbf-version",
                cwd=root,
                timeout=min(remaining, 5),
            )
            results.append(version)
            if version["status"] != "passed":
                continue
            # PHPCBF 3.x reports successful fixes as 1; 4.x uses cumulative issue bits.
            if any(
                re.search(r"PHP_CodeSniffer version 3\.", line)
                for line in version["summary"]
            ):
                success_codes = (0, 1)
                formatter_major = 3
            elif any(
                re.search(r"PHP_CodeSniffer version 4\.", line)
                for line in version["summary"]
            ):
                formatter_major = 4
            remaining = timeout - (time.monotonic() - started) - 2
        value = QUALITY.run_quality(
            command,
            label=label,
            cwd=root,
            shell=shell,
            adapter=adapter,
            max_lines=max_lines,
            timeout=max(0.1, min(remaining, 90)),
            success_codes=success_codes,
        )
        if label == "format-phpcbf-edited" and (
            (formatter_major == 3 and value["returncode"] == 3)
            or (formatter_major == 4 and value["returncode"] & (4 | 16 | 64))
        ):
            value["status"] = "tool_failure"
            value["summary"].insert(
                0,
                "PHPCBF could not complete processing or fixing; inspect the formatter log before retrying.",
            )
        results.append(value)
    if missing:
        results.append(QUALITY.result("not_checked", "missing required tools", missing))
    if not results:
        return QUALITY.result(
            "not_checked",
            "edited files",
            [
                "No checks executed for the selected files; run the project's applicable verifier."
            ],
        )
    priority = {"passed": 0, "findings": 1, "not_checked": 2, "tool_failure": 3}
    status = max((value["status"] for value in results), key=priority.get)
    summary = []
    for value in sorted(results, key=lambda item: -priority[item["status"]]):
        if value["status"] == "passed":
            summary.append(f"{value['label']}: passed")
        else:
            summary.extend([f"{value['label']}: {value['status']}", *value["summary"]])
            if value.get("log"):
                summary.append(f"Full log: {value['log']}")
    signatures = [
        item.get("diagnostic_signature", "")
        for item in results
        if item["status"] != "passed"
    ]
    return QUALITY.result(
        status,
        "edited files",
        summary,
        results=results,
        files=files,
        command_count=sum(item["command_count"] for item in results),
        duration_seconds=round(time.monotonic() - started, 4),
        output_bytes=sum(item["output_bytes"] for item in results),
        diagnostic_signature=QUALITY.hashlib.sha256(
            json.dumps([missing, signatures]).encode()
        ).hexdigest(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Format and check selected files with structured diagnostics."
    )
    parser.add_argument(
        "--list-files",
        nargs="*",
        metavar="EXTENSION",
        help="List project files separated by NUL bytes, optionally filtered by extension.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=float, default=100)
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    root = repo_root()
    os.chdir(root)
    if args.list_files is not None:
        try:
            files = all_project_files(root)
        except OSError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        sys.stdout.buffer.write(
            b"".join(
                os.fsencode("./" + name) + b"\0"
                for name in files
                if not args.list_files or Path(name).suffix.lower() in args.list_files
            )
        )
        return 0
    value = check_files(
        root, args.files or recorded_edited_files(root), timeout=args.timeout
    )
    print(json.dumps(value) if args.json else QUALITY.render_result(value))
    return (
        0
        if value["status"] == "passed"
        else 2
        if value["status"] == "not_checked"
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
