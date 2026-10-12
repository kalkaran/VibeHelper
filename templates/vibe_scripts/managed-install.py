#!/usr/bin/env python3
"""Conservative migration, ownership receipts, and local installation evidence."""

import argparse
import ast
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(root, relative):
    path = root / relative
    if path.is_absolute() and not path.is_relative_to(root):
        return None
    if ".." in Path(relative).parts or not path.resolve().is_relative_to(root):
        return None
    return (
        None
        if any(
            item.is_symlink()
            for item in [path, *path.parents]
            if item != root and item.is_relative_to(root)
        )
        else path
    )


def files(root, provider):
    for relative in [
        f".{provider}",
        f"agent/{provider}",
        f"vibe_scripts/{provider}",
        "AGENTS.md" if provider == "codex" else "CLAUDE.md",
    ]:
        path = root / relative
        candidates = path.rglob("*") if path.is_dir() else [path]
        for item in candidates:
            if (
                item.is_file()
                and safe(root, item.relative_to(root))
                and ".bak." not in item.name
            ):
                yield str(item.relative_to(root)), item


def recognizable(path, provider):
    try:
        text = path.read_text()
    except (OSError, UnicodeError):
        return False
    marker = provider.title() + "Helper-Version:"
    if marker in text:
        return True
    canonical = Path(__file__).parent / path.name
    if (
        path.parent.name == provider
        and path.parent.parent.name == "vibe_scripts"
        and canonical.is_file()
    ):
        return path.read_bytes() == canonical.read_bytes()
    if provider == "claude" and path.name == "settings.local.json":
        try:
            document = json.loads(text)
            if set(document) != {"hooks"} or not isinstance(document["hooks"], dict):
                return False
            commands = [
                hook["command"]
                for groups in document["hooks"].values()
                for group in groups
                for hook in group["hooks"]
            ]
            return bool(commands) and all(
                ".claude/hooks/" in command
                or "/vibe_scripts/claude/quality-hook.py" in command
                for command in commands
            )
        except (ValueError, TypeError, KeyError):
            return False
    return False


def merge_codex(path, snippet):
    """Append missing managed hook registrations without rewriting user TOML."""
    import tomllib

    original = path.read_text() if path.exists() else ""
    current = tomllib.loads(original)
    desired = tomllib.loads(snippet)
    hooks = current.get("hooks", {})
    if not isinstance(hooks, dict):
        raise TypeError("Codex hooks must be a table; configuration preserved")

    def inline(value):
        if isinstance(value, dict):
            return (
                "{ "
                + ", ".join(
                    json.dumps(key) + " = " + inline(item)
                    for key, item in value.items()
                )
                + " }"
            )
        if isinstance(value, list):
            return "[" + ", ".join(inline(item) for item in value) + "]"
        return json.dumps(value)

    text = original.rstrip()
    if not original:
        text = "# CodexHelper-Version: 2026-10-12-v44\n"
    for event, groups in desired.get("hooks", {}).items():
        existing = hooks.get(event, [])
        if not isinstance(existing, list) or not all(
            isinstance(group, dict) and isinstance(group.get("hooks", []), list)
            for group in existing
        ):
            raise ValueError(f"invalid existing {event} hooks; configuration preserved")
        commands = {
            hook.get("command")
            for group in existing
            for hook in group.get("hooks", [])
            if isinstance(hook, dict)
        }
        for group in groups:
            missing = [
                hook
                for hook in group.get("hooks", [])
                if hook.get("command") not in commands
            ]
            if missing:
                merged = dict(group, hooks=missing)
                text += "\n\n[[hooks." + json.dumps(event) + "]]\n"
                text += (
                    "\n".join(
                        json.dumps(key) + " = " + inline(value)
                        for key, value in merged.items()
                    )
                    + "\n"
                )
                commands.update(hook.get("command") for hook in missing)
    # Preserve an explicit user opt-out and report activation as needs action.
    features = current.get("features", {})
    if not isinstance(features, dict):
        raise TypeError("Codex features must be a table; configuration preserved")
    if "features" not in current:
        text += "\n[features]\nhooks = true\n"
    elif isinstance(features, dict) and "hooks" not in features:
        match = re.search(r"^\s*\[features\]\s*(?:#.*)?$", text, flags=re.MULTILINE)
        if match:
            text = text[: match.end()] + "\nhooks = true" + text[match.end() :]
        else:
            print(
                "needs action: enable features.hooks in existing inline configuration"
            )
    elif features.get("hooks") is False:
        print(
            "needs action: features.hooks is explicitly disabled; existing preference preserved"
        )
    updated = text.rstrip() + "\n"
    parsed = tomllib.loads(updated)
    for key, value in current.items():
        if key not in {"hooks", "features"} and parsed.get(key) != value:
            raise ValueError(f"cannot preserve Codex {key}; configuration unchanged")
    if updated != original:
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + f".bak.{time.time_ns()}"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated)
    print(
        "configured: Codex hooks merged; existing settings and custom hooks preserved"
    )


def migrate(root, dry_run=False):
    changed = []
    for relative in [
        "Makefile",
        "vibe_scripts/Makefile",
        "vibe_scripts/codex/Makefile",
        "vibe_scripts/claude/Makefile",
    ]:
        path = safe(root, relative)
        if not path or not path.is_file():
            continue
        original = path.read_text()
        if "semgrep" not in original.lower():
            continue

        def block(match):
            text = match.group()
            removed = set()

            def target(target_match):
                name, dependencies, body = (
                    target_match.group(1),
                    target_match.group(2),
                    target_match.group(3),
                )
                if dependencies.strip():
                    return target_match.group()
                lines = [
                    line.strip().lstrip("@")
                    for line in body.splitlines()
                    if line.strip()
                ]
                known = lines and all(known_recipe(line) for line in lines)
                if known and any(
                    "semgrep" in line and "scan" in line for line in lines
                ):
                    removed.add(name)
                    return ""
                return target_match.group()

            text = re.sub(
                r"^(security(?:-ai)?):([^\n]*)\n((?:\t[^\n]*\n)+)",
                target,
                text,
                flags=re.MULTILINE,
            )
            for name in removed:
                text = re.sub(
                    r"^(\.PHONY:|verify(?:-ai)?:)([^\n]*)$",
                    lambda m, name=name: (
                        m[1]
                        + re.sub(r"(?<!\S)" + re.escape(name) + r"(?!\S)", "", m[2])
                    ),
                    text,
                    flags=re.MULTILINE,
                )
                text = re.sub(
                    r"^\t@?\$\(MAKE\)(?: -f vibe_scripts/(?:codex|claude)/Makefile)? "
                    + re.escape(name)
                    + r"\n",
                    "",
                    text,
                    flags=re.MULTILINE,
                )
            return text

        if re.match(
            r"^# (?:CodexHelper|ClaudeHelper)-Version: [^\n]+\n# Generated by part2.sh\n",
            original,
        ):
            updated = block(re.match(r"[\s\S]*", original))
        else:
            updated = re.sub(
                r"^# >>> (?:CodexHelper|ClaudeHelper|ai-quality targets >>>)[^\n]*\n.*?^# <<< (?:CodexHelper|ClaudeHelper|ai-quality targets <<<)[^\n]*(?:\n|$)",
                block,
                original,
                flags=re.MULTILINE | re.DOTALL,
            )
        if updated != original:
            if not dry_run:
                backup = path.with_name(path.name + f".bak.{time.time_ns()}")
                shutil.copy2(path, backup)
                path.write_text(updated)
            changed.append(relative)
            print(
                f"configured: {relative}: retired generated Semgrep wiring; backup preserved"
                if not dry_run
                else f"needs action: would migrate {relative}"
            )
        if "semgrep" in updated.lower():
            print(
                f"needs action: {relative}: custom or ambiguous Semgrep content preserved for manual review"
            )
    return changed


def known_recipe(line):
    """Accept only generated command grammar; additions require manual review."""
    try:
        words = shlex.split(line)
        if len(words) > 1 and re.fullmatch(
            r"vibe_scripts/(?:codex/|claude/)?ai-quality-wrapper.py", words[1]
        ):
            if words[0] != "python3" or words[2:4] != ["--label", "security-semgrep"]:
                return False
            options, separator, command = line.partition(" -- ")
            if not separator or any(token in options for token in [";", "&&", "|"]):
                return False
            words = shlex.split(command)
            if len(words) == 1:
                words = shlex.split(words[0])
        if words[:4] == ["mkdir", "-p", ".cache/semgrep", "&&"]:
            words = words[4:]
        elif words == ["mkdir", "-p", ".cache/semgrep"]:
            return True
        while words and re.fullmatch(
            r"(?:SSL_CERT_FILE|SEMGREP_LOG_FILE|SEMGREP_SETTINGS_FILE|SEMGREP_VERSION_CACHE_PATH)=[/A-Za-z0-9_.@+-]+",
            words[0],
        ):
            words.pop(0)
        return words in [["semgrep", "scan"], ["semgrep", "scan", "--json"]]
    except ValueError:
        return False


def recover_stale_lock(root, lock, provider):
    owner = safe(root, (lock / "run-id").relative_to(root))
    run_id = owner.read_text() if owner and owner.is_file() else ""
    if not re.fullmatch(r"[1-9][0-9]{0,9}", run_id) or int(run_id) > 2147483647:
        raise SystemExit("failed: lock owner is unknown; preserve it for manual review")
    try:
        os.kill(int(run_id), 0)
    except ProcessLookupError:
        pass
    except PermissionError:
        raise SystemExit(
            "failed: lock owner may still be running; cannot force restart"
        )
    else:
        raise SystemExit(
            f"failed: installation process {run_id} is still running; cannot force restart"
        )
    prior = safe(
        root, (lock.parent / f"{provider}.{run_id}.before.json").relative_to(root)
    )
    if prior is None:
        raise SystemExit(
            "failed: prior installation receipt is a symlink; preserve it for review"
        )
    prior.unlink(missing_ok=True)
    print(
        f"configured: cleared abandoned installation transaction {run_id}; restarting"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=["begin", "finish", "abort", "cleanup", "migrate", "merge-codex"],
    )
    parser.add_argument("--provider", choices=["codex", "claude"], required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--hooks-skipped", action="store_true")
    parser.add_argument("--run-id", default=str(os.getppid()))
    parser.add_argument("--skipped", action="append", default=[])
    parser.add_argument("--quality-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.operation == "merge-codex":
        path = safe(root, ".codex/config.toml")
        if not path:
            raise ValueError("refusing symlinked Codex configuration")
        merge_codex(path, sys.stdin.read())
        return
    directory = root / ".cache" / "vibehelper-install"
    if not safe(root, directory.relative_to(root)):
        raise SystemExit(
            "failed: managed receipt directory is a symlink; preserve it and review manually"
        )
    receipt = directory / (args.provider + ".json")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        raise SystemExit("failed: invalid installer run identifier")
    before = directory / (args.provider + "." + args.run_id + ".before.json")
    lock = directory / (args.provider + ".lock")
    if not all(safe(root, path.relative_to(root)) for path in [receipt, before, lock]):
        raise SystemExit(
            "failed: installation receipt is a symlink; preserve it and review manually"
        )

    def load(path):
        value = json.loads(path.read_text()) if path.is_file() else {}
        if not isinstance(value, dict) or not all(
            isinstance(name, str)
            and isinstance(fingerprint, str)
            and re.fullmatch(r"[0-9a-f]{64}", fingerprint)
            for name, fingerprint in value.items()
        ):
            raise ValueError(f"invalid ownership receipt: {path.name}")
        return value

    if args.operation == "migrate":
        migrate(root, args.dry_run)
        return
    if args.operation == "cleanup":
        if not args.dry_run:
            directory.mkdir(parents=True, exist_ok=True)
            try:
                lock.mkdir()
            except FileExistsError:
                raise SystemExit(
                    "failed: installation transaction is active; finish or abort its recorded run before cleanup"
                )
        elif lock.exists():
            raise SystemExit(
                "failed: installation transaction is active; wait before previewing cleanup"
            )
        try:
            owned = load(receipt)
            parents = set()
            for relative, fingerprint in owned.items():
                allowed = relative == (
                    "AGENTS.md" if args.provider == "codex" else "CLAUDE.md"
                ) or any(
                    relative.startswith(prefix)
                    for prefix in [
                        f".{args.provider}/",
                        f"agent/{args.provider}/",
                        f"vibe_scripts/{args.provider}/",
                    ]
                )
                if not allowed:
                    print(
                        f"needs action: preserved path outside managed scope: {relative}"
                    )
                    continue
                path = safe(root, relative)
                if path and path.is_file() and digest(path) == fingerprint:
                    print(
                        f"explicitly skipped: would remove unchanged managed file {relative}"
                        if args.dry_run
                        else f"configured: removed {relative}"
                    )
                    if not args.dry_run:
                        path.unlink()
                        parents.update(
                            parent
                            for parent in path.parents
                            if parent != root and parent.is_relative_to(root)
                        )
                else:
                    print(f"needs action: preserved modified or missing {relative}")
            if not owned:
                print(
                    "needs action: no ownership receipt; preserved all existing files"
                )
            for parent in sorted(
                parents, key=lambda path: len(path.parts), reverse=True
            ):
                try:
                    parent.rmdir()
                except OSError:
                    pass
            return
        finally:
            if not args.dry_run:
                lock.rmdir()
    if args.dry_run:
        print("explicitly skipped: managed receipts and validation during dry run")
        return
    directory.mkdir(parents=True, exist_ok=True)
    current = {name: digest(path) for name, path in files(root, args.provider)}
    if args.operation == "begin":
        guard = safe(root, (directory / (args.provider + ".guard")).relative_to(root))
        if guard is None:
            raise SystemExit(
                "failed: installation guard is a symlink; preserve it for review"
            )
        # Serialize forced retries through publishing the replacement owner.
        with guard.open("a") as guard_stream:
            fcntl.flock(guard_stream, fcntl.LOCK_EX)
            try:
                lock.mkdir()
            except FileExistsError:
                if not args.force:
                    raise SystemExit(
                        "failed: another installation transaction is active; use --force to restart an abandoned run"
                    )
                recover_stale_lock(root, lock, args.provider)
            (lock / "run-id").write_text(args.run_id)
            with before.open("x") as stream:
                json.dump(current, stream)
        migrate(root)
        return
    if not before.is_file():
        raise SystemExit(
            "failed: no matching begin receipt; ownership cannot be inferred"
        )
    if not lock.is_dir() or (lock / "run-id").read_text() != args.run_id:
        raise SystemExit("failed: installation transaction does not own this lock")
    if args.operation == "abort":
        before.unlink()
        (lock / "run-id").unlink()
        lock.rmdir()
        print(
            "explicitly skipped: transaction aborted; generated files preserved for review"
        )
        return
    try:
        prior, owned = load(before), load(receipt)
        receipt.write_text(
            json.dumps(
                {
                    name: value
                    for name, value in current.items()
                    if (name not in prior and recognizable(root / name, args.provider))
                    or (name in owned and owned.get(name) == prior.get(name))
                },
                indent=2,
            )
        )
        failures = []
        required = ["ai-quality-wrapper.py", "agent-check-edited.py"]
        if not args.quality_only:
            required += ["agent-verify.sh", "quality-hook.py"]
        for name in required:
            path = root / "vibe_scripts" / args.provider / name
            if not path.is_file():
                failures.append(name)
                print(f"failed: required helper missing: {path.relative_to(root)}")
            else:
                try:
                    if path.suffix == ".py":
                        ast.parse(path.read_text(), filename=str(path))
                    else:
                        subprocess.run(
                            ["bash", "-n", str(path)],
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                    version = re.search(
                        r"(?:Helper-Version:|Version:)\s*([^\n]+)", path.read_text()
                    )
                    print(
                        f"verified: {path.relative_to(root)} syntax; version:{version[1] if version else 'shared'} sha256:{digest(path)[:12]}"
                    )
                except (SyntaxError, subprocess.CalledProcessError, OSError) as error:
                    failures.append(name)
                    print(f"failed: {name}: invalid required helper: {error}")
        if args.hooks_skipped or args.quality_only:
            print(
                "explicitly skipped: provider hook validation (hooks disabled or quality-only phase)"
            )
        else:
            for name in [
                "pre_tool_use_policy.py",
                "post_edit_check.py",
                "stop_edited_check.py",
            ]:
                path = root / f".{args.provider}" / "hooks" / name
                try:
                    ast.parse(path.read_text(), filename=str(path))
                    print(
                        f"verified: {path.relative_to(root)} syntax; activation still requires provider trust"
                    )
                except (SyntaxError, OSError) as error:
                    failures.append(name)
                    print(f"failed: {name}: required hook invalid or missing: {error}")
            config = (
                root
                / f".{args.provider}"
                / ("config.toml" if args.provider == "codex" else "settings.local.json")
            )
            try:
                if args.provider == "codex":
                    import tomllib

                    settings = tomllib.loads(config.read_text())
                else:
                    settings = json.loads(config.read_text())
                if not isinstance(settings, dict) or not isinstance(
                    settings.get("hooks"), dict
                ):
                    raise TypeError("hook configuration and hooks must be objects")
                hooks = settings["hooks"]
                for event, expected in [
                    ("SessionStart", "quality-hook.py"),
                    ("PostToolUse", "post_edit_check.py"),
                    ("Stop", "stop_edited_check.py"),
                ]:
                    groups = hooks.get(event, [])
                    if not isinstance(groups, list) or not all(
                        isinstance(group, dict)
                        and isinstance(group.get("hooks"), list)
                        and all(
                            isinstance(hook, dict)
                            and isinstance(hook.get("command"), str)
                            for hook in group["hooks"]
                        )
                        for group in groups
                    ):
                        raise ValueError(
                            f"{event} groups must contain hook command objects"
                        )
                    if expected not in json.dumps(groups):
                        raise ValueError(
                            f"{event} does not register the required {expected}"
                        )
                print(
                    f"verified: {config.relative_to(root)} required hook registration; live activation needs trust"
                )
            except (ImportError, OSError, ValueError, TypeError) as error:
                failures.append(str(config))
                print(f"failed: hook configuration: {error}")
        config_text = ""
        for base in [root / f".{args.provider}", Path.home() / f".{args.provider}"]:
            for filename in ["config.toml", "settings.json", "settings.local.json"]:
                try:
                    config_text += (base / filename).read_text().lower()
                except OSError:
                    pass
        for component in [
            "context7",
            "chrome-devtools",
            "code-review-graph",
            "rtk",
            "graphify",
            "ponytail",
            "humanizer",
            "unlazy",
            "impeccable",
            "omniroute",
            "llm-council",
        ]:
            if component in args.skipped:
                print(
                    f"explicitly skipped: {component}: disabled for this installer run"
                )
            elif component in config_text or shutil.which(component):
                print(
                    f"configured: {component}: configuration or executable found; access/authentication unverified"
                )
            else:
                print(
                    f"needs action: {component}: no local configuration or executable evidence; verify provider availability"
                )
        print(
            "needs action: restart provider and review/trust project hooks before live activation"
        )
        if failures:
            raise SystemExit(1)
    finally:
        before.unlink(missing_ok=True)
        (lock / "run-id").unlink(missing_ok=True)
        lock.rmdir()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        print(
            f"failed: managed installation evidence: {error}; preserve existing files and review the receipt",
            file=sys.stderr,
        )
        raise SystemExit(1)
