#!/usr/bin/env bash
set -Eeuo pipefail
IFS='
	'

MODE="${1:-edited}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [[ "$(basename -- "$SCRIPT_DIR")" == "codex" || "$(basename -- "$SCRIPT_DIR")" == "claude" ]]; then
	ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
fi
shift "$(($# > 0 ? 1 : 0))"
if [[ "$MODE" != "test" && "$MODE" != "fallow" && "$MODE" != "edited" && "$MODE" != "lint" && "$#" -gt 0 ]]; then
	echo "Only edited, lint, test and fallow accept additional scope arguments" >&2
	exit 2
fi
MAKE_ARGS=()
if [[ -f "$SCRIPT_DIR/Makefile" ]]; then
	MAKE_ARGS=(-f "$SCRIPT_DIR/Makefile")
fi
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

run_if_available() {
	local name="$1"
	shift
	if command -v "$name" >/dev/null 2>&1; then
		echo "+ $*"
		"$@"
	else
		echo "- $name not found; skipping"
	fi
}

if [[ "$MODE" == "fallow" ]]; then
	python3 "$SCRIPT_DIR/fallow.py" report "$@"
elif [[ "$MODE" == "edited" ]]; then
	if [[ "$#" -gt 0 ]]; then
		export VIBEHELPER_EDITED_FILES_JSON
		VIBEHELPER_EDITED_FILES_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")"
	fi
	make "${MAKE_ARGS[@]}" edited-ai
elif [[ "$MODE" == "lint" ]]; then
	if [[ "${1:-}" == "--all" ]]; then
		[[ "$#" -eq 1 ]] || {
			echo "--all cannot be combined with paths" >&2
			exit 2
		}
		make "${MAKE_ARGS[@]}" lint-all-ai
	else
		python3 "$SCRIPT_DIR/agent-check-edited.py" --mode lint -- "$@"
	fi
elif [[ "$MODE" == "typecheck" ]]; then
	make "${MAKE_ARGS[@]}" typecheck-ai
elif [[ "$MODE" == "test" ]]; then
	if [[ "$#" -gt 0 ]]; then
		python3 - "$SCRIPT_DIR" "$@" <<'PY_TARGET'
import re
import subprocess
import sys
from pathlib import Path

root = Path.cwd().resolve()
targets = sys.argv[2:]
for target in targets:
    path_part, *identifiers = target.split("::")
    path = Path(path_part)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or not (path.parts[0] == "tests" or path.parts[:2] in (("CodexHelper", "tests"), ("ClaudeHelper", "tests")))
        or not path.exists()
        or not path.resolve().is_relative_to(root)
        or (path.is_file() and path.suffix != ".py")
        or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\[[^\r\n]+\])?", item) for item in identifiers)
    ):
        print(f"Invalid test path or identifier: {target!r}", file=sys.stderr)
        sys.exit(2)
wrapper = Path(sys.argv[1]) / "ai-quality-wrapper.py"
sys.exit(subprocess.run([
    sys.executable, str(wrapper), "--label", "test-pytest-targeted",
    "--adapter", "rtk:pytest", "--max-lines", "30", "--timeout", "600", "--",
    sys.executable, "-m", "pytest", "-q", "--", *targets,
], check=False).returncode)
PY_TARGET
	else
		make "${MAKE_ARGS[@]}" test-ai
	fi
elif [[ "$MODE" == "security" ]]; then
	make "${MAKE_ARGS[@]}" security-ai
elif [[ "$MODE" == "graph" ]]; then
	run_if_available code-review-graph code-review-graph detect-changes --brief
elif [[ "$MODE" == "all" || "$MODE" == "verify" ]]; then
	if command -v code-review-graph >/dev/null 2>&1; then
		code-review-graph detect-changes --brief || true
	fi
	make "${MAKE_ARGS[@]}" verify-ai
else
	echo "Unknown mode: $MODE" >&2
	echo "Usage: $0 [edited|all|verify|lint|typecheck|test|security|graph|fallow (--files PATH...|--all)]" >&2
	exit 2
fi
