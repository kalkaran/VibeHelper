#!/usr/bin/env bash
set -Eeuo pipefail
IFS='
	'

MODE="${1:-edited}"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"

changed_files() {
	if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		git diff --name-only --diff-filter=ACMRTUXB HEAD 2>/dev/null || git diff --name-only --diff-filter=ACMRTUXB
	fi
}

print_changed_files_summary() {
	local tmp count limit=30
	tmp="$(mktemp)"
	changed_files >"$tmp" || true
	count="$(wc -l <"$tmp" | tr -d ' ')"
	if [[ "$count" -eq 0 ]]; then
		echo "  none"
	elif [[ "$count" -le "$limit" ]]; then
		sed 's/^/  - /' "$tmp"
	else
		head -n "$limit" "$tmp" | sed 's/^/  - /'
		echo "  ... $((count - limit)) more changed files hidden from AI output"
	fi
	rm -f "$tmp"
}

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

echo "Agent verification from: $ROOT"
echo "Mode: $MODE"
echo

echo "Changed files:"
print_changed_files_summary
echo

if [[ "$MODE" == "edited" ]]; then
	make edited-ai
elif [[ "$MODE" == "lint" ]]; then
	make lint-ai
elif [[ "$MODE" == "typecheck" ]]; then
	make typecheck-ai
elif [[ "$MODE" == "test" ]]; then
	make test-ai
elif [[ "$MODE" == "security" ]]; then
	make security-ai
elif [[ "$MODE" == "graph" ]]; then
	run_if_available code-review-graph code-review-graph detect-changes --brief
elif [[ "$MODE" == "all" || "$MODE" == "verify" ]]; then
	if command -v code-review-graph >/dev/null 2>&1; then
		code-review-graph detect-changes --brief || true
	fi
	make verify-ai
else
	echo "Unknown mode: $MODE" >&2
	echo "Usage: $0 [edited|all|verify|lint|typecheck|test|security|graph]" >&2
	exit 2
fi
