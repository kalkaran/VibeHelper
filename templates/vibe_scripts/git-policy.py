"""Build the standalone Git mutation hook without project quality restrictions."""

import ast
import sys
from pathlib import Path


def build_policy(source):
    names = {
        "ASSIGNMENT",
        "CONTROL_TOKENS",
        "ENV_OPTIONS_WITH_VALUES",
        "SUDO_OPTIONS_WITH_VALUES",
        "XARGS_OPTIONS_WITH_VALUES",
        "blocked",
        "command_name",
        "shell_segments",
        "strip_prefixes",
        "xargs_child",
        "find_command",
        "mutation_reason",
    }
    body = []
    for node in ast.parse(source).body:
        name = (
            node.name
            if isinstance(node, ast.FunctionDef)
            else node.targets[0].id
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            else None
        )
        if name in names:
            segment = ast.get_source_segment(source, node)
            if name == "mutation_reason":
                # Preserve the old conservative checks for dynamic shell execution.
                segment = segment.replace(
                    "    segments = shell_segments(command)",
                    '    if "$(" in command or "`" in command or re.search(r"(?:^|[;&|\\n])\\s*(?:if\\b|then\\b|do\\b|else\\b|elif\\b|while\\b|until\\b|for\\b|case\\b|function\\b|exec\\b|!|\\{)", command):\n'
                    "        for pattern, reason in blocked:\n"
                    "            if re.search(pattern, command):\n"
                    "                return reason\n"
                    "    segments = shell_segments(command)",
                    1,
                )
            body.append(segment)
    if len(body) != len(names):
        raise ValueError("installer command policy layout changed")
    return (
        "#!/usr/bin/env python3\n# CodexHelper-Version: git-policy-command-v1\nimport json, os, re, shlex, sys\nfrom typing import Any\n\n"
        + "\n\n".join(body)
        + """

try:
    payload = json.load(sys.stdin)
except (ValueError, OSError):
    payload = {}
reason = mutation_reason(find_command(payload))
output = {"hookEventName": "PreToolUse"}
if reason:
    output.update(permissionDecision="deny", permissionDecisionReason=reason)
print(json.dumps({"hookSpecificOutput": output}))
"""
    )


if __name__ == "__main__":
    print(build_policy(Path(sys.argv[1]).read_text()), end="")
