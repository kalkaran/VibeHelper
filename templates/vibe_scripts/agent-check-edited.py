#!/usr/bin/env python3
"""Format and verify files edited by the agent.

Default input is the current Git changed/untracked file set. Commands are routed
through vibe_scripts/ai-quality-wrapper.py so full output is logged while the AI
transcript receives capped summaries.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


EXCLUDED_DIRS = {
    ".agents",
    ".cache",
    ".claude",
    ".codex",
    ".git",
    ".venv",
    "build",
    "coverage",
    "dist",
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
    return sorted(
        {
            line.strip()
            for relative in EDITED_FILE_LISTS
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


def existing_project_files(root: Path, files: list[str]) -> list[str]:
    result: list[str] = []
    for file in files:
        normalized = file.strip()
        if not normalized or is_excluded(normalized):
            continue
        full_path = (root / normalized).resolve()
        try:
            full_path.relative_to(root.resolve())
        except ValueError:
            continue
        if full_path.is_file():
            result.append(normalized)
    return sorted(set(result))


def split_by_ext(files: list[str]) -> dict[str, list[str]]:
    groups = {"biome": [], "html": [], "markdown": [], "php": [], "shell": []}
    for file in files:
        suffix = Path(file).suffix.lower()
        if suffix in BIOME_EXTS:
            groups["biome"].append(file)
        if suffix in HTML_EXTS:
            groups["html"].append(file)
        if suffix in MARKDOWN_EXTS:
            groups["markdown"].append(file)
        if suffix in PHP_EXTS:
            groups["php"].append(file)
        if suffix in SHELL_EXTS:
            groups["shell"].append(file)
    return groups


def have_path(root: Path, path: str) -> bool:
    return (root / path).exists()


def have_command(command: str, root: Path) -> bool:
    return shutil.which(command) is not None


def quote_files(files: list[str]) -> str:
    return " ".join(shlex.quote(file) for file in files)


def markdownlint_command(root: Path) -> list[str] | None:
    local_bin = root / "node_modules" / ".bin" / "markdownlint-cli2"
    if local_bin.exists():
        return ["npx", "--no-install", "markdownlint-cli2"]
    executable = shutil.which("markdownlint-cli2")
    if executable:
        return [executable]
    return None


def wrapper_command(
    root: Path,
    label: str,
    command: list[str],
    *,
    shell: bool = False,
    max_lines: int = 24,
) -> list[str]:
    wrapper = root / "vibe_scripts" / "ai-quality-wrapper.py"
    args = [
        sys.executable,
        str(wrapper),
        "--label",
        label,
        "--max-lines",
        str(max_lines),
    ]
    if shell:
        args.append("--shell")
    args.append("--")
    args.extend(command)
    return args


def run_wrapped(
    root: Path,
    label: str,
    command: list[str],
    *,
    shell: bool = False,
    max_lines: int = 24,
) -> int:
    completed = subprocess.run(
        wrapper_command(root, label, command, shell=shell, max_lines=max_lines),
        cwd=root,
        check=False,
    )
    return completed.returncode


def run_phase(
    root: Path, phase: str, commands: list[tuple[str, list[str], bool, int, bool]]
) -> int:
    if not commands:
        print(f"[edited-check] {phase}: no applicable commands")
        return 0
    print(f"[edited-check] {phase}")
    sys.stdout.flush()
    failed = 0
    for label, command, shell, max_lines, allow_failure in commands:
        code = run_wrapped(root, label, command, shell=shell, max_lines=max_lines)
        if code != 0 and not allow_failure:
            failed = 1
    return failed


def build_commands(
    root: Path, groups: dict[str, list[str]]
) -> tuple[
    list[tuple[str, list[str], bool, int, bool]],
    list[tuple[str, list[str], bool, int, bool]],
    list[tuple[str, list[str], bool, int, bool]],
]:
    format_cmds: list[tuple[str, list[str], bool, int, bool]] = []
    lint_cmds: list[tuple[str, list[str], bool, int, bool]] = []
    type_cmds: list[tuple[str, list[str], bool, int, bool]] = []

    if groups["biome"] and have_path(root, "node_modules/.bin/biome"):
        files = quote_files(groups["biome"])
        format_cmds.append(
            (
                "format-biome-edited",
                [f"npx biome check --write {files}"],
                True,
                20,
                False,
            )
        )
        lint_cmds.append(
            (
                "lint-biome-edited",
                [f"npx biome check --colors=off --max-diagnostics=20 {files}"],
                True,
                24,
                False,
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
                False,
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
                False,
            )
        )
        lint_cmds.append(
            ("lint-markdown-edited", [f"{command} {files}"], True, 24, False)
        )

    if groups["php"]:
        files = quote_files(groups["php"])
        syntax_loop = "for file in " + files + '; do php -l "$file"; done'
        if have_command("php", root):
            lint_cmds.append(("lint-php-syntax-edited", [syntax_loop], True, 20, False))
        if have_path(root, "vendor/bin/phpcbf"):
            format_cmds.append(
                (
                    "format-phpcbf-edited",
                    [
                        f"vendor/bin/phpcbf --standard=PSR12 --extensions=php {files} || true"
                    ],
                    True,
                    20,
                    True,
                )
            )
        if have_path(root, "vendor/bin/phpcs"):
            lint_cmds.append(
                (
                    "lint-phpcs-edited",
                    [
                        f"vendor/bin/phpcs --standard=PSR12 --extensions=php --report=summary -q {files}"
                    ],
                    True,
                    24,
                    False,
                )
            )
        if have_path(root, "vendor/bin/phpstan"):
            type_cmds.append(
                (
                    "type-phpstan-edited",
                    [
                        f"vendor/bin/phpstan analyse --memory-limit=1G --no-progress --error-format=table -- {files}"
                    ],
                    True,
                    30,
                    False,
                )
            )

    if groups["shell"]:
        files = quote_files(groups["shell"])
        if have_command("shfmt", root):
            format_cmds.append(
                ("format-shfmt-edited", [f"shfmt -w {files}"], True, 20, False)
            )
        if have_command("shellcheck", root):
            lint_cmds.append(
                ("lint-shellcheck-edited", [f"shellcheck {files}"], True, 24, False)
            )

    return format_cmds, lint_cmds, type_cmds


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Format, lint, and typecheck edited files with capped AI output."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Specific files to check. Defaults to files recorded by agent edit hooks.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repo_root()
    os.chdir(root)
    files = existing_project_files(
        root, args.files if args.files else recorded_edited_files(root)
    )
    groups = split_by_ext(files)
    relevant = sorted(
        set(
            groups["biome"]
            + groups["html"]
            + groups["markdown"]
            + groups["php"]
            + groups["shell"]
        )
    )

    print(f"[edited-check] root: {root}")
    if not relevant:
        print(
            "[edited-check] no edited JS/CSS/JSON/HTML/Markdown/PHP/shell files to check"
        )
        return 0

    print(f"[edited-check] files: {len(relevant)}")
    for file in relevant[:30]:
        print(f"  - {file}")
    if len(relevant) > 30:
        print(f"  ... {len(relevant) - 30} more files hidden from AI output")
    sys.stdout.flush()

    format_cmds, lint_cmds, type_cmds = build_commands(root, groups)
    failed = 0
    failed |= run_phase(root, "format edited files", format_cmds)
    failed |= run_phase(root, "lint edited files", lint_cmds)
    failed |= run_phase(root, "typecheck edited files", type_cmds)

    if failed:
        print(
            "[edited-check] errors found after formatting/linting/typechecking edited files"
        )
        return 1
    print("[edited-check] edited-file checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
