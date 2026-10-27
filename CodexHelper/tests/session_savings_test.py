"""Checks for the generated Codex session-reduction tracker."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def install(platform: str, repository: Path, env: dict[str, str]) -> None:
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    unlazy = repository / ".agents/skills/unlazy/SKILL.md"
    unlazy.parent.mkdir(parents=True)
    unlazy.write_text("# Unlazy\n", encoding="utf-8")
    completed = run(
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
        cwd=repository,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout
    completed = run(
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
        cwd=repository,
        env=env | {"CODEXHELPER_UNIFIED_INSTALL": "1"},
    )
    assert completed.returncode == 0, completed.stdout


def rtk_document(saved: int, commands: int) -> str:
    return json.dumps(
        {
            "summary": {
                "total_commands": commands,
                "total_input": saved + 100,
                "total_output": 100,
                "total_saved": saved,
            }
        }
    )


def test_session_savings_tracker() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        bin_dir = temp / "bin"
        bin_dir.mkdir()
        rtk_stats = temp / "rtk.json"
        rtk = bin_dir / "rtk"
        rtk.write_text('#!/bin/sh\ncat "$RTK_STATS"\n', encoding="utf-8")
        rtk.chmod(0o755)
        rtk_stats.write_text(rtk_document(100, 4), encoding="utf-8")
        env = os.environ | {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "RTK_STATS": str(rtk_stats),
        }
        generated_hooks: list[str] = []

        for platform in ("MacOS", "WSL"):
            rtk_stats.write_text(rtk_document(100, 4), encoding="utf-8")
            repository = temp / platform.lower()
            platform_env = env | {"HOME": str(temp / f"{platform.lower()}-home")}
            Path(platform_env["HOME"]).mkdir()
            install(platform, repository, platform_env)

            hook = repository / ".codex/hooks/post_tool_use.py"
            config = (repository / ".codex/config.toml").read_text(encoding="utf-8")
            makefile = (repository / "vibe_scripts/codex/Makefile").read_text(
                encoding="utf-8"
            )
            generated_hooks.append(hook.read_text(encoding="utf-8"))
            hooks = tomllib.loads(config)["hooks"]
            assert any(
                entry.get("matcher") == "^(Bash|mcp__code[-_]review[-_]graph__.*)$"
                and any(
                    "post_tool_use.py" in hook["command"] for hook in entry["hooks"]
                )
                for entry in hooks["PostToolUse"]
            )
            for event in ("SessionStart", "SessionEnd"):
                assert any(
                    "post_tool_use.py" in hook["command"]
                    for entry in hooks[event]
                    for hook in entry["hooks"]
                )
            assert "savings-ai:" in makefile

            base = {
                "session_id": "session/example",
                "cwd": str(repository),
            }
            started = run(
                [sys.executable, str(hook)],
                cwd=repository,
                env=platform_env,
                payload=base | {"hook_event_name": "SessionStart", "source": "startup"},
            )
            assert started.returncode == 0 and not started.stdout

            rtk_stats.write_text(rtk_document(700, 7), encoding="utf-8")
            commit_output = """
┌─────────────────────── Token Savings ────────────────────────┐
│ Full context would be:      1,500 tokens                     │
│ Graph context used:           300 tokens                     │
│ Saved:                      1,200 tokens (~80%)               │
└──────────────────────────────────────────────────────────────┘
"""
            bash_event = base | {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_use_id": "bash-1",
                "tool_response": commit_output,
            }
            recorded = run(
                [sys.executable, str(hook)],
                cwd=repository,
                env=platform_env,
                payload=bash_event,
            )
            assert recorded.returncode == 0 and not recorded.stdout
            duplicate = run(
                [sys.executable, str(hook)],
                cwd=repository,
                env=platform_env,
                payload=bash_event,
            )
            assert duplicate.returncode == 0 and not duplicate.stdout

            graph_event = base | {
                "hook_event_name": "PostToolUse",
                "tool_name": "mcp__code_review_graph__detect_changes_tool",
                "tool_use_id": "graph-1",
                "tool_response": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"context_savings": {"saved_tokens": 800}}
                            ),
                        }
                    ]
                },
            }
            graph_recorded = run(
                [sys.executable, str(hook)],
                cwd=repository,
                env=platform_env,
                payload=graph_event,
            )
            assert graph_recorded.returncode == 0 and not graph_recorded.stdout

            report = run(
                [sys.executable, str(hook), "--report"],
                cwd=repository,
                env=platform_env,
            )
            assert report.returncode == 0, report.stdout
            assert (
                "RTK output reduction: ~600 tokens across 3 command(s)" in report.stdout
            )
            assert (
                "Code-review-graph context estimate: ~2,000 tokens across 2 call(s)"
                in report.stdout
            )
            assert "Combined total: not reported" in report.stdout

            state = json.loads(
                (
                    repository / ".cache/session-token-savings/session_example.json"
                ).read_text(encoding="utf-8")
            )
            assert state["crg_calls"] == 2
            assert state["crg_saved"] == 2_000

            ended = run(
                [sys.executable, str(hook)],
                cwd=repository,
                env=platform_env,
                payload=base | {"hook_event_name": "SessionEnd", "reason": "other"},
            )
            assert ended.returncode == 0 and not ended.stdout
            state = json.loads(
                (
                    repository / ".cache/session-token-savings/session_example.json"
                ).read_text(encoding="utf-8")
            )
            assert state["ended_at"]

            rtk_stats.write_text(rtk_document(1_400, 10), encoding="utf-8")
            frozen = run(
                [sys.executable, str(hook), "--report"],
                cwd=repository,
                env=platform_env,
            )
            assert frozen.returncode == 0, frozen.stdout
            assert (
                "RTK output reduction: ~600 tokens across 3 command(s)" in frozen.stdout
            )

            malformed = subprocess.run(
                [sys.executable, str(hook)],
                cwd=repository,
                env=platform_env,
                input="{bad json",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            assert malformed.returncode == 0 and not malformed.stdout

        assert generated_hooks[0] == generated_hooks[1]
