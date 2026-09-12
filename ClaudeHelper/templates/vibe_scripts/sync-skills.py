#!/usr/bin/env python3
# ClaudeHelper-Version: 2026-09-05-v4
"""Check or refresh managed AI skills and plugins."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "skills-lock.json"


DEFAULT_LOCK: dict[str, Any] = {
    "version": 2,
    "skills": {
        "humanizer": {
            "source": "blader/humanizer",
            "sourceType": "github",
            "skillPath": "SKILL.md",
            "updatePolicy": "manual",
            "providers": {
                "codex": {
                    "installedPaths": [
                        ".agents/skills/humanizer",
                        "~/.codex/skills/humanizer",
                    ],
                    "installCommand": [
                        "npx",
                        "--yes",
                        "skills",
                        "add",
                        "blader/humanizer",
                        "--agent",
                        "codex",
                        "--yes",
                    ],
                },
                "claude-code": {
                    "installedPaths": [
                        ".claude/skills/humanizer",
                        "~/.claude/skills/humanizer",
                    ],
                    "installCommand": [
                        "npx",
                        "--yes",
                        "skills",
                        "add",
                        "blader/humanizer",
                        "--agent",
                        "claude-code",
                        "--yes",
                    ],
                },
            },
        },
        "graphify": {
            "source": "graphifyy",
            "sourceType": "python-cli",
            "skillPath": "SKILL.md",
            "updatePolicy": "on-helper-upgrade",
            "providers": {
                "codex": {
                    "installedPaths": ["~/.agents/skills/graphify"],
                    "installCommand": ["graphify", "install", "--platform", "codex"],
                },
                "claude-code": {
                    "installedPaths": ["~/.claude/skills/graphify"],
                    "installCommand": ["graphify", "install", "--platform", "claude"],
                },
            },
        },
        "impeccable": {
            "source": "impeccable",
            "sourceType": "npm-cli",
            "skillPath": "SKILL.md",
            "updatePolicy": "manual",
            "providers": {
                "codex": {
                    "installedPaths": [".agents/skills/impeccable"],
                    "installCommand": [
                        "npx",
                        "impeccable",
                        "skills",
                        "install",
                        "-y",
                        "--providers=codex",
                        "--scope=project",
                    ],
                    "updateCommand": [
                        "npx",
                        "impeccable",
                        "skills",
                        "install",
                        "-y",
                        "--providers=codex",
                        "--scope=project",
                        "--force",
                    ],
                },
                "claude-code": {
                    "installedPaths": [".claude/skills/impeccable"],
                    "installCommand": [
                        "npx",
                        "impeccable",
                        "skills",
                        "install",
                        "-y",
                        "--providers=claude-code",
                        "--scope=project",
                    ],
                    "updateCommand": [
                        "npx",
                        "impeccable",
                        "skills",
                        "install",
                        "-y",
                        "--providers=claude-code",
                        "--scope=project",
                        "--force",
                    ],
                },
            },
        },
    },
    "plugins": {
        "ponytail": {
            "source": "DietrichGebert/ponytail",
            "sourceType": "plugin-marketplace",
            "updatePolicy": "manual",
            "providers": {
                "codex": {
                    "pluginId": "ponytail@ponytail",
                    "marketplaceName": "ponytail",
                    "checkCommand": ["codex", "plugin", "list", "--json"],
                    "marketplaceCheckCommand": [
                        "codex",
                        "plugin",
                        "marketplace",
                        "list",
                    ],
                    "installCommand": [
                        "codex",
                        "plugin",
                        "marketplace",
                        "add",
                        "DietrichGebert/ponytail",
                    ],
                    "manualNote": "Open /plugins to install Ponytail and /hooks to review/trust its hooks.",
                },
                "claude-code": {
                    "pluginId": "ponytail@ponytail",
                    "checkCommand": ["claude", "plugin", "list"],
                    "installCommand": [
                        "claude",
                        "plugin",
                        "marketplace",
                        "add",
                        "DietrichGebert/ponytail",
                    ],
                    "manualNote": "Run claude plugin install ponytail@ponytail if needed, then review hooks in /hooks.",
                },
            },
        }
    },
}


@dataclass
class Finding:
    kind: str
    name: str
    provider: str
    status: str
    detail: str
    action: list[str] | None = None
    policy: str = "manual"


def deep_merge(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(default))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_lock() -> dict[str, Any]:
    if not LOCK_PATH.exists():
        return DEFAULT_LOCK
    try:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"skills-lock.json is invalid JSON: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return deep_merge(DEFAULT_LOCK, lock)


def resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path


def hash_path(path: Path) -> str | None:
    if not path.exists():
        return None
    files = (
        [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    )
    digest = hashlib.sha256()
    for file_path in files:
        if ".git" in file_path.parts or "__pycache__" in file_path.parts:
            continue
        rel = file_path.relative_to(path if path.is_dir() else path.parent)
        digest.update(str(rel).encode())
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def first_existing(paths: list[str]) -> Path | None:
    for raw in paths:
        path = resolve_path(raw)
        if path.exists():
            return path
    return None


def run_capture(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    if not command or shutil.which(command[0]) is None:
        return None
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def run_action(command: list[str], dry_run: bool) -> int:
    if dry_run:
        print("  would run: " + " ".join(command))
        return 0
    if shutil.which(command[0]) is None:
        print(f"  missing command: {command[0]}", file=sys.stderr)
        return 127
    print("  running: " + " ".join(command))
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def inspect_skill(name: str, data: dict[str, Any], provider: str) -> Finding | None:
    provider_data = data.get("providers", {}).get(provider)
    if not provider_data:
        return None
    policy = data.get("updatePolicy", "manual")
    path = first_existing(provider_data.get("installedPaths", []))
    action = provider_data.get("installCommand")
    if path is None:
        return Finding(
            "skill",
            name,
            provider,
            "missing",
            "no installed skill path found",
            action,
            policy,
        )
    skill_path = data.get("skillPath")
    hash_target = path / skill_path if skill_path and path.is_dir() else path
    current_hash = hash_path(hash_target)
    expected = provider_data.get("computedHash") or data.get("computedHash")
    if expected and current_hash and expected != current_hash:
        return Finding(
            "skill",
            name,
            provider,
            "drift",
            f"{hash_target} hash differs from lock",
            action,
            policy,
        )
    return Finding("skill", name, provider, "ok", f"{path} installed", None, policy)


def inspect_plugin(name: str, data: dict[str, Any], provider: str) -> Finding | None:
    provider_data = data.get("providers", {}).get(provider)
    if not provider_data:
        return None
    policy = data.get("updatePolicy", "manual")
    action = provider_data.get("installCommand")
    check = provider_data.get("checkCommand")
    result = run_capture(check) if check else None
    if result is None:
        return Finding(
            "plugin",
            name,
            provider,
            "unknown",
            f"{provider} CLI unavailable",
            action,
            policy,
        )
    output = (result.stdout or "") + (result.stderr or "")
    plugin_id = provider_data.get("pluginId", name)
    if result.returncode == 0 and plugin_id in output:
        return Finding(
            "plugin", name, provider, "ok", "plugin appears installed", None, policy
        )
    marketplace_check = provider_data.get("marketplaceCheckCommand")
    marketplace = run_capture(marketplace_check) if marketplace_check else None
    if (
        marketplace
        and marketplace.returncode == 0
        and provider_data.get("marketplaceName", name) in marketplace.stdout
    ):
        note = provider_data.get(
            "manualNote", "finish plugin install and hook trust in the provider UI"
        )
        return Finding("plugin", name, provider, "manual", note, None, policy)
    note = provider_data.get(
        "manualNote", "plugin install may require interactive trust"
    )
    return Finding("plugin", name, provider, "missing", note, action, policy)


def selected_providers(provider: str) -> set[str]:
    if provider == "all":
        return {"codex", "claude-code"}
    return {provider}


def collect_findings(
    lock: dict[str, Any], provider: str, include_plugins: bool
) -> list[Finding]:
    providers = selected_providers(provider)
    findings: list[Finding] = []
    for name, data in sorted(lock.get("skills", {}).items()):
        for current in sorted(providers):
            finding = inspect_skill(name, data, current)
            if finding:
                findings.append(finding)
    if include_plugins:
        for name, data in sorted(lock.get("plugins", {}).items()):
            for current in sorted(providers):
                finding = inspect_plugin(name, data, current)
                if finding:
                    findings.append(finding)
    return findings


def print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        print(
            f"{finding.status:7} {finding.kind:6} {finding.provider:11} {finding.name}: {finding.detail}"
        )


def should_update(finding: Finding, force: bool, allow_manual: bool) -> bool:
    if finding.status not in {"missing", "drift"}:
        return False
    if finding.action is None:
        return False
    if force:
        return True
    if finding.policy in {"on-helper-upgrade", "latest"}:
        return True
    return allow_manual and finding.policy == "manual"


def write_checked_timestamp(lock: dict[str, Any]) -> None:
    if not LOCK_PATH.exists():
        return
    lock["lastChecked"] = datetime.now(timezone.utc).isoformat()
    LOCK_PATH.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=["all", "codex", "claude-code"], default="all"
    )
    parser.add_argument(
        "--check", action="store_true", help="Report status without changing anything."
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Run managed update/install commands when policy allows it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show update commands without running them.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow all managed update commands, including drifted/manual items.",
    )
    parser.add_argument(
        "--allow-manual",
        action="store_true",
        help="Allow manual-policy items during --update.",
    )
    parser.add_argument("--no-plugins", action="store_true", help="Skip plugin checks.")
    parser.add_argument(
        "--write-checked",
        action="store_true",
        help="Update skills-lock.json lastChecked after a successful check.",
    )
    args = parser.parse_args()

    if args.update and args.check:
        parser.error("--check and --update are mutually exclusive")
    if not args.update:
        args.check = True

    lock = load_lock()
    findings = collect_findings(lock, args.provider, not args.no_plugins)
    print_findings(findings)

    failures = 0
    if args.update:
        for finding in findings:
            if not should_update(finding, args.force, args.allow_manual):
                continue
            assert finding.action is not None
            code = run_action(finding.action, args.dry_run)
            failures += 1 if code else 0
    elif args.write_checked:
        write_checked_timestamp(lock)

    actionable = [
        f for f in findings if f.status in {"missing", "drift", "manual", "unknown"}
    ]
    if actionable:
        print()
        print(
            "Review the non-ok items above. Use --update for policy-approved updates or --force for explicit refreshes."
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
