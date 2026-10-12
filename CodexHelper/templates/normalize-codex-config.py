"""Reconcile installer integrations with Codex's TOML configuration."""

import argparse
import copy
import datetime
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        raise SystemExit(
            "Codex config normalization requires Python 3.11+ or an installed tomli parser; files left unchanged."
        ) from None

START = "# >>> CodexHelper imported hooks"
END = "# <<< CodexHelper imported hooks"


def imported_block(text):
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip() == START]
    ends = [i for i, line in enumerate(lines) if line.rstrip() == END]
    if not starts and not ends:
        return ""
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise ValueError("Invalid imported-hook markers; configuration left unchanged")
    return "".join(lines[starts[0] : ends[0] + 1])


def toml_value(value):
    if isinstance(value, (str, bool, int)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                json.dumps(key) + " = " + toml_value(item)
                for key, item in value.items()
            )
            + " }"
        )
    raise ValueError(f"Unsupported hook value: {value!r}; configuration left unchanged")


def normalize(config, context_only=False, check=False):
    hooks_file = config.with_name("hooks.json")
    if config.is_symlink() or (not context_only and hooks_file.is_symlink()):
        raise ValueError("Refusing to replace symlinked Codex configuration")
    original = config.read_text() if config.exists() else ""
    expected = tomllib.loads(original)
    text = original
    context = expected.get("mcp_servers", {}).get("context7", {})
    if "type" in context:
        del context["type"]
        # Validate the complete parsed result instead of guessing TOML table boundaries.
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if "type" not in line.split("=", 1)[0]:
                continue
            candidate = "".join(lines[:index] + lines[index + 1 :])
            try:
                if tomllib.loads(candidate) == expected:
                    text = candidate
                    break
            except tomllib.TOMLDecodeError:
                continue
        else:
            raise ValueError(
                "Cannot safely remove Context7 type; configuration left unchanged"
            )

    migrate = not context_only and hooks_file.exists()
    if migrate:
        payload = json.loads(hooks_file.read_text())
        if not isinstance(payload, dict) or set(payload) - {"hooks", "description"}:
            raise ValueError(
                "Unsupported hooks.json fields; configuration left unchanged"
            )
        events = payload.get("hooks", {})
        if not isinstance(events, dict):
            raise ValueError("hooks.json hooks must be an object")
        block = imported_block(text)
        additions = []
        for event, groups in events.items():
            if not isinstance(groups, list) or event == "state":
                raise ValueError(
                    "Invalid hook event groups; configuration left unchanged"
                )
            if not groups:
                continue
            existing = expected.setdefault("hooks", {}).setdefault(event, [])
            for group in groups:
                if not isinstance(group, dict) or not isinstance(
                    group.get("hooks"), list
                ):
                    raise TypeError("Invalid hook group; configuration left unchanged")
                if group not in existing:
                    additions.append("\n[[hooks." + json.dumps(event) + "]]\n")
                    additions.extend(
                        json.dumps(key) + " = " + toml_value(value) + "\n"
                        for key, value in group.items()
                    )
                    existing.append(copy.deepcopy(group))
        if additions:
            if block:
                replacement = (
                    block[: block.rfind(END)] + "".join(additions) + END + "\n"
                )
                text = text.replace(block, replacement, 1)
            else:
                text = (
                    text.rstrip()
                    + "\n\n"
                    + START
                    + "\n"
                    + "".join(additions)
                    + END
                    + "\n"
                )
    if tomllib.loads(text) != expected:
        raise ValueError("Configuration verification failed; files left unchanged")
    if check or (text == original and not migrate):
        return

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    if text != original:
        config.parent.mkdir(parents=True, exist_ok=True)
        mode = config.stat().st_mode & 0o777 if config.exists() else 0o600
        if config.exists():
            shutil.copy2(config, config.with_name(config.name + ".bak." + stamp))
        descriptor, temporary = tempfile.mkstemp(dir=config.parent, prefix=".config-")
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(text)
            os.chmod(temporary, mode)
            os.replace(temporary, config)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    if migrate:
        hooks_file.rename(hooks_file.with_name("hooks.json.bak." + stamp))
        print(f"Consolidated hooks in {config}; review moved hooks in Codex /hooks.")
    elif text != original:
        print(f"Removed unsupported Context7 type from {config}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--context-only", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--preserved-block", action="store_true")
    args = parser.parse_args()
    try:
        if args.preserved_block:
            text = args.config.read_text() if args.config.exists() else ""
            tomllib.loads(text)
            print(imported_block(text), end="")
        else:
            normalize(args.config, args.context_only, args.check)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"Codex config normalization failed: {error}\n")
