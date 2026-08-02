#!/usr/bin/env bash
# Wire AI-safe quality targets for a repository already bootstrapped by WSL/part1.sh.
#
# Version: 2026-07-27-v4

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_NAME="$(basename "$0")"
DRY_RUN=0
FORCE=0
WIRE=0
REPO_ROOT=""

log() { printf '\033[1;34m[claude-quality]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

usage() {
	cat <<USAGE
Usage: $SCRIPT_NAME [options]

Options:
  --dry-run       Show what would happen without writing files.
  --wire          Write/update the managed Makefile block.
  --force         Replace an existing ClaudeHelper managed Makefile block.
  --repo PATH     Run against a specific repo/path.
  -h, --help      Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--wire)
		WIRE=1
		shift
		;;
	--force)
		FORCE=1
		shift
		;;
	--repo)
		REPO_ROOT="${2:-}"
		if [[ -z "$REPO_ROOT" ]]; then
			err "--repo requires a path"
			exit 2
		fi
		shift 2
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		err "Unknown option: $1"
		usage
		exit 2
		;;
	esac
done

repo_root() {
	if [[ -n "$REPO_ROOT" ]]; then
		cd "$REPO_ROOT"
	fi
	if command -v git >/dev/null 2>&1 && git rev-parse --show-toplevel >/dev/null 2>&1; then
		git rev-parse --show-toplevel
	else
		pwd
	fi
}

cmd_exists() { command -v "$1" >/dev/null 2>&1; }
has_npm_script() {
	local name="$1"
	[[ -f package.json ]] || return 1
	python3 - "$name" <<'PY'
import json
import sys
from pathlib import Path

name = sys.argv[1]
try:
    data = json.loads(Path("package.json").read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
scripts = data.get("scripts")
raise SystemExit(0 if isinstance(scripts, dict) and isinstance(scripts.get(name), str) else 1)
PY
}

has_markdown_files() {
	local match
	match="$(find . \
		\( -name .agents -o -name .claude -o -name .codex -o -name node_modules -o -name vendor -o -name dist -o -name build -o -name coverage -o -name graphify-out -o -name .git -o -name .cache -o -name .venv \) -prune \
		-o \( -name '*.md' -o -name '*.markdown' \) -print -quit 2>/dev/null)"
	[[ -n "$match" ]]
}

markdownlint_globs() {
	printf '"**/*.md" "**/*.markdown" "#**/.agents/**" "#**/.claude/**" "#**/.codex/**" "#**/node_modules/**" "#**/vendor/**" "#**/dist/**" "#**/build/**" "#**/coverage/**" "#**/graphify-out/**" "#**/.git/**" "#**/.cache/**" "#**/.venv/**" "#**/obsidian/**"'
}

ROOT="$(repo_root)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
log "Working in repo/root: $ROOT"
[[ "$DRY_RUN" -eq 1 ]] && warn "Dry run: no files will be written."

if [[ "$WIRE" -ne 1 && "$DRY_RUN" -ne 1 ]]; then
	warn "No changes requested. Pass --wire to write Makefile targets."
	exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
	err "python3 is required to inspect project scripts and run ClaudeHelper targets."
	exit 1
fi

make_target() {
	local target="$1"
	local body="$2"
	printf '.PHONY: %s\n%s:\n%s\n\n' "$target" "$target" "$body"
}

helper_targets_expected() {
	[[ "${CLAUDEHELPER_UNIFIED_INSTALL:-0}" == "1" ]]
}

wrapper='python3 vibe_scripts/ai-quality-wrapper.py'
targets=()
verify_deps=()
helper_expected=0
helper_targets_expected && helper_expected=1

if [[ -f vibe_scripts/agent-check-edited.py || "$helper_expected" -eq 1 ]]; then
	targets+=("$(make_target edited-ai $'\t@python3 vibe_scripts/agent-check-edited.py')")
	verify_deps+=("edited-ai")
fi

if [[ -f vibe_scripts/update-codebase-wiki.py || -d codebase-wiki || "$helper_expected" -eq 1 ]]; then
	targets+=("$(make_target wiki-ai $'\t@python3 vibe_scripts/update-codebase-wiki.py')")
fi

if [[ -f vibe_scripts/sync-skills.py || "$helper_expected" -eq 1 ]]; then
	targets+=("$(make_target skills-check $'\t@python3 vibe_scripts/sync-skills.py --check')")
	targets+=("$(make_target skills-update $'\t@python3 vibe_scripts/sync-skills.py --update')")
fi

if [[ -f vibe_scripts/agent-verify.sh || "$helper_expected" -eq 1 ]]; then
	targets+=("$(make_target agent-verify $'\t@./vibe_scripts/agent-verify.sh')")
fi

lint_commands=()
if has_npm_script lint; then
	lint_commands+=("$wrapper --label lint-npm --max-lines 30 -- npm run lint")
fi
if cmd_exists shellcheck; then
	lint_commands+=("$wrapper --label lint-shell --max-lines 30 --shell -- 'find . \\( -name .agents -o -name .claude -o -name .codex -o -name node_modules -o -name vendor -o -name dist -o -name build -o -name coverage -o -name graphify-out -o -name .git -o -name .cache -o -name .venv \\) -prune -o -name \"*.sh\" -print0 | xargs -0 -r shellcheck'")
fi
if cmd_exists markdownlint-cli2 && has_markdown_files; then
	lint_commands+=("$wrapper --label lint-markdown --max-lines 30 --shell -- 'markdownlint-cli2 $(markdownlint_globs)'")
fi
if [[ "${#lint_commands[@]}" -gt 0 ]]; then
	body=""
	for command in "${lint_commands[@]}"; do
		body+=$'\t@'"$command"$'\n'
	done
	targets+=("$(make_target lint-ai "$body")")
	verify_deps+=("lint-ai")
fi

if has_npm_script typecheck; then
	targets+=("$(make_target typecheck-ai $'\t@python3 vibe_scripts/ai-quality-wrapper.py --label typecheck-npm --max-lines 40 -- npm run typecheck')")
	verify_deps+=("typecheck-ai")
fi

if has_npm_script test; then
	targets+=("$(make_target test-ai $'\t@python3 vibe_scripts/ai-quality-wrapper.py --label test-npm --max-lines 40 -- npm test')")
	verify_deps+=("test-ai")
fi

if cmd_exists semgrep; then
	targets+=("$(make_target security-ai $'\t@mkdir -p .cache/semgrep\n\t@python3 vibe_scripts/ai-quality-wrapper.py --label security-semgrep --max-lines 40 -- semgrep scan')")
	verify_deps+=("security-ai")
fi

if [[ "${#verify_deps[@]}" -gt 0 ]]; then
	body=""
	for dep in "${verify_deps[@]}"; do
		body+=$'\t@$(MAKE) '"$dep"$'\n'
	done
	targets+=("$(make_target verify-ai "$body")")
fi

if [[ "${#targets[@]}" -eq 0 ]]; then
	warn "No AI-safe targets could be generated. Run part1.sh first or install project quality tools."
	exit 0
fi

block_start="# >>> ClaudeHelper managed targets"
block_end="# <<< ClaudeHelper managed targets"
block="$block_start"$'\n'"# ClaudeHelper-Version: 2026-07-27-v4"$'\n\n'
for target in "${targets[@]}"; do
	block+="$target"$'\n\n'
done
block+="$block_end"$'\n'

if [[ "$DRY_RUN" -eq 1 ]]; then
	printf '\n%s\n' "$block"
	exit 0
fi

existing_block_count=0
if [[ -f Makefile ]]; then
	existing_block_count="$(grep -cF "$block_start" Makefile || true)"
fi
if [[ "$existing_block_count" -gt 1 ]]; then
	err "Makefile contains multiple ClaudeHelper blocks; resolve them manually before wiring."
	exit 1
fi

if [[ "$existing_block_count" -eq 1 ]]; then
	current_block="$(awk -v start="$block_start" -v end="$block_end" '
		$0 == start { capture = 1 }
		capture { print }
		$0 == end { capture = 0 }
	' Makefile)"
	if [[ "$current_block" == "${block%$'\n'}" ]]; then
		log "Makefile ClaudeHelper targets are already current."
		exit 0
	fi
fi

if [[ -f Makefile && "$FORCE" -ne 1 && "$existing_block_count" -gt 0 ]]; then
	warn "Makefile has an older ClaudeHelper block. Use --force to back it up and replace it."
	exit 0
fi

tmp="$(mktemp)"
if [[ -f Makefile ]]; then
	awk -v start="$block_start" -v end="$block_end" '
		$0 == start { skip = 1; next }
		$0 == end { skip = 0; next }
		!skip { print }
	' Makefile | awk '
		{ lines[NR] = $0 }
		NF { last = NR }
		END { for (line = 1; line <= last; line++) print lines[line] }
	' >"$tmp"
else
	: >"$tmp"
fi
[[ -s "$tmp" ]] && printf '\n' >>"$tmp"
printf '%s' "$block" >>"$tmp"
if [[ -f Makefile ]]; then
	cp Makefile "Makefile.bak.$(date +%Y%m%d-%H%M%S)"
fi
mv "$tmp" Makefile
log "Updated Makefile with ClaudeHelper targets."
