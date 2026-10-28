#!/usr/bin/env python3
"""Format and lint supported edited files with capped AI output."""

from __future__ import annotations

import argparse
import configparser
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

try:
    import tomllib
except ImportError:
    tomllib = None

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
    variables = {
        "codex": ("CODEX_THREAD_ID", "CODEX_SESSION_ID"),
        "claude": ("CLAUDE_SESSION_ID",),
    }.get(provider, ())
    session = next(
        (os.environ[name] for name in variables if os.environ.get(name)), None
    )
    if not session:
        raise ValueError(
            "Current session ID is unavailable; start a trusted session or select explicit files with agent-verify.sh edited PATH."
        )
    key = provider + "-" + QUALITY.hashlib.sha256(session.encode()).hexdigest()[:24]
    state = json.loads((root / ".cache/quality-sessions" / (key + ".json")).read_text())
    if (
        not isinstance(state, dict)
        or state.get("baseline_format") != "stat-v1"
        or not isinstance(state.get("baseline"), dict)
        or not isinstance(state.get("paths"), list)
        or not all(isinstance(path, str) for path in state["paths"])
        or not all(
            isinstance(path, str) and isinstance(stamp, str)
            for path, stamp in state["baseline"].items()
        )
    ):
        raise ValueError(
            "Current session baseline is invalid; start a trusted session or select explicit files with agent-verify.sh edited PATH."
        )
    spec = importlib.util.spec_from_file_location(
        "session_quality", Path(__file__).with_name("quality-hook.py")
    )
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    # Reuse hook attribution, including shell edits; never infer ownership from Git dirtiness.
    hook.ensure_helpers()
    return sorted(
        set(state["paths"])
        | set(hook.changed_since(state["baseline"], hook.snapshot(root)))
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
    if path.startswith(("node_modules/", "vendor/", ".venv/")):
        return bool(executable(root, Path(path).name))
    return (root / path).exists()


def have_command(command: str, root: Path) -> bool:
    return executable(root, command) is not None


def local_impeccable_command(root: Path) -> list[str] | None:
    local_bin = root / "node_modules" / ".bin" / "impeccable"
    if local_bin.exists():
        return ["npx", "--no-install", "impeccable"]
    executable = shutil.which("impeccable")
    if executable:
        return [executable]
    return None


def markdownlint_command(root: Path) -> list[str] | None:
    tool = executable(root, "markdownlint-cli2")
    if tool:
        return [tool]
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
        lint_cmds.append(
            (
                "lint-gofmt-edited",
                [
                    sys.executable,
                    "-c",
                    (
                        "import subprocess,sys; "
                        "r=subprocess.run([sys.argv[1],'-l',*sys.argv[2:]],capture_output=True,text=True); "
                        "print(r.stdout,end=''); print(r.stderr,end='',file=sys.stderr); "
                        "sys.exit(r.returncode or bool(r.stdout))"
                    ),
                    executable(root, "gofmt"),
                    *groups["go"],
                ],
                False,
                20,
                "generic",
            )
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


# Package context is analysis scope, never permission to format other files.
PROJECT_MARKERS = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "composer.lock",
    "uv.lock",
    "poetry.lock",
    ".markdownlint.json",
    ".markdownlint.yaml",
    ".markdownlint.yml",
    ".markdownlint-cli2.jsonc",
    ".markdownlint-cli2.yaml",
    "phpcs.xml",
    "phpcs.xml.dist",
    ".phpcs.xml",
    ".phpcs.xml.dist",
    "phpstan.neon",
    "phpstan.neon.dist",
    "pyrightconfig.json",
    "mypy.ini",
    ".mypy.ini",
    ".isort.cfg",
    ".pylintrc",
    "pylintrc",
    "rustfmt.toml",
    ".rustfmt.toml",
    "eslint.config.ts",
    "eslint.config.cts",
    "eslint.config.mts",
    ".eslintrc.yml",
    ".eslintrc.yaml",
    ".eslintrc.cjs",
    ".prettierrc.js",
    ".prettierrc.cjs",
    ".prettierrc.yml",
    ".prettierrc.yaml",
    "prettier.config.cjs",
    "package.json",
    "pyproject.toml",
    "setup.cfg",
    "composer.json",
    "go.mod",
    "Cargo.toml",
    "ruff.toml",
    ".ruff.toml",
    "biome.json",
    "biome.jsonc",
    ".eslintrc",
    ".eslintrc.json",
    ".eslintrc.js",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    ".prettierrc",
    ".prettierrc.json",
    "prettier.config.js",
    "prettier.config.mjs",
    "tsconfig.json",
}


def package_owner(root: Path, name: str) -> Path:
    directory = (root / name).parent
    while directory != root:
        if any((directory / marker).is_file() for marker in PROJECT_MARKERS) or any(
            directory.glob("*.csproj")
        ):
            return directory
        directory = directory.parent
    return root


def executable(root: Path, name: str) -> str | None:
    for directory in [root, *root.parents]:
        for folder in ("node_modules/.bin", "vendor/bin", ".venv/bin"):
            path = directory / folder / name
            if path.is_file():
                return str(path)
    return shutil.which(name)


def configured(root: Path, names: tuple[str, ...]) -> bool:
    return any(
        (directory / name).is_file()
        for directory in [root, *root.parents]
        for name in names
    )


def inherited_file(root: Path, name: str) -> Path | None:
    return next(
        (
            directory / name
            for directory in [root, *root.parents]
            if (directory / name).is_file()
        ),
        None,
    )


def script_command(script: str) -> tuple[str, list[str]] | None:
    """Recognize only a direct native command; never execute package lifecycle scripts."""
    if re.search(r"[;&|`$<>\n]", script):
        return None
    parts = shlex.split(script)
    if parts[:2] == ["npx", "--no-install"]:
        parts = parts[2:]
    elif parts[:1] == ["npx"]:
        parts = parts[1:]
    if not parts or parts[0] not in {
        "eslint",
        "prettier",
        "black",
        "isort",
        "pylint",
        "ruff",
        "biome",
    }:
        return None
    name, options = parts[0], []
    flags = {
        "--check",
        "--write",
        "--fix",
        "--check-only",
        "--diff",
        "--quiet",
        "-q",
        "--no-cache",
    }
    valued = {
        "--max-warnings",
        "--config",
        "-c",
        "--line-length",
        "--target-version",
        "--ext",
    }
    index = 1
    while index < len(parts):
        item = parts[index]
        if item in flags:
            if item not in {"--check", "--write", "--fix", "--check-only", "--diff"}:
                options.append(item)
        elif item in valued:
            if index + 1 == len(parts) or parts[index + 1].startswith("-"):
                return None
            options.extend(parts[index : index + 2])
            index += 1
        elif item.startswith("-"):
            return None
        index += 1
    if name in {"ruff", "biome"}:
        if len(parts) < 2 or parts[1] not in {"check", "format", "lint"}:
            return None
        name += ":" + parts[1]
    return name, options


def tool_extensions(tool: str) -> set[str]:
    if tool in {"black", "isort", "pylint", "ruff"}:
        return PYTHON_EXTS
    if tool == "biome":
        return BIOME_EXTS
    if tool == "eslint":
        return {
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".ts",
            ".tsx",
            ".vue",
            ".svelte",
            ".astro",
        }
    return (
        BIOME_EXTS
        | HTML_EXTS
        | MARKDOWN_EXTS
        | {".vue", ".svelte", ".astro", ".yaml", ".yml", ".scss", ".less"}
    )


def plan_checks(root: Path, files: list[str], mode: str):
    commands, missing, coverage = [], [], {}
    owners = {}
    supported = set().union(
        BIOME_EXTS,
        HTML_EXTS,
        MARKDOWN_EXTS,
        PYTHON_EXTS,
        GO_EXTS,
        PHP_EXTS,
        SHELL_EXTS,
        {
            ".vue",
            ".svelte",
            ".astro",
            ".yaml",
            ".yml",
            ".scss",
            ".less",
            ".cs",
            ".fs",
            ".vb",
        },
    )
    for name in files:
        coverage[name] = "not applicable: no file-scoped lint/format adapter"
        if (root / name).is_file() and Path(name).suffix.lower() in supported:
            owners.setdefault(package_owner(root, name), []).append(name)
    for owner, selected in owners.items():
        current = selected
        analysis = current

        def relative(names, owner=owner):
            return ["./" + str((root / n).relative_to(owner)) for n in names]

        groups = split_by_ext(relative(analysis))
        writes = split_by_ext(relative(current))
        json_only = (
            groups["biome"]
            and all(Path(n).suffix == ".json" for n in groups["biome"])
            and not configured(owner, ("biome.json", "biome.jsonc"))
        )
        json_files = groups["biome"][:] if json_only else []
        if json_only:
            groups["biome"] = []
            writes["biome"] = []
        phases = list(build_commands(owner, groups))
        phases[0] = build_commands(owner, writes)[0] if mode == "edited" else []
        if json_files:
            phases[1].append(
                (
                    "lint-json-syntax",
                    [
                        sys.executable,
                        "-c",
                        "import json,sys; [json.load(open(p)) for p in sys.argv[1:]]",
                        *json_files,
                    ],
                    False,
                    20,
                    "generic",
                )
            )
        problems = missing_tools(owner, groups)
        if mode == "lint":
            problems = [p for p in problems if "shfmt" not in p]
            if groups["shell"] and not have_command("shellcheck", owner):
                problems.append(
                    "Required ShellCheck is unavailable for selected shell files."
                )
        if configured(
            owner, ("phpcs.xml", "phpcs.xml.dist", ".phpcs.xml", ".phpcs.xml.dist")
        ):
            if groups["php"] and not executable(owner, "phpcs"):
                problems.append(
                    "Configured PHPCS is unavailable for selected PHP files."
                )
            phases = [
                [
                    (
                        label,
                        [part.replace(" --standard=PSR12", "") for part in args],
                        shell,
                        limit,
                        adapter,
                    )
                    for label, args, shell, limit, adapter in phase
                ]
                for phase in phases
            ]
        package = {}
        manifest = inherited_file(owner, "package.json")
        web_selected = any(groups[kind] for kind in ("biome", "html", "markdown"))
        web_optional = any(
            Path(n).suffix
            in {".vue", ".svelte", ".astro", ".yaml", ".yml", ".scss", ".less"}
            for n in selected
        )
        if manifest and (web_selected or web_optional):
            try:
                package = json.loads(manifest.read_text())
            except json.JSONDecodeError:
                if web_selected:
                    raise
                package = {}
            if not isinstance(package, dict) or not isinstance(
                package.get("scripts", {}), dict
            ):
                if web_selected:
                    raise ValueError(f"Invalid package scripts: {manifest}")
                package = {}
        python = {}
        python_config = inherited_file(owner, "pyproject.toml")
        if python_config and groups["python"]:
            if tomllib is None:
                raise ValueError(
                    f"Cannot inspect {python_config}: TOML configuration checking requires Python 3.11+; verification remains incomplete."
                )
            python = tomllib.loads(python_config.read_text()).get("tool", {})
        setup = inherited_file(owner, "setup.cfg")
        if setup and groups["python"]:
            config = configparser.ConfigParser(interpolation=None)
            config.read(setup)
            for name in ("isort", "mypy"):
                if config.has_section(name):
                    python.setdefault(name, {})
        requested = []
        scripts = package.get("scripts", {})
        unresolved_format = False
        composer = inherited_file(owner, "composer.json")
        if composer and groups["php"]:
            composer_scripts = json.loads(composer.read_text()).get("scripts", {})
            for kind in ("lint", "format", "lint:fix"):
                if composer_scripts.get(kind):
                    missing.append(
                        f"{composer}: required {kind} script needs an explicit scoped native command; verification incomplete."
                    )
                    unresolved_format |= kind in {"format", "lint:fix"}
        for kind in ("lint", "format"):
            if scripts.get(kind):
                if not isinstance(scripts[kind], str):
                    if web_selected:
                        raise ValueError(f"Invalid {kind} script: {manifest}")
                    continue
                parsed = script_command(scripts[kind])
                # Opaque JS scripts cannot establish support for unrelated files.
                if parsed is None and not (groups["biome"] or groups["html"]):
                    continue
                if parsed is not None and not any(
                    Path(n).suffix.lower() in tool_extensions(parsed[0].split(":")[0])
                    for n in selected
                ):
                    continue
                if parsed is None or any(
                    scripts.get(prefix + kind) for prefix in ("pre", "post")
                ):
                    missing.append(
                        f"{manifest}: required {kind} script cannot be safely scoped. Review it and provide a direct scoped check."
                    )
                    if kind == "format":
                        phases[0] = []
                        unresolved_format = True
                else:
                    for index, option in enumerate(parsed[1][:-1]):
                        if option in {"--config", "-c"}:
                            parsed[1][index + 1] = str(
                                manifest.parent / parsed[1][index + 1]
                            )
                    requested.append((*parsed, kind))
        if (
            configured(
                owner,
                (
                    "eslint.config.js",
                    "eslint.config.mjs",
                    "eslint.config.cjs",
                    "eslint.config.ts",
                    "eslint.config.mts",
                    "eslint.config.cts",
                    ".eslintrc",
                    ".eslintrc.json",
                    ".eslintrc.cjs",
                    ".eslintrc.yaml",
                    ".eslintrc.yml",
                    ".eslintrc.js",
                ),
            )
            or "eslintConfig" in package
        ):
            requested.append(("eslint", [], "lint"))
        if (
            configured(
                owner,
                (
                    ".prettierrc",
                    ".prettierrc.json",
                    ".prettierrc.yaml",
                    ".prettierrc.cjs",
                    ".prettierrc.js",
                    ".prettierrc.yml",
                    "prettier.config.js",
                    "prettier.config.mjs",
                    "prettier.config.cjs",
                ),
            )
            or "prettier" in package
        ):
            requested.append(("prettier", [], "format"))
        for name in ("black", "isort", "pylint"):
            if name in python or configured(
                owner,
                {
                    "black": (),
                    "isort": (".isort.cfg",),
                    "pylint": (".pylintrc", "pylintrc"),
                }[name],
            ):
                requested.append((name, [], "lint" if name == "pylint" else "format"))
        seen = set()
        extra = []

        def add(name, args, label, phase=1, owner=owner, extra=extra):
            tool = executable(owner, name)
            if not tool:
                missing.append(
                    f"{owner}: required {name} is unavailable; restore the project tool and recheck."
                )
            else:
                extra.append((phase, (label, [tool, *args], False, 20, "generic")))

        for tool, options, kind in requested:
            base = tool.split(":")[0]
            if (base, kind) in seen:
                continue
            seen.add((base, kind))
            suffixes = tool_extensions(base)
            targets = relative(
                [n for n in analysis if Path(n).suffix.lower() in suffixes]
            )
            writing = relative([n for n in current if Path(n).suffix in suffixes])
            if not targets:
                continue
            if base in {"black", "isort"}:
                phases[0] = [c for c in phases[0] if c[0] != "format-ruff-edited"]
            if base == "eslint":
                remaining_biome = [
                    n for n in groups["biome"] if Path(n).suffix not in suffixes
                ]
                phases[1] = [c for c in phases[1] if "biome" not in c[0]]
                if remaining_biome and have_path(owner, "node_modules/.bin/biome"):
                    phases[1].append(
                        (
                            "lint-biome-edited",
                            [
                                "npx biome check --colors=off --max-diagnostics=none "
                                + quote_files(remaining_biome)
                            ],
                            True,
                            24,
                            "generic",
                        )
                    )
                phases[0] = [
                    (
                        label,
                        [arg.replace("biome check", "biome format") for arg in args],
                        shell,
                        limit,
                        adapter,
                    )
                    for label, args, shell, limit, adapter in phases[0]
                ]
                if not remaining_biome and (
                    mode == "lint" or any(t[0] == "prettier" for t in requested)
                ):
                    problems = [p for p in problems if "Biome" not in p]
            if base == "prettier":
                phases[0] = [
                    c
                    for c in phases[0]
                    if "markdownlint" not in c[0] and "biome" not in c[0]
                ]
                phases[1] = [
                    (
                        label,
                        [arg.replace("biome check", "biome lint") for arg in args],
                        shell,
                        limit,
                        adapter,
                    )
                    for label, args, shell, limit, adapter in phases[1]
                ]
            if base == "pylint" and not (
                "ruff" in python or configured(owner, ("ruff.toml", ".ruff.toml"))
            ):
                phases[1] = [c for c in phases[1] if "ruff" not in c[0]]
                if mode == "lint" or any(t[0] == "black" for t in requested):
                    problems = [p for p in problems if "Ruff" not in p]
            checks = {
                "prettier": ["--check"],
                "black": ["--check"],
                "isort": ["--check-only"],
                "eslint": [],
                "pylint": [],
                "ruff": ["check"],
                "biome": ["check"],
            }
            check_args = checks[base]
            if tool == "ruff:format":
                check_args = ["format", "--check"]
            if tool == "biome:format":
                check_args = ["format"]
            if base in {"ruff", "biome"}:
                replaced = 0 if kind == "format" or tool.endswith(":format") else 1
                phases[replaced] = [c for c in phases[replaced] if base not in c[0]]
            add(base, [*check_args, *options, *targets], f"lint-{base}-configured")
            if mode == "edited" and writing and kind == "format":
                write_args = {
                    "prettier": ["--write"],
                    "black": [],
                    "isort": [],
                    "eslint": ["--fix"],
                    "ruff": ["format"] if tool == "ruff:format" else ["check", "--fix"],
                    "biome": ["format", "--write"]
                    if tool == "biome:format"
                    else ["check", "--write"],
                    "pylint": None,
                }[base]
                if write_args is None:
                    missing.append(f"{owner}: {base} is not a formatter.")
                else:
                    add(
                        base,
                        [*write_args, *options, *writing],
                        f"format-{base}-configured",
                        0,
                    )
        dotnet = [n for n in analysis if Path(n).suffix in {".cs", ".fs", ".vb"}]
        if dotnet:
            add(
                "dotnet",
                ["format", "--verify-no-changes", "--include", *relative(dotnet)],
                "lint-dotnet-format",
            )
            writing = relative([n for n in current if n in dotnet])
            if mode == "edited" and writing:
                add(
                    "dotnet",
                    ["format", "--include", *writing],
                    "format-dotnet-scoped",
                    0,
                )
        for phase, cmd in extra:
            phases[phase].append(cmd)
        if unresolved_format:
            phases[0] = []
        missing.extend(problems)
        known = set().union(
            BIOME_EXTS,
            HTML_EXTS,
            MARKDOWN_EXTS,
            PYTHON_EXTS,
            GO_EXTS,
            PHP_EXTS,
            SHELL_EXTS,
            {".cs", ".fs", ".vb"},
        )
        for tool, _, _ in requested:
            if tool == "prettier":
                known |= {
                    ".vue",
                    ".svelte",
                    ".astro",
                    ".yaml",
                    ".yml",
                    ".scss",
                    ".less",
                }
            elif tool == "eslint":
                known |= {".vue", ".svelte", ".astro"}
        for name in selected:
            if Path(name).suffix.lower() in known:
                coverage[name] = "required checks planned"
        for phase in phases:
            for label, args, shell, limit, adapter in phase:
                if shell:
                    for name in ("biome", "htmlhint", "phpcs", "phpcbf", "phpstan"):
                        tool = executable(owner, name)
                        if tool:
                            args = [
                                arg.replace("npx " + name, shlex.quote(tool)).replace(
                                    "vendor/bin/" + name, shlex.quote(tool)
                                )
                                for arg in args
                            ]
                    for name in ("ruff", "shellcheck", "shfmt", "gofmt", "php"):
                        tool = executable(owner, name)
                        if tool:
                            args = [
                                shlex.quote(tool) + arg[len(name) :]
                                if arg.startswith(name + " ")
                                else arg
                                for arg in args
                            ]
                commands.append((owner, (label, args, shell, limit, adapter)))
    return commands, missing, coverage


def check_files(
    root: Path, selected: list[str], *, timeout: float = 100, mode: str = "edited"
) -> dict:
    started = time.monotonic()
    try:
        files = project_paths(root, selected)
    except OSError as exc:
        return QUALITY.result("tool_failure", "file selection", [str(exc)])
    if not files:
        return QUALITY.result(
            "not_checked",
            "edited files",
            ["No applicable files selected."],
            explicitly_skipped=True,
        )
    try:
        commands, missing, coverage = plan_checks(root, files, mode)
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        KeyError,
        configparser.Error,
    ) as exc:
        return QUALITY.result("tool_failure", "check planning", [str(exc)])
    rejected = [
        name for name in selected if os.path.relpath(root / name, root) not in files
    ]
    if rejected:
        missing.append(
            "Selected paths excluded or outside project: " + ", ".join(rejected)
        )
    results = []
    for working, (label, command, shell, max_lines, adapter) in commands:
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
                [executable(working, "phpcbf") or "vendor/bin/phpcbf", "--version"],
                label="phpcbf-version",
                cwd=working,
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
            cwd=working,
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
            QUALITY.save_summary(value, root=root)
        results.append(value)
    if missing:
        results.append(QUALITY.result("not_checked", "missing required tools", missing))
    if not results:
        if coverage and all(
            item.startswith("not applicable") for item in coverage.values()
        ):
            return QUALITY.result(
                "not_checked",
                "selected assets",
                ["Not applicable: no code checks required for selected assets."],
                coverage=coverage,
                explicitly_skipped=True,
            )
        return QUALITY.result(
            "not_checked",
            "edited files",
            [
                "No checks executed for the selected files; run the project's applicable verifier."
            ],
        )
    priority = {"passed": 0, "findings": 1, "not_checked": 2, "tool_failure": 3}
    status = max((value["status"] for value in results), key=priority.get)
    summary = [
        f"Scope: {len(files)} selected paths; mode: {mode}. File-scoped lint/format only."
    ]
    for value in sorted(results, key=lambda item: -priority[item["status"]]):
        if value["status"] == "passed":
            notices = [
                line
                for line in value["summary"]
                if re.search(
                    r"UNPARSED|REVIEW NEEDED|\bwarnings?\b|\bskipped\b",
                    line,
                    re.IGNORECASE,
                )
            ]
            if notices:
                summary.extend([f"{value['label']}: passed with notices", *notices])
                if value.get("summary_log"):
                    summary.append(f"Summary log: {value['summary_log']}")
            continue
        else:
            summary.extend([f"{value['label']}: {value['status']}", *value["summary"]])
            if value.get("summary_log"):
                summary.append(f"Summary log: {value['summary_log']}")
            elif value.get("log"):
                summary.append(f"Full log: {value['log']}")
    passed = sum(value["status"] == "passed" for value in results)
    summary.insert(
        1,
        f"PASS: {passed} of {len(results)} checks; remaining checks listed below."
        if passed != len(results)
        else f"PASS: all {passed} checks.",
    )
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
        coverage=coverage,
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
    parser.add_argument("--mode", choices=("edited", "lint"), default="edited")
    parser.add_argument(
        "--all", action="store_true", help="Explicit full-project read-only lint"
    )
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
    try:
        if args.all:
            if args.files or args.mode != "lint":
                raise ValueError("--all requires lint mode without explicit paths")
            selected = all_project_files(root)
        elif args.files:
            selected = args.files
        elif "VIBEHELPER_EDITED_FILES_JSON" in os.environ:
            selected = json.loads(os.environ["VIBEHELPER_EDITED_FILES_JSON"])
            if not isinstance(selected, list) or not all(
                isinstance(path, str) for path in selected
            ):
                raise ValueError("Explicit task files must be a JSON list of paths.")
        else:
            selected = recorded_edited_files(root)
        value = check_files(root, selected, timeout=args.timeout, mode=args.mode)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        value = QUALITY.result(
            "not_checked",
            "edited files",
            [
                f"Session scope unavailable: {exc}",
                "Start a trusted session or use agent-verify.sh edited PATH with explicit task files.",
            ],
        )
    QUALITY.save_summary(value, root=root)
    # Explicit checks invalidate an older automatic failure, including tool repairs.
    marker = root / ".cache" / "quality-sessions" / "explicit-check"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(time.time_ns()), encoding="utf-8")
    print(json.dumps(value) if args.json else QUALITY.render_result(value))
    return (
        0
        if value["status"] == "passed" or value.get("explicitly_skipped")
        else 2
        if value["status"] == "not_checked"
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
