"""Installer transcript migrations preserve history without weakening doc checks."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTIVE = b"<!-- markdownlint-disable-file -->"
HISTORY = (
    "# Transcript\n\n## 2026-09-29\n\n"
    + "verbatim message " * 12
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
            install("--dry-run")
            assert all(not p.exists() for p in logs)
            install()
            assert all(p.read_bytes().count(DIRECTIVE) == 1 for p in logs)
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

            # Exercise the installed gate using the real local Markdown linter.
            (repo / "node_modules").symlink_to(
                ROOT / "node_modules", target_is_directory=True
            )
            provider = "codex" if helper == "CodexHelper" else "claude"
            makefile = f"vibe_scripts/{provider}/Makefile"
            (repo / makefile).write_text(
                f"edited-ai:\n\t@python3 vibe_scripts/{provider}/agent-check-edited.py\n"
            )
            cache = repo / f".cache/{provider}-edited-files.txt"
            cache.parent.mkdir(exist_ok=True)

            def gate(files, cache=cache, repo=repo, makefile=makefile):
                cache.write_text("\n".join(files) + "\n")
                return subprocess.run(
                    ["make", "-f", makefile, "edited-ai"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            result = gate(["notes/queries.md", "notes/conversation-log.md"])
            assert result.returncode == 0, result.stdout + result.stderr
            assert [p.read_bytes() for p in logs] == expected
            (repo / "ordinary.md").write_bytes(HISTORY + b"\n")
            result = gate(["ordinary.md"])
            assert result.returncode != 0
            assert "MD013" in result.stdout and "MD024" in result.stdout
            assert "MD025" in result.stdout and "MD003" in result.stdout
