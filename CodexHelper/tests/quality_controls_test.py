"""Focused checks for generated quality controls and output reduction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def install_platform(platform: str, destination: Path) -> None:
    destination.mkdir()
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    unlazy = destination / ".agents/skills/unlazy/SKILL.md"
    unlazy.parent.mkdir(parents=True)
    unlazy.write_text("# Unlazy\n", encoding="utf-8")
    home = destination.parent / f"{platform.lower()}-home"
    home.mkdir()
    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "CodexHelper" / platform / "part1.sh"),
            "--repo-only",
            "--yes",
            "--no-humanizer",
            "--no-rtk-hook",
            "--no-crg-build",
            "--no-apply-codex-config",
        ],
        cwd=destination,
        env=os.environ | {"HOME": str(home)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout


def hook_reason(hook: Path, command: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_input": {"command": command}}),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    if not completed.stdout.strip():
        return ""
    payload = json.loads(completed.stdout)
    return payload.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def test_generated_command_policy(repositories: dict[str, Path]) -> None:
    blocked = {
        "vendor/bin/phpstan analyse src": "phpstan",
        "tools/phpstan.phar analyse src": "phpstan",
        "php vendor/bin/phpstan analyse --level=0 src": "PHPStan",
        "php tools/phpstan.phar analyse src": "PHPStan",
        "php -d memory_limit=-1 tools/phpstan.phar analyse src": "PHPStan",
        "composer exec phpstan -- analyse -c weak.neon": "phpstan",
        "composer --no-interaction -d . exec phpstan -- analyse -l 0": "phpstan",
        "find src -name '*.php' -exec vendor/bin/phpstan analyse {} +": "phpstan",
        "find src -name '*.php' -print0 | xargs -0 vendor/bin/phpstan analyse": "phpstan",
        "env APP_ENV=test phpstan analyse": "phpstan",
        "bash -c 'phpstan analyse src'": "phpstan",
        "bash -lc 'phpstan analyse src'": "phpstan",
        "sh -xc 'ruff check .'": "ruff",
        "rtk phpstan analyse src": "phpstan",
        "uv run ruff check .": "ruff",
        "npx biome check .": "biome",
        "npm run lint": "npm",
        "pnpm exec phpstan analyse src": "phpstan",
        "go test ./...": "Go",
        "staticcheck ./...": "staticcheck",
        "cargo clippy": "Cargo",
        "dotnet format": "dotnet",
        "make lint": "make lint",
        "make -j lint": "make lint",
        "gmake lint": "make lint",
        "make format-ai": "format-ai",
        "make -f alternate.mk lint-ai": "overrides",
        "make -Cother lint-ai": "overrides",
        "make -rfake.mk lint-ai": "overrides",
        "make MODE=quiet edited-ai": "overrides",
        "MODE=quiet make edited-ai": "wrappers",
        "env PHPSTAN_LEVEL=0 make typecheck-ai": "wrappers",
        "MAKEFLAGS=-fweak.mk ./vibe_scripts/codex/agent-verify.sh typecheck": "wrappers",
        "timeout 30 phpstan analyse src": "phpstan",
        "python3 vibe_scripts/codex/ai-quality-wrapper.py --label fake -- phpstan analyse": "ai-quality-wrapper.py",
    }
    allowed = (
        "make -f vibe_scripts/codex/Makefile edited-ai",
        "make -f vibe_scripts/codex/Makefile -j 4 lint-ai",
        "gmake -f vibe_scripts/codex/Makefile lint-ai typecheck-ai test-ai security-ai verify-ai",
        "./vibe_scripts/codex/agent-verify.sh verify",
        "python3 vibe_scripts/codex/agent-check-edited.py src/app.py",
        "rg 'phpstan|phpcs|make lint' docs",
        "grep -R 'vendor/bin/phpstan analyse' README.md",
        "git diff -- phpstan.neon",
        "make build",
        "rtk pipe --filter phpstan",
    )
    for platform, repository in repositories.items():
        hook = repository / ".codex/hooks/pre_tool_use_policy.py"
        assert hook.is_file()
        assert not (repository / "vibe_scripts/codex/quality_policy.py").exists()
        for command, expected in blocked.items():
            reason = hook_reason(hook, command)
            assert reason and expected.lower() in reason.lower(), (
                platform,
                command,
                reason,
            )
        for command in allowed:
            assert not hook_reason(hook, command), (platform, command)


def run_wrapper(
    wrapper: Path,
    logs: Path,
    adapter: str,
    command: list[str],
    env: dict[str, str],
    *,
    label: str = "fixture",
    max_lines: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(wrapper),
            "--label",
            label,
            "--adapter",
            adapter,
            "--log-dir",
            str(logs),
            "--max-lines",
            str(max_lines),
            "--",
            *command,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        check=False,
    )


def test_rtk_adapters_and_fallbacks(repository: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        bin_dir = temp / "bin"
        logs = temp / "logs"
        bin_dir.mkdir()
        counter = temp / "count"
        filtered_args = temp / "filtered-args"
        executable(
            bin_dir / "fake-quality-tool",
            "#!/bin/sh\nprintf x >>\"$COUNT_FILE\"\nprintf 'raw line 1\\nraw line 2\\n'\nexit 7\n",
        )
        executable(
            bin_dir / "rtk",
            '#!/bin/sh\nprintf \'%s\\n\' "$*" >>"$FILTERED_ARGS"\ncat >/dev/null\nprintf \'filtered %s\\n\' "$3"\n',
        )
        env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "COUNT_FILE": str(counter),
            "FILTERED_ARGS": str(filtered_args),
        }
        wrapper = repository / "vibe_scripts/codex/ai-quality-wrapper.py"
        adapters = {
            "rtk:ruff-check": "ruff-check",
            "rtk:ruff-format": "ruff-format",
            "rtk:mypy": "mypy",
            "rtk:pytest": "pytest",
            "rtk:go-test": "go-test",
            "rtk:cargo-test": "cargo-test",
        }
        for adapter in adapters:
            completed = run_wrapper(
                wrapper,
                logs,
                adapter,
                ["fake-quality-tool"],
                env,
                label=adapter.replace(":", "-"),
            )
            assert completed.returncode == 7, completed.stdout
            # Failed checks retain original diagnostics even when RTK drops them.
            assert "raw line 1" in completed.stdout
            assert "raw line 2" in completed.stdout
        assert counter.read_text(encoding="utf-8") == "x" * len(adapters)
        assert filtered_args.read_text(encoding="utf-8").splitlines() == [
            f"pipe --filter {filter_name}" for filter_name in adapters.values()
        ]
        log_files = list(logs.glob("*.log"))
        assert len(log_files) == len(adapters)
        for log_file in log_files:
            log_text = log_file.read_text(encoding="utf-8")
            assert "exit_code=7" in log_text
            assert "raw line 1\nraw line 2\n" in log_text

        executable(bin_dir / "rtk", "#!/bin/sh\ncat >/dev/null\nexit 1\n")
        completed = run_wrapper(wrapper, logs, "rtk:mypy", ["fake-quality-tool"], env)
        assert completed.returncode == 7
        assert "rtk exited 1" in completed.stdout
        assert "raw line 1" in completed.stdout

        completed = run_wrapper(
            wrapper, logs, "rtk:not-real", ["fake-quality-tool"], env
        )
        assert completed.returncode == 7
        assert "unknown adapter 'rtk:not-real'" in completed.stdout
        assert "raw line 1" in completed.stdout

        executable(bin_dir / "rtk", "#!/bin/sh\ncat >/dev/null\n")
        completed = run_wrapper(wrapper, logs, "rtk:mypy", ["fake-quality-tool"], env)
        assert completed.returncode == 7
        assert "rtk returned no summary" in completed.stdout

        executable(bin_dir / "rtk", "#!/bin/sh\nsleep 6\n")
        completed = run_wrapper(wrapper, logs, "rtk:mypy", ["fake-quality-tool"], env)
        assert completed.returncode == 7
        assert "rtk timed out after 5 seconds" in completed.stdout

        without_rtk = env | {"PATH": "/usr/bin:/bin"}
        completed = run_wrapper(
            wrapper,
            logs,
            "rtk:mypy",
            [str(bin_dir / "fake-quality-tool")],
            without_rtk,
        )
        assert completed.returncode == 7
        assert "rtk is not installed" in completed.stdout

        executable(
            bin_dir / "rtk",
            "#!/bin/sh\ncat >/dev/null\nprintf 'Go test: No tests found\\n'\n",
        )
        completed = run_wrapper(
            wrapper, logs, "rtk:go-test", ["fake-quality-tool"], env
        )
        assert completed.returncode == 7
        assert "rtk reported no tests for a failed Go test run" in completed.stdout
        assert "raw line 1" in completed.stdout


def test_generic_fallback(repository: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        logs = temp / "logs"
        output_file = temp / "output"
        tool = temp / "fake-tool"
        executable(tool, '#!/bin/sh\ncat "$OUTPUT_FILE"\nexit 9\n')
        raw_lines = [
            "early error " + "x" * 788,
            *[f"line {index}" for index in range(1, 10)],
        ]
        raw_lines[5] = "fatal compiler exploded in the omitted middle"
        raw = "\n".join(raw_lines) + "\n"
        output_file.write_text(raw, encoding="utf-8")
        completed = run_wrapper(
            repository / "vibe_scripts/codex/ai-quality-wrapper.py",
            logs,
            "generic",
            [str(tool)],
            os.environ | {"OUTPUT_FILE": str(output_file)},
            max_lines=4,
        )
        assert completed.returncode == 9, completed.stdout
        assert "300 chars omitted" in completed.stdout
        assert "7 output lines omitted" in completed.stdout
        assert "fatal compiler exploded in the omitted middle" in completed.stdout
        assert len(completed.stdout.splitlines()) <= 7
        log_files = list(logs.glob("*.log"))
        assert len(log_files) == 1
        assert raw in log_files[0].read_text(encoding="utf-8")


def test_native_adapters(repository: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        logs = temp / "logs"
        output_file = temp / "output"
        counter = temp / "count"
        tool = temp / "fake-tool"
        executable(
            tool,
            '#!/bin/sh\nprintf x >>"$COUNT_FILE"\ncat "$OUTPUT_FILE"\nexit "$EXIT_CODE"\n',
        )
        env = os.environ | {
            "COUNT_FILE": str(counter),
            "OUTPUT_FILE": str(output_file),
            "EXIT_CODE": "2",
        }
        wrapper = repository / "vibe_scripts/codex/ai-quality-wrapper.py"

        phpcs = {
            "totals": {"errors": 5, "warnings": 0, "fixable": 3},
            "files": {
                "src/\nunsafe.php": {
                    "messages": [
                        {
                            "message": f"Problem {index}\ncontinued",
                            "source": "Rule\nCode",
                            "severity": 5,
                            "fixable": index < 3,
                            "type": "ERROR",
                            "line": index,
                            "column": 2,
                        }
                        for index in range(1, 6)
                    ]
                }
            },
        }
        output_file.write_text(json.dumps(phpcs), encoding="utf-8")
        completed = run_wrapper(
            wrapper,
            logs,
            "native:phpcs-json",
            [str(tool)],
            env,
            max_lines=4,
        )
        assert completed.returncode == 2, completed.stdout
        assert "PHPCS: 5 errors, 0 warnings, 3 fixable" in completed.stdout
        assert (
            "src/ unsafe.php:1:2 [error Rule Code severity=5 fixable]"
            in completed.stdout
        )
        assert "3 diagnostics omitted" in completed.stdout

        phpstan = {
            "totals": {"errors": 1, "file_errors": 2},
            "files": {
                "src/App.php": {
                    "errors": 2,
                    "messages": [
                        {
                            "message": "Method returns the wrong type",
                            "line": 12,
                            "ignorable": True,
                            "identifier": "return.type",
                        },
                        {
                            "message": "Unknown property",
                            "line": 20,
                            "ignorable": False,
                            "identifier": "property.notFound",
                        },
                    ],
                }
            },
            "errors": ["Internal analysis error"],
        }
        output_file.write_text(json.dumps(phpstan), encoding="utf-8")
        completed = run_wrapper(
            wrapper,
            logs,
            "native:phpstan-json",
            [str(tool)],
            env,
        )
        assert completed.returncode == 2, completed.stdout
        assert (
            "PHPStan: 1 global errors, 2 file errors across 1 files" in completed.stdout
        )
        assert "<phpstan> [error global] Internal analysis error" in completed.stdout
        assert (
            "src/App.php:12 [error return.type] Method returns the wrong type"
            in completed.stdout
        )
        assert (
            "src/App.php:20 [error property.notFound] Unknown property"
            in completed.stdout
        )

        mixed = {
            "comments": [
                {
                    "file": "src/app.sh",
                    "line": 9,
                    "column": 4,
                    "level": "warning",
                    "code": 2086,
                    "message": "finding",
                },
                {
                    "file": "broken.sh",
                    "line": 3,
                    "column": 1,
                    "level": "error",
                    "code": 1009,
                    "message": "parser failed",
                },
            ]
        }
        output_file.write_text(
            "warning one\nwarning two\n"
            + json.dumps(mixed)
            + "\nwarning three\nwarning four\n",
            encoding="utf-8",
        )
        completed = run_wrapper(
            wrapper,
            logs,
            "native:shellcheck-json1",
            [str(tool)],
            env,
            max_lines=6,
        )
        assert completed.returncode == 2, completed.stdout
        assert "ShellCheck: 2 issues" in completed.stdout
        assert completed.stdout.index("broken.sh:3") < completed.stdout.index(
            "src/app.sh:9:4"
        )
        assert "2 non-JSON output lines omitted" in completed.stdout

        shellcheck_documents = (
            {
                "comments": [
                    {
                        "file": f"warning-{index}.sh",
                        "line": index,
                        "column": 3,
                        "level": "warning",
                        "code": 2086,
                        "message": "quote it",
                    }
                    for index in range(1, 5)
                ]
            },
            [
                {
                    "file": "error.sh",
                    "line": 9,
                    "column": 1,
                    "level": "error",
                    "code": 1009,
                    "message": "bad syntax",
                }
            ],
        )
        output_file.write_text(
            "\n".join(json.dumps(document) for document in shellcheck_documents),
            encoding="utf-8",
        )
        completed = run_wrapper(
            wrapper,
            logs,
            "native:shellcheck-json1",
            [str(tool)],
            env,
            max_lines=4,
        )
        assert "ShellCheck: 5 issues (1 error, 4 warning)" in completed.stdout
        assert "error.sh:9:1 [error SC1009]" in completed.stdout
        assert "warning-1.sh:1:3 [warning SC2086]" in completed.stdout
        assert completed.stdout.index("error.sh:9:1") < completed.stdout.index(
            "warning-1.sh:1:3"
        )
        assert "3 diagnostics omitted" in completed.stdout

        pyright = {
            "generalDiagnostics": [
                {
                    "file": "src/app.py",
                    "severity": "error",
                    "message": "wrong type",
                    "rule": "reportAssignmentType",
                    "range": {"start": {"line": 0, "character": 5}},
                }
            ],
            "summary": {
                "filesAnalyzed": 1,
                "errorCount": 1,
                "warningCount": 0,
                "informationCount": 0,
            },
            "fatalErrorOccurred": True,
            "configParseErrorOccurred": True,
        }
        output_file.write_text(json.dumps(pyright), encoding="utf-8")
        completed = run_wrapper(wrapper, logs, "native:pyright-json", [str(tool)], env)
        assert "2 fatal/config errors" in completed.stdout
        assert "src/app.py:1:6" in completed.stdout
        assert "fatalErrorOccurred" in completed.stdout
        assert "configParseErrorOccurred" in completed.stdout

        output_file.write_text('{"totals":', encoding="utf-8")
        completed = run_wrapper(wrapper, logs, "native:phpcs-json", [str(tool)], env)
        assert "no complete JSON document was found" in completed.stdout
        assert '{"totals":' in completed.stdout

        output_file.write_text(
            json.dumps(
                {"totals": {"errors": 0, "warnings": 0, "fixable": 0}, "files": {}}
            ),
            encoding="utf-8",
        )
        completed = run_wrapper(wrapper, logs, "native:phpcs-json", [str(tool)], env)
        assert "failed without any parsed diagnostics" in completed.stdout
        assert counter.read_text(encoding="utf-8") == "x" * 7


def test_generated_make_adapter_wiring() -> None:
    expected_common = (
        "--adapter rtk:ruff-check",
        "--adapter rtk:pytest",
        "--adapter rtk:go-test",
        "--adapter rtk:cargo-test",
        "--adapter native:phpcs-json",
        "--adapter native:phpstan-json",
        "--adapter native:shellcheck-json1",
        "--error-format=json",
        "--report=json",
        "--max-diagnostics=none",
    )
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        bin_dir = temp / "bin"
        bin_dir.mkdir()
        for tool_name in (
            "cargo",
            "dotnet",
            "go",
            "php",
            "phpcbf",
            "phpcs",
            "phpstan",
            "pytest",
            "ruff",
            "semgrep",
            "shellcheck",
            "shfmt",
            "staticcheck",
        ):
            executable(bin_dir / tool_name, "#!/bin/sh\nexit 0\n")

        for platform, typechecker in (("MacOS", "mypy"), ("WSL", "pyright")):
            repository = temp / platform.lower()
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            for name, content in {
                "app.py": "print('x')\n",
                "test_app.py": "def test_x(): assert True\n",
                "app.php": "<?php echo 'x';\n",
                "app.sh": "#!/bin/sh\ntrue\n",
                "app.go": "package main\n",
                "go.mod": "module example.invalid/test\n",
                "app.rs": "fn main() {}\n",
                "Cargo.toml": "[package]\nname='test'\nversion='0.1.0'\n",
                "app.js": "const x = 1;\n",
            }.items():
                (repository / name).write_text(content, encoding="utf-8")
            local_bin = repository / "node_modules/.bin"
            local_bin.mkdir(parents=True)
            executable(local_bin / "biome", "#!/bin/sh\nexit 0\n")
            executable(bin_dir / typechecker, "#!/bin/sh\nexit 0\n")
            other_typechecker = bin_dir / (
                "pyright" if typechecker == "mypy" else "mypy"
            )
            other_typechecker.unlink(missing_ok=True)
            completed = subprocess.run(
                [
                    "bash",
                    str(ROOT / "CodexHelper" / platform / "part2.sh"),
                    "--repo",
                    str(repository),
                    "--force",
                    "--yes",
                    "--no-install",
                    "--wire",
                    "--no-fix",
                ],
                env=os.environ
                | {
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "CODEXHELPER_UNIFIED_INSTALL": "1",
                },
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            assert completed.returncode == 0, completed.stdout
            makefile = (repository / "vibe_scripts/codex/Makefile").read_text(
                encoding="utf-8"
            )
            unescaped_makefile = makefile.replace("\\", "")
            wrapper = (
                repository / "vibe_scripts/codex/ai-quality-wrapper.py"
            ).read_text(encoding="utf-8")
            for expected in expected_common:
                assert expected in makefile, (platform, expected, makefile)
            assert '"native:phpstan-json": parse_phpstan' in wrapper
            assert '"rtk:phpstan"' not in wrapper
            assert "-f json1" in unescaped_makefile
            assert "semgrep" not in makefile.lower()
            assert "go test -json ./..." in unescaped_makefile
            expected_type = (
                "--adapter rtk:mypy"
                if typechecker == "mypy"
                else "--adapter native:pyright-json"
            )
            assert expected_type in makefile
            if typechecker == "pyright":
                assert "--outputjson" in makefile
            assert "format-ai:" not in makefile
            assert "|| true" not in makefile

            raw_lint = makefile.split("lint:\n", 1)[1].split("\n\n", 1)[0]
            raw_type = makefile.split("typecheck:\n", 1)[1].split("\n\n", 1)[0]
            raw_test = makefile.split("test:\n", 1)[1].split("\n\n", 1)[0]
            assert "security:\n" not in makefile
            assert "--report=json" not in raw_lint
            assert "-f json1" not in raw_lint
            assert "--error-format=json" not in raw_type
            assert "--outputjson" not in raw_type
            assert "go test -json" not in raw_test


def test_empty_edit_set_fails(repository: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(repository / "vibe_scripts/codex/agent-check-edited.py"),
            "missing-source.py",
        ],
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert completed.returncode == 2, completed.stdout
    assert "not_checked" in completed.stdout
    assert "absent or excluded" in completed.stdout


def test_php_syntax_failure_is_not_masked(repository: Path) -> None:
    bad = repository / "a-bad.php"
    good = repository / "z-good.php"
    bad.write_text("<?php broken\n", encoding="utf-8")
    good.write_text("<?php echo 'ok';\n", encoding="utf-8")
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        bin_dir = temp / "bin"
        calls = temp / "calls"
        bin_dir.mkdir()
        executable(
            bin_dir / "php",
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$2" >>"$CALLS"\n'
            'case "$2" in\n'
            "*a-bad.php) printf 'Parse error in %s\\n' \"$2\"; exit 255 ;;\n"
            "*) printf 'No syntax errors detected in %s\\n' \"$2\" ;;\n"
            "esac\n",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(repository / "vibe_scripts/codex/agent-check-edited.py"),
                bad.name,
                good.name,
            ],
            cwd=repository,
            env=os.environ
            | {
                "CALLS": str(calls),
                "PATH": f"{bin_dir}:/usr/bin:/bin",
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert completed.returncode == 1, completed.stdout
        assert "lint-php-syntax-edited: findings" in completed.stdout
        assert "Parse error in a-bad.php" in completed.stdout
        assert calls.read_text(encoding="utf-8").splitlines() == [
            "a-bad.php",
            "z-good.php",
        ]


def test_phpcbf_failure_is_not_suppressed(repository: Path) -> None:
    php_file = repository / "app.php"
    php_file.write_text("<?php echo 'x';\n", encoding="utf-8")
    phpcbf = repository / "vendor/bin/phpcbf"
    phpcbf.parent.mkdir(parents=True)
    executable(
        phpcbf,
        "#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'PHP_CodeSniffer version 3.13.4'; exit 0; fi\nprintf 'formatter failed\\n'\nexit 3\n",
    )
    binaries = repository / "fake-php-bin"
    binaries.mkdir(exist_ok=True)
    executable(
        binaries / "php", "#!/bin/sh\necho 'No syntax errors detected in app.php'\n"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(repository / "vibe_scripts/codex/agent-check-edited.py"),
            "app.php",
        ],
        cwd=repository,
        env=os.environ | {"PATH": f"{binaries}:/usr/bin:/bin"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout
    assert "format-phpcbf-edited: tool_failure" in completed.stdout
    assert "formatter failed" in completed.stdout


def test_generated_script_parity(repositories: dict[str, Path]) -> None:
    for relative in (
        "vibe_scripts/codex/ai-quality-wrapper.py",
        "vibe_scripts/codex/agent-check-edited.py",
    ):
        assert (repositories["MacOS"] / relative).read_text(encoding="utf-8") == (
            repositories["WSL"] / relative
        ).read_text(encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        repositories = {}
        for platform in ("MacOS", "WSL"):
            repository = temp / platform.lower()
            install_platform(platform, repository)
            repositories[platform] = repository
        test_generated_script_parity(repositories)
        test_generated_command_policy(repositories)
        test_rtk_adapters_and_fallbacks(repositories["MacOS"])
        test_generic_fallback(repositories["MacOS"])
        test_native_adapters(repositories["MacOS"])
        test_empty_edit_set_fails(repositories["MacOS"])
        test_php_syntax_failure_is_not_masked(repositories["MacOS"])
        test_phpcbf_failure_is_not_suppressed(repositories["MacOS"])
    test_generated_make_adapter_wiring()
    print("Quality control tests passed.")


if __name__ == "__main__":
    main()
