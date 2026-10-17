"""Install project memory hooks without replacing unrelated configuration."""

import argparse
import copy
import json
import re
import shutil
import sys
import time
from pathlib import Path

START = "# >>> VibeHelper project memory"
END = "# <<< VibeHelper project memory"


def command(provider):
    return f'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; helper="$root/vibe_scripts/{provider}/project-memory.py"; [ -f "$helper" ] || exit 0; exec python3 "$helper" hook --provider {provider} --root "$root"'


def desired(provider):
    return {
        event: [
            {
                "matcher": matcher,
                "hooks": [
                    {"type": "command", "command": command(provider), "timeout": 100}
                ],
            }
        ]
        for event, matcher in {
            "SessionStart": ".*",
            "PostToolUse": "^(Bash|Edit|Write|NotebookEdit|apply_patch|exec_command|write_stdin)$",
            "PreToolUse": "^mcp__.*(?:code[-_]review[-_]graph|graphify).*",
            "Stop": ".*",
        }.items()
    }


def legacy_commands(root):
    graph_first = 'root="${CLAUDE_PROJECT_DIR:-}"; [ -n "$root" ] || root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"; hook="$root/.claude/hooks/graph_first_policy.py"; [ -f "$hook" ] || exit 0; exec python3 "$hook"'
    return {
        graph_first,
        f'cat >/dev/null || true; git rev-parse --git-dir >/dev/null 2>&1 && code-review-graph update --skip-flows --repo "{root}" || true',
        "cat >/dev/null || true; git rev-parse --git-dir >/dev/null 2>&1 && code-review-graph update --skip-flows || true",
        f"cat >/dev/null || true; git rev-parse --git-dir >/dev/null 2>&1 && code-review-graph status --repo \"{root}\" || echo 'Not a git repo, skipping'",
        "cat >/dev/null || true; git rev-parse --git-dir >/dev/null 2>&1 && code-review-graph status || echo 'Not a git repo, skipping'",
    }


def update_hooks(hooks, root, provider, install=True):
    result = copy.deepcopy(hooks)
    for event, groups in result.items():
        retained = []
        for group in groups:
            original = group.get("hooks", [])
            group["hooks"] = [
                hook
                for hook in original
                if hook.get("command")
                not in legacy_commands(root) | {command(provider)}
            ]
            if group["hooks"] or not original:
                retained.append(group)
        result[event] = retained
    if install:
        for event, groups in desired(provider).items():
            result.setdefault(event, []).extend(groups)
    return result


def toml_value(value):
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(json.dumps(k) + " = " + toml_value(v) for k, v in value.items())
            + " }"
        )
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return json.dumps(value)


def save(path, text):
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink: {path}")
    if path.exists() and path.read_text() == text:
        return
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + f".bak.{time.time_ns()}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def configure(path, root, provider, install=True):
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Configuration path leaves repository: {path}")
    original = path.read_text() if path.exists() else ""
    if path.suffix == ".json":
        data = json.loads(original or "{}")
        data["hooks"] = update_hooks(data.get("hooks", {}), root, provider, install)
        save(path, json.dumps(data, indent=2) + "\n")
        return
    import tomllib

    data = tomllib.loads(original)
    expected = copy.deepcopy(data)
    expected["hooks"] = update_hooks(data.get("hooks", {}), root, provider, install)
    # Preserve all unrelated TOML text. Rewrite only recognized hook groups;
    # validation below refuses uncommon layouts that cannot be preserved.
    pattern = r'(?m)^\[\[hooks\.(?:"([^"]+)"|([\w]+))\]\][^\n]*\n.*?(?=^\[(?!\[?hooks\.(?:"[^"\n]+"|[\w]+)\.hooks)|\Z)'

    def remove(match):
        event = match[1] or match[2]
        groups = tomllib.loads(match[0])["hooks"][event]
        if any(
            h.get("command") in legacy_commands(root) | {command(provider)}
            for g in groups
            for h in g.get("hooks", [])
        ):
            kept = update_hooks({event: groups}, root, provider, False)[event]
            return "".join(render(event, group) for group in kept)
        return match[0]

    text = re.sub(pattern, remove, original, flags=re.DOTALL)
    if install:
        for event, groups in desired(provider).items():
            text = (
                text.rstrip()
                + "\n\n"
                + "".join(render(event, group) for group in groups)
            )
    # Empty event arrays and absent arrays are equivalent; normalize for comparison.
    parsed = tomllib.loads(text)
    for doc in (parsed, expected):
        if "hooks" in doc:
            doc["hooks"] = {k: v for k, v in doc["hooks"].items() if v}
    if parsed != expected:
        raise ValueError(f"Unsupported hook TOML layout; preserved {path}")
    save(path, text)


def render(event, group):
    return (
        f"[[hooks.{json.dumps(event)}]]\n"
        + "\n".join(json.dumps(k) + " = " + toml_value(v) for k, v in group.items())
        + "\n"
    )


def install(root, provider, hooks=True, crg=True, graphify=True):
    helper = root / f"vibe_scripts/{provider}"
    for path in (helper, root / ".graphifyignore", root / f".{provider}"):
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Memory installation path leaves repository: {path}")
    save(
        helper / "memory-policy.json",
        json.dumps(
            {
                "_managed": provider.title() + "Helper-Version: project-memory-v1",
                "hooks": hooks,
                "crg": crg,
                "graphify": graphify,
            },
            indent=2,
        )
        + "\n",
    )
    if hooks:
        target = root / (
            ".codex/config.toml"
            if provider == "codex"
            else ".claude/settings.local.json"
        )
        configure(target, root, provider)
        secondary = root / (
            ".codex/config-snippet.toml"
            if provider == "codex"
            else ".claude/settings.json"
        )
        if secondary.exists():
            configure(secondary, root, provider, install=provider == "codex")
    ignore = root / ".graphifyignore"
    if graphify:
        text = ignore.read_text() if ignore.exists() else ""
        block = (
            START
            + "\ngraphify-out/\n.code-review-graph/\n.cache/\nnotes/\nobsidian/\n"
            + END
        )
        text = re.sub(
            re.escape(START) + ".*?" + re.escape(END), "", text, flags=re.DOTALL
        ).rstrip()
        save(ignore, text + "\n\n" + block + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=("codex", "claude"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--hooks", type=int, choices=(0, 1), default=1)
    parser.add_argument("--crg", type=int, choices=(0, 1), default=1)
    parser.add_argument("--graphify", type=int, choices=(0, 1), default=1)
    parser.add_argument("--template-json", action="store_true")
    args = parser.parse_args()
    if args.template_json:
        template = json.load(sys.stdin)
        template["hooks"] = update_hooks(
            template.get("hooks", {}), args.root.resolve(), args.provider
        )
        print(json.dumps(template, indent=2))
    else:
        install(
            args.root.resolve(),
            args.provider,
            bool(args.hooks),
            bool(args.crg),
            bool(args.graphify),
        )
