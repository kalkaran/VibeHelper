"""Installer transcript migrations preserve history without weakening doc checks."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTIVE = b"<!-- markdownlint-disable-file -->"
HISTORY = (
    "# Transcript\n\n## 2026-09-29\n\n"
    + "verbatim message " * 14
    + "\n\n## 2026-09-29\n\n# Pasted title\n\nPasted heading\n===\n\nVerbatim trailing spaces   \nAnother entry without a final newline"
).encode()


def test_session_logs(tmp_path):
    for helper in ("CodexHelper", "ClaudeHelper"):
        for platform in ("MacOS", "WSL"):
            repo = tmp_path / helper / platform
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            skill = repo / ".agents/skills/unlazy/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("# Unlazy\n")
            bin_dir = repo / "test-bin"
            bin_dir.mkdir()
            npx = bin_dir / "npx"
            npx.write_text("#!/bin/sh\nexit 0\n")
            npx.chmod(0o755)
            command = [
                "bash",
                str(ROOT / helper / platform / "part1.sh"),
                "--repo-only",
                "--yes",
                "--no-humanizer",
            ]
            command += (
                ["--no-rtk-hook", "--no-crg-build", "--no-apply-codex-config"]
                if helper == "CodexHelper"
                else [
                    "--no-rtk",
                    "--no-graphify",
                    "--no-ponytail",
                    "--no-crg",
                    "--no-context7",
                    "--no-chrome-mcp",
                ]
            )

            def install(*options, command=command, repo=repo, bin_dir=bin_dir):
                result = subprocess.run(
                    command + list(options),
                    cwd=repo,
                    env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert result.returncode == 0, result.stdout + result.stderr

            logs = [
                repo / "notes" / name for name in ("queries.md", "conversation-log.md")
            ]
            markdown_config = repo / ".markdownlint.json"
            biome = repo / "biome.json"
            biome_original = '{"files": {"includes": ["ordinary.md"]}, "formatter": {"indentWidth": 4}}\n'
            biome.write_text(biome_original)
            install("--dry-run")
            assert all(not p.exists() for p in logs)
            assert not markdown_config.exists()
            assert biome.read_text() == biome_original
            install()
            assert all(p.read_bytes().count(DIRECTIVE) == 1 for p in logs)
            assert json.loads(markdown_config.read_text()) == {"MD013": False}
            assert json.loads(biome.read_text()) == {
                "files": {"includes": ["ordinary.md", ".markdownlint.json"]},
                "formatter": {"indentWidth": 4},
            }
            assert len(list(repo.glob("biome.json.bak.*"))) == 1
            originals = [
                HISTORY + b"\n\n```text\nunclosed fence",
                HISTORY + b"\n\n<!-- unclosed comment",
            ]
            for p, original in zip(logs, originals, strict=True):
                p.write_bytes(original)
            install("--force", "--dry-run")
            assert [p.read_bytes() for p in logs] == originals
            install()
            expected = [DIRECTIVE + b"\n\n" + original for original in originals]
            assert [p.read_bytes() for p in logs] == expected
            install()
            install("--force")
            assert [p.read_bytes() for p in logs] == expected
            assert len(list(repo.glob("biome.json.bak.*"))) == 1

            # Exercise the installed gate using the real local Markdown linter.
            (repo / "node_modules").symlink_to(
                ROOT / "node_modules", target_is_directory=True
            )
            provider = "codex" if helper == "CodexHelper" else "claude"
            makefile = f"vibe_scripts/{provider}/Makefile"
            (repo / makefile).write_text(
                f"edited-ai:\n\t@python3 vibe_scripts/{provider}/agent-check-edited.py\n"
            )

            def gate(files, repo=repo, provider=provider):
                return subprocess.run(
                    [
                        str(repo / "vibe_scripts" / provider / "agent-verify.sh"),
                        "edited",
                        *files,
                    ],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            lint_copies = []
            for index, log in enumerate(logs):
                copy = repo / f"session-log-{index}.md"
                copy.write_bytes(log.read_bytes())
                lint_copies.append(copy.name)
            result = gate(lint_copies)
            assert result.returncode == 0, result.stdout + result.stderr
            assert [p.read_bytes() for p in logs] == expected
            boundary = ("word " * 40).rstrip() + "."
            assert len(boundary) == 200
            (repo / "ordinary.md").write_text("# Markdown\n\n" + boundary + "\n")
            result = gate(["ordinary.md"])
            assert result.returncode == 0, result.stdout + result.stderr
            over_limit = boundary[:-1] + " x"
            assert len(over_limit) == 201
            (repo / "ordinary.md").write_text("# Markdown\n\n" + over_limit + "\n")
            result = gate(["ordinary.md"])
            assert result.returncode == 0, result.stdout + result.stderr
            (repo / "ordinary.md").write_bytes(HISTORY + b"\n")
            result = gate(["ordinary.md"])
            assert result.returncode != 0
            assert "MD013" not in result.stdout and "MD024" in result.stdout
            assert "MD025" in result.stdout and "MD003" in result.stdout

            custom = '{"MD013": {"line_length": 120}, "MD024": false}\n'
            markdown_config.write_text(custom)
            install("--force")
            assert markdown_config.read_text() == custom
            markdown_config.unlink()
            alternate = repo / ".markdownlint.yaml"
            alternate.write_text("MD013:\n  line_length: 120\n")
            install("--force")
            assert not markdown_config.exists()
            assert alternate.read_text() == "MD013:\n  line_length: 120\n"
