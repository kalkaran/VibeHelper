#!/usr/bin/env python3
"""Conservative migration, ownership receipts, and local installation evidence."""

import argparse
import ast
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

MEMORY_ASSETS = (
    "project-memory.py",
    "project-memory-index.py",
    "project-memory-watch.py",
    "install-project-memory.py",
)


def memory_projects(search_roots=None):
    """Discover live installations, including configured roots outside ~/Code."""
    import tomllib

    roots = set(search_roots or [Path.home() / "Code"])
    if search_roots is None:
        for config in (
            Path.home() / ".codex/config.toml",
            Path.home() / ".claude.json",
        ):
            if config.is_file():
                data = (
                    tomllib.loads(config.read_text())
                    if config.suffix == ".toml"
                    else json.loads(config.read_text())
                )
                roots.update(
                    Path(name).expanduser() for name in data.get("projects", {})
                )
    found = {}
    excluded = {
        ".git",
        ".cache",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "notes",
        ".graphify",
        "graphify-out",
        ".agents",
        "backups",
    }
    for root in sorted(roots):
        for directory, directories, _files in os.walk(root):
            directories[:] = [
                name
                for name in directories
                if name not in excluded and not (Path(directory) / name).is_symlink()
            ]
            project = Path(directory).resolve()
            providers = [
                provider
                for provider in ("codex", "claude")
                if (project / f"vibe_scripts/{provider}/project-memory.py").is_file()
            ]
            if providers:
                found[str(project)] = providers
    return dict(
        sorted(
            found.items(), key=lambda item: (Path(item[0]).name != "react_app", item[0])
        )
    )


def memory_refresh(root, provider, dry_run=False, previous_source=None):
    guard = safe(root, f".cache/vibehelper-install/{provider}.guard")
    if guard is None:
        raise ValueError("Unsafe installation guard")
    if dry_run:
        return _memory_refresh(root, provider, True, previous_source)
    guard.parent.mkdir(parents=True, exist_ok=True)
    with guard.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        if (guard.parent / f"{provider}.lock").exists():
            raise ValueError("An installation is already active")
        return _memory_refresh(root, provider, False, previous_source)


@contextlib.contextmanager
def memory_transaction(root, backup, manifest):
    try:
        yield
    except BaseException:
        manifest["status"] = "failed; restoring backup"
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        for name in manifest["modified"]:
            previous = manifest["before"][name]
            path = root / name
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                shutil.copy2(backup / "files" / name, path)
        manifest["status"] = "rolled-back"
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        raise


def _memory_refresh(root, provider, dry_run=False, previous_source=None):
    """Migrate only memory assets; retain durable backups and custom settings."""
    receipt = safe(root, f".cache/vibehelper-install/{provider}.json")
    if receipt is None:
        raise ValueError("Unsafe installation receipt")
    owned = json.loads(receipt.read_text()) if receipt.exists() else {}
    assets = {}
    for name in MEMORY_ASSETS:
        relative = f"vibe_scripts/{provider}/{name}"
        target = safe(root, relative)
        source = Path(__file__).with_name(name)
        previous = previous_source / name if previous_source else None
        if target is None:
            raise ValueError(f"Preserving symlinked memory helper: {relative}")
        if target.exists() and not (
            owned.get(relative) == digest(target)
            or target.read_bytes() == source.read_bytes()
            or (
                previous
                and previous.is_file()
                and target.read_bytes() == previous.read_bytes()
            )
        ):
            raise ValueError(f"Preserving customized memory helper: {relative}")
        assets[relative] = source
    policy_name = f"vibe_scripts/{provider}/memory-policy.json"
    policy_path = safe(root, policy_name)
    if policy_path is None:
        raise ValueError("Unsafe memory policy")
    policy = json.loads(policy_path.read_text()) if policy_path.exists() else {}
    # v1 hooks=false was the shipped default, before session watching existed.
    enabled = policy.get("watch", True)
    if not isinstance(enabled, bool):
        raise TypeError("Custom watch policy needs review")
    config_names = (
        [
            f".{provider}/config.toml",
            f".{provider}/config-snippet.toml",
            f".{provider}/hooks.json",
        ]
        if provider == "codex"
        else [".claude/settings.local.json", ".claude/settings.json"]
    )
    names = [
        *assets,
        policy_name,
        *config_names,
        ".graphifyignore",
        ".cache/project-memory/state.json",
        f".cache/vibehelper-install/{provider}.json",
    ]
    guidance_name = f"agent/{provider}/context-strategy.md"
    guidance = safe(root, guidance_name)
    old_guidance = "Automatic memory hooks are disabled. Use the context or refresh command explicitly;\nordinary edits, reads and completion do not trigger graph maintenance."
    new_guidance = "Agent sessions share a filesystem watcher that refreshes code after three seconds\nwithout saves. SessionEnd releases it; Stop between replies leaves it running.\nKnowledge and media changes await explicit semantic review. Use memory-policy.json\nwatch=false or --no-memory-watch to opt out. Status reports coverage and logs."
    update_guidance = (
        guidance is not None
        and guidance.is_file()
        and old_guidance in guidance.read_text()
    )
    if update_guidance:
        names.append(guidance_name)
    for directory in (
        ".graphify",
        "graphify-out",
        ".code-review-graph",
        ".cache/project-memory",
    ):
        base = safe(root, directory)
        if base is None:
            raise ValueError(f"Unsafe graph directory: {directory}")
        if base.exists():
            names.extend(
                str(path.relative_to(root))
                for path in base.rglob("*")
                if path.is_file()
            )
    for name in names:
        if safe(root, name) is None:
            raise ValueError(f"Unsafe memory artifact: {name}")
    if dry_run:
        return {
            "root": str(root),
            "provider": provider,
            "status": "ready",
            "assets": list(assets),
            "watch": enabled,
        }
    cache = root / ".cache/project-memory"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        backup = receipt.parent / f"memory-backup-{provider}-{time.time_ns()}"
        backup.mkdir(parents=True)
        before = {}
        for name in sorted(set(names)):
            path = root / name
            before[name] = digest(path) if path.is_file() else None
            if path.is_file():
                destination = backup / "files" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if name == ".code-review-graph/graph.db":
                    with (
                        sqlite3.connect(path) as source_db,
                        sqlite3.connect(destination) as backup_db,
                    ):
                        source_db.backup(backup_db)
                else:
                    shutil.copy2(path, destination)
        manifest = {
            "root": str(root),
            "provider": provider,
            "before": before,
            "modified": [
                *assets,
                policy_name,
                *config_names,
                ".graphifyignore",
                ".cache/project-memory/state.json",
                f".cache/vibehelper-install/{provider}.json",
                *([guidance_name] if update_guidance else []),
            ],
            "status": "backed-up",
        }
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (backup / "RESTORE.txt").write_text(
            "Stop agent sessions before restoring. Copy each file under files/ back to the same relative path in "
            + str(root)
            + ". Paths with null in manifest.before did not exist; remove only the migration-created versions after checking for later edits. Preserve the backup until coverage is verified.\n"
        )
        with memory_transaction(root, backup, manifest):
            for name, source in assets.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, root / name)
                owned[name] = digest(root / name)
            policy["watch"] = enabled
            policy_path.write_text(json.dumps(policy, indent=2) + "\n")
            spec = importlib.util.spec_from_file_location(
                "memory_installer",
                Path(__file__).with_name("install-project-memory.py"),
            )
            installer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(installer)
            installer.install(
                root,
                provider,
                hooks=enabled,
                crg=policy.get("crg", True),
                graphify=policy.get("graphify", True),
            )
            if update_guidance:
                guidance.write_text(
                    guidance.read_text().replace(old_guidance, new_guidance)
                )
                if guidance_name in owned:
                    owned[guidance_name] = digest(guidance)
            for name in (policy_name, *config_names, ".graphifyignore"):
                path = root / name
                if path.is_file() and (name in owned or name == policy_name):
                    owned[name] = digest(path)
            receipt.write_text(json.dumps(owned, indent=2) + "\n")
            state_path = cache / "state.json"
            if state_path.is_file():
                state = json.loads(state_path.read_text())
                for backend in ("crg", "graphify"):
                    state.pop(backend, None)
                state_path.write_text(json.dumps(state, indent=2) + "\n")
            manifest.update(
                status="migrated",
                after={
                    name: digest(root / name)
                    for name in names
                    if (root / name).is_file()
                },
            )
            (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {
        "root": str(root),
        "provider": provider,
        "status": "migrated",
        "watch": enabled,
        "backup": str(backup),
        "restore": str(backup / "RESTORE.txt"),
    }


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
                or command
                == 'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; helper="$root/vibe_scripts/claude/project-memory.py"; [ -f "$helper" ] || exit 0; exec python3 "$helper" hook --provider claude --root "$root"'
                or command
                == 'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; helper="$root/vibe_scripts/claude/cleanup-cache.py"; [ -f "$helper" ] || exit 0; exec python3 "$helper" --root "$root"'
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


def configure_test_verification(root, provider, dry_run=False):
    path = safe(root, f"vibe_scripts/{provider}/Makefile")
    if path is None or not path.is_file():
        return
    original = path.read_text()
    if "test-verification.py" in original or "test-run-ai:" in original:
        return
    match = re.search(r"(?m)^test-ai:\s*\n((?:\t[^\n]*\n)+)", original)
    if not match:
        return
    for line in match[1].splitlines():
        try:
            words = shlex.split(line.lstrip("\t@"))
        except ValueError:
            return
        if (
            len(words) < 5
            or words[:3]
            != ["python3", f"vibe_scripts/{provider}/ai-quality-wrapper.py", "--label"]
            or not words[3].startswith("test-")
        ):
            print(
                "needs action: custom test-ai recipe preserved; verification reuse unavailable"
            )
            return
    if dry_run:
        print("needs action: would record complete controlled test verification")
        return
    if not (path.parent / "test-verification.py").is_file():
        raise OSError(
            "Test verification driver must be installed before wiring test-ai"
        )
    replacement = (
        f"test-ai:\n\t@python3 vibe_scripts/{provider}/test-verification.py --provider {provider}\n\n"
        ".PHONY: test-run-ai\ntest-run-ai:\n" + match[1]
    )
    shutil.copy2(path, path.with_name(path.name + f".bak.{time.time_ns()}"))
    path.write_text(original[: match.start()] + replacement + original[match.end() :])
    print("configured: full test target verification; existing test recipes preserved")


def configure_markdownlint(root, dry_run=False):
    configs = list(root.glob(".markdownlint*"))
    if not configs:
        if dry_run:
            print("needs action: would disable Markdown line-length linting")
        else:
            (root / ".markdownlint.json").write_text(
                json.dumps({"MD013": False}, indent=2) + "\n"
            )
            print("configured: Markdown line-length linting disabled")
    else:
        config = safe(root, ".markdownlint.json")
        if config is not None and config.is_file():
            try:
                previous = json.loads(config.read_text())
            except (OSError, UnicodeError, ValueError):
                previous = None
            if previous == {"MD013": {"line_length": 200}}:
                if dry_run:
                    print(
                        "needs action: would disable legacy Markdown line-length linting"
                    )
                else:
                    config.write_text(json.dumps({"MD013": False}, indent=2) + "\n")
                    print("configured: disabled legacy Markdown line-length linting")
    path = safe(root, "biome.json")
    if path is None or (root / "biome.jsonc").exists():
        print(
            "needs action: custom/symlinked Biome config preserved; review Markdownlint coverage"
        )
        return
    if not path.is_file():
        return
    original = path.read_text()
    try:
        data = json.loads(original)
        includes = data.get("files", {}).get("includes")
    except (ValueError, AttributeError):
        print(
            "needs action: custom Biome config preserved; review Markdownlint coverage"
        )
        return
    if not isinstance(includes, list) or ".markdownlint.json" in includes:
        return
    if dry_run:
        print("needs action: would include .markdownlint.json in Biome")
        return
    includes.append(".markdownlint.json")
    shutil.copy2(path, path.with_name(path.name + f".bak.{time.time_ns()}"))
    path.write_text(json.dumps(data, indent=2) + "\n")
    print("configured: Biome includes .markdownlint.json; other settings preserved")


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


def resource_writable(root, provider, name):
    """Refresh new workflow resources only while their installed bytes are owned."""
    path = safe(root, name)
    receipt = safe(root, f".cache/vibehelper-install/{provider}.json")
    if path is None or receipt is None:
        return False
    if not path.exists():
        return True
    try:
        owned = json.loads(receipt.read_text())
        return (
            isinstance(owned, dict)
            and path.is_file()
            and owned.get(name) == digest(path)
        )
    except (OSError, ValueError):
        return False


def quality_assets(root, provider):
    """Prepare only quality resources; never run the general installer or fixers."""
    source = Path(__file__).parent
    assets = {
        f"vibe_scripts/{provider}/{name}": (source / name).read_bytes()
        for name in (
            "agent-check-edited.py",
            "agent-verify.sh",
            "ai-quality-wrapper.py",
            "quality-hook.py",
        )
    }
    helper = "CodexHelper" if provider == "codex" else "ClaudeHelper"
    installer = (source.parents[1] / helper / "MacOS/part1.sh").read_text()
    procedures = installer.split("<<'EOF_QUALITY_PROCEDURES' || :\n", 1)[1].split(
        "\nEOF_QUALITY_PROCEDURES", 1
    )[0]
    assets[f"agent/{provider}/procedures.md"] = (procedures + "\n").encode()
    if provider == "codex":
        hook = installer.split("cat <<'EOF_PRE_HOOK'\n", 1)[1].split(
            "\nEOF_PRE_HOOK", 1
        )[0]
        assets[".codex/hooks/pre_tool_use_policy.py"] = (hook + "\n").encode()
    name = f"vibe_scripts/{provider}/Makefile"
    makefile = safe(root, name)
    if makefile is None or not makefile.is_file():
        raise ValueError("Quality refresh requires an existing managed Makefile")
    text = makefile.read_text()
    command = f"\t@python3 vibe_scripts/{provider}/agent-check-edited.py --mode lint"
    pattern = r"(?m)^lint-ai:[^\n]*\n(?:\t[^\n]*\n|\n)*"
    if not re.search(pattern, text):
        raise ValueError("Quality refresh cannot locate the managed lint target")
    text = re.sub(pattern, "lint-ai:\n" + command + "\n\n", text)
    if not re.search(r"(?m)^lint-all-ai:", text):
        addition = "\n.PHONY: lint-all-ai\nlint-all-ai:\n" + command + " --all\n\n"
        marker = "# <<< ClaudeHelper managed targets"
        text = (
            text.replace(marker, addition + marker, 1)
            if marker in text
            else text + addition
        )
    else:
        text = re.sub(
            r"(?m)^lint-all-ai:[^\n]*\n(?:\t[^\n]*\n|\n)*",
            "lint-all-ai:\n" + command + " --all\n\n",
            text,
        )

    def verify(match):
        return (
            match[0]
            .replace(" edited-ai", " lint-all-ai")
            .replace(" lint-ai", " lint-all-ai")
        )

    text = re.sub(r"(?m)^verify-ai:[^\n]*\n(?:\t[^\n]*\n|\n)*", verify, text)
    assets[name] = text.encode()
    return assets


def install_usage_tracker(
    root,
    provider,
    dry_run=False,
    *,
    quality=False,
    memory_hook=False,
    reporting=False,
    previous_source=None,
    reviewed_makefile_sha256=None,
):
    """Refresh authorized assets in an existing installation, retaining ownership."""
    label = (
        "Reporting"
        if reporting
        else "Memory hook"
        if memory_hook
        else "Quality"
        if quality
        else "Usage tracker"
    )
    receipt = safe(root, f".cache/vibehelper-install/{provider}.json")
    if receipt is None or not receipt.is_file():
        raise ValueError(f"{label} refresh requires an existing managed installation")
    guard = safe(root, f".cache/vibehelper-install/{provider}.guard")
    if guard is None:
        raise ValueError("Installation guard is a symlink")
    with contextlib.nullcontext() if dry_run else guard.open("a") as stream:
        if stream is not None:
            fcntl.flock(stream, fcntl.LOCK_EX)
        if (receipt.parent / f"{provider}.lock").exists():
            raise ValueError("An installation is already active")
        owned = json.loads(receipt.read_text())
        if not isinstance(owned, dict):
            raise TypeError("Invalid installation receipt")
        assets = {}
        content_by_name = (
            {
                f"vibe_scripts/{provider}/{name}": Path(__file__)
                .with_name(name)
                .read_bytes()
                for name in (
                    "ai-quality-wrapper.py",
                    "agent-check-edited.py",
                    "agent-verify.sh",
                    "test-verification.py",
                    "project-memory.py",
                    "write-git-add.py",
                )
            }
            if reporting
            else {
                f"vibe_scripts/{provider}/project-memory.py": Path(__file__)
                .with_name("project-memory.py")
                .read_bytes()
            }
            if memory_hook
            else quality_assets(root, provider)
            if quality
            else {
                f"vibe_scripts/{provider}/usage-tracker.{suffix}": (
                    Path(__file__).parent / f"usage-tracker.{suffix}"
                ).read_bytes()
                for suffix in ("py", "patch", "md")
            }
        )
        if reporting:
            guidance_name = f"agent/{provider}/procedures.md"
            guidance = safe(root, guidance_name)
            if guidance is not None and guidance.is_file():
                content = guidance.read_text()
                if "write-git-add.py" not in content:
                    content += f"\nAfter reviewing manifest paths, run `python3 vibe_scripts/{provider}/write-git-add.py < /path/to/reviewed-manifest.txt`. This writes git-add.txt and a timestamped copy under notes/git-add-history/, preserving the previous recommendation. Exclude history from staging.\n"
                content_by_name[guidance_name] = content.encode()
        for name, content in content_by_name.items():
            destination = safe(root, name)
            previous = (
                previous_source / Path(name).name
                if previous_source
                and name
                in {
                    f"vibe_scripts/{provider}/{asset}"
                    for asset in (
                        "agent-check-edited.py",
                        "agent-verify.sh",
                        "ai-quality-wrapper.py",
                        "quality-hook.py",
                        "test-verification.py",
                        "project-memory.py",
                    )
                }
                else None
            )
            matches_previous = bool(
                (quality or reporting)
                and destination
                and destination.is_file()
                and previous
                and previous.is_file()
                and destination.read_bytes() == previous.read_bytes()
            )
            if (
                quality
                and previous_source
                and name == ".codex/hooks/pre_tool_use_policy.py"
                and destination
                and destination.is_file()
            ):
                old_installer = (
                    previous_source.parents[1] / "CodexHelper/MacOS/part1.sh"
                )
                if old_installer.is_file():
                    old_hook = (
                        old_installer.read_text()
                        .split("cat <<'EOF_PRE_HOOK'\n", 1)[1]
                        .split("\nEOF_PRE_HOOK", 1)[0]
                        + "\n"
                    )
                    installed_hook = re.sub(
                        r"(?m)^# CodexHelper-Version: [^\n]+\n",
                        "",
                        destination.read_text(),
                    )
                    matches_previous = installed_hook == old_hook
            if (
                quality
                and name == f"vibe_scripts/{provider}/Makefile"
                and destination
                and destination.is_file()
                and reviewed_makefile_sha256
            ):
                matches_previous = digest(destination) == reviewed_makefile_sha256
            if destination is None or not (
                resource_writable(root, provider, name) or matches_previous
            ):
                raise ValueError(
                    f"Preserving modified or symlinked managed asset: {name}"
                )
            assets[name] = (
                destination,
                content,
            )
        if dry_run:
            print(f"{label} preview: {provider}, {len(assets)} assets, {root}")
            return
        # Back up only authorized assets; the ordinary installer owns all others.
        previous = {
            path: path.read_bytes() if path.exists() else None
            for path, _ in assets.values()
        }
        old_receipt = receipt.read_bytes()
        if reporting:
            backup = receipt.parent / f"reporting-backup-{provider}-{time.time_ns()}"
            backup.mkdir()
            for path, content in previous.items():
                if content is not None:
                    target = backup / "files" / path.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    target.chmod(path.stat().st_mode & 0o777)
            (backup / "receipt.json").write_bytes(old_receipt)
            created = [
                str(path.relative_to(root))
                for path, content in previous.items()
                if content is None
            ]
            (backup / "created.json").write_text(json.dumps(created, indent=2) + "\n")
            (backup / "RESTORE.txt").write_text(
                f"Copy files/ back into {root}, preserving relative paths and permissions, then restore receipt.json to {receipt}. Remove only unchanged migration-created files listed in created.json. Stop agent sessions first.\n"
            )
            print(f"Reporting backup: {backup}")
        try:
            for name, (path, content) in assets.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                if (
                    (quality or reporting)
                    and path.name == "agent-verify.sh"
                    and previous[path] is None
                ):
                    path.chmod(0o755)
                owned[name] = digest(path)
            receipt.write_text(json.dumps(owned, indent=2) + "\n")
        except OSError:
            for path, content in previous.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            receipt.write_bytes(old_receipt)
            raise
        print(f"{label} refreshed: {provider}, {len(assets)} assets, {root}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=[
            "begin",
            "finish",
            "abort",
            "cleanup",
            "migrate",
            "merge-codex",
            "resource-writable",
            "usage-tracker",
            "quality-refresh",
            "reporting-refresh",
            "memory-hook-refresh",
            "memory-refresh",
            "memory-discover",
        ],
    )
    parser.add_argument("--provider", choices=["codex", "claude"], required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--hooks-skipped", action="store_true")
    parser.add_argument("--run-id", default=str(os.getppid()))
    parser.add_argument("--skipped", action="append", default=[])
    parser.add_argument("--quality-only", action="store_true")
    parser.add_argument("--path")
    parser.add_argument(
        "--previous-source",
        type=Path,
        help="Explicit reviewed previous quality source; permits only exact byte matches",
    )
    parser.add_argument(
        "--reviewed-makefile-sha256",
        help="Explicit hash of the reviewed quality Makefile for a stale receipt",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.operation == "memory-discover":
        print(json.dumps(memory_projects(), indent=2))
        return
    if args.operation == "memory-refresh":
        started = time.monotonic()
        result = memory_refresh(root, args.provider, args.dry_run, args.previous_source)
        if not args.dry_run:
            log = Path(result["backup"]) / "refresh.log"
            command = [
                sys.executable,
                str(root / f"vibe_scripts/{args.provider}/project-memory.py"),
                "refresh",
                "--root",
                str(root),
                "--architecture",
            ]
            with log.open("w") as stream:
                completed = subprocess.run(
                    command, stdout=stream, stderr=subprocess.STDOUT, check=False
                )
            state_path = root / ".cache/project-memory/state.json"
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            result.update(
                refresh_exit_code=completed.returncode,
                log=str(log),
                duration_seconds=round(time.monotonic() - started, 3),
                coverage={
                    key: {
                        name: len(value) if isinstance(value, (list, dict)) else value
                        for name, value in coverage.items()
                    }
                    for key, coverage in state.get("coverage", {}).items()
                },
                errors=state.get("errors", {}),
            )
            (Path(result["backup"]) / "result.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
        print(json.dumps(result, indent=2))
        if result.get("refresh_exit_code"):
            raise SystemExit(result["refresh_exit_code"])
        return
    if args.operation in {
        "usage-tracker",
        "quality-refresh",
        "memory-hook-refresh",
        "reporting-refresh",
    }:
        install_usage_tracker(
            root,
            args.provider,
            args.dry_run,
            quality=args.operation == "quality-refresh",
            memory_hook=args.operation == "memory-hook-refresh",
            reporting=args.operation == "reporting-refresh",
            previous_source=args.previous_source,
            reviewed_makefile_sha256=args.reviewed_makefile_sha256,
        )
        return
    if args.operation == "resource-writable":
        if not args.path or not resource_writable(root, args.provider, args.path):
            raise SystemExit(1)
        return
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
        if args.operation == "finish":
            configure_markdownlint(root, dry_run=True)
            configure_test_verification(root, args.provider, dry_run=True)
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
        configure_markdownlint(root)
        configure_test_verification(root, args.provider)
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
