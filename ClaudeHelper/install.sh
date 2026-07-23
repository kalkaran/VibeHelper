#!/usr/bin/env bash
# Unified entry point for the Claude Helper repository and quality setup phases.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
FORCE=0
REPO_ROOT=""
WIRE_MODE="auto"
PART1_ONLY_ARGS=()

log() { printf '\033[1;34m[claude-helper]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

usage() {
	cat <<USAGE
Usage: $SCRIPT_NAME [options]

Runs the matching platform's part1.sh and part2.sh in order. Part 2 runs only
when part 1 succeeds. Makefile wiring is enabled by default, even when Codex
setup is detected.

Options:
  --dry-run       Preview both phases without making changes.
  --force         Back up and refresh managed files (not allowed with Codex).
  --repo PATH     Configure PATH instead of the current repository.
  --wire          Write/update the quality Makefile (default).
  --no-wire       Run part 2 without writing the quality Makefile.
  --no-humanizer  Do not install the Humanizer writing skill in part 1.
  --no-rtk        Do not install RTK or its command-routing hook.
  --no-graphify   Do not install the graphify skill or its guidance.
  --no-ponytail   Do not install the Ponytail plugin.
  --no-crg        Do not install/register code-review-graph.
  --crg-build     Build the code-review-graph index for the repo after install.
  --context7      Run interactive Context7 setup.
  --impeccable    Install the Impeccable design skill/hooks.
  --with-llm-council  Clone karpathy/llm-council locally.
  --repo-only     Only write repo files; skip all global tool installs.
  -h, --help      Show this help.

Examples:
  bash /path/to/ClaudeHelper/install.sh --dry-run
  bash /path/to/ClaudeHelper/install.sh
  bash /path/to/ClaudeHelper/install.sh --repo /path/to/project
  bash /path/to/ClaudeHelper/install.sh --force --no-humanizer
USAGE
}

require_value() {
	local option="$1"
	local value="${2:-}"
	if [[ -z "$value" ]]; then
		err "$option requires a value."
		exit 2
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--force)
		FORCE=1
		shift
		;;
	--repo)
		require_value "$1" "${2:-}"
		REPO_ROOT="$2"
		shift 2
		;;
	--wire)
		WIRE_MODE="wire"
		shift
		;;
	--no-wire)
		WIRE_MODE="no-wire"
		shift
		;;
	--no-humanizer | --no-rtk | --no-graphify | --no-ponytail | --no-crg | --crg-build | --context7 | --impeccable | --with-llm-council | --repo-only | --skip-global | --yes)
		PART1_ONLY_ARGS+=("$1")
		shift
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

detect_platform_dir() {
	local kernel
	kernel="$(uname -s 2>/dev/null || true)"
	case "$kernel" in
	Darwin)
		printf '%s\n' "MacOS"
		;;
	Linux)
		if [[ -n "${WSL_DISTRO_NAME:-}" ]] || { [[ -r /proc/sys/kernel/osrelease ]] && grep -qi microsoft /proc/sys/kernel/osrelease; }; then
			printf '%s\n' "WSL"
		else
			err "Linux was detected, but this installer currently supports Ubuntu WSL only."
			return 1
		fi
		;;
	*)
		err "Unsupported platform: ${kernel:-unknown}. Use macOS or Ubuntu WSL."
		return 1
		;;
	esac
}

PLATFORM_DIR="$(detect_platform_dir)"
PART1="$SCRIPT_DIR/$PLATFORM_DIR/part1.sh"
PART2="$SCRIPT_DIR/$PLATFORM_DIR/part2.sh"

for phase_script in "$PART1" "$PART2"; do
	if [[ ! -f "$phase_script" ]]; then
		err "Required installer phase was not found: $phase_script"
		exit 1
	fi
done

if [[ -n "$REPO_ROOT" ]]; then
	if [[ ! -d "$REPO_ROOT" ]]; then
		err "Repository path does not exist: $REPO_ROOT"
		exit 2
	fi
	cd "$REPO_ROOT"
fi

codex_setup_present() {
	[[ -d .codex ]] ||
		{ [[ -f AGENTS.md ]] && grep -Fq "CodexHelper-Version:" AGENTS.md; } ||
		{ [[ -f Makefile ]] && grep -Fq "CodexHelper-Version:" Makefile; }
}

CODEX_SETUP=0
if codex_setup_present; then
	CODEX_SETUP=1
fi
if [[ "$CODEX_SETUP" -eq 1 && "$FORCE" -eq 1 ]]; then
	err "--force is unsafe with detected Codex setup because the helpers share managed files."
	err "Rerun without --force so existing shared files are preserved."
	exit 2
fi

if [[ "$WIRE_MODE" == "auto" ]]; then
	WIRE_MODE="wire"
	if [[ "$CODEX_SETUP" -eq 1 ]]; then
		log "Codex project setup detected; wiring the shared Makefile anyway (pass --no-wire to skip)."
	fi
fi

PART1_ARGS=()
PART2_ARGS=()

if [[ "$DRY_RUN" -eq 1 ]]; then
	PART1_ARGS+=("--dry-run")
	PART2_ARGS+=("--dry-run")
fi
if [[ "$FORCE" -eq 1 ]]; then
	PART1_ARGS+=("--force")
	PART2_ARGS+=("--force")
fi
if [[ "${#PART1_ONLY_ARGS[@]}" -gt 0 ]]; then
	PART1_ARGS+=("${PART1_ONLY_ARGS[@]}")
fi
if [[ "$WIRE_MODE" == "wire" ]]; then
	PART2_ARGS+=("--wire")
fi

run_part1() {
	if [[ "${#PART1_ARGS[@]}" -gt 0 ]]; then
		CLAUDE_HELPER_ORCHESTRATED=1 bash "$PART1" "${PART1_ARGS[@]}"
	else
		CLAUDE_HELPER_ORCHESTRATED=1 bash "$PART1"
	fi
}

run_part2() {
	if [[ "${#PART2_ARGS[@]}" -gt 0 ]]; then
		bash "$PART2" "${PART2_ARGS[@]}"
	else
		bash "$PART2"
	fi
}

log "Platform: $PLATFORM_DIR"
log "Target repository: $(pwd)"
log "Running repository workflow setup (part 1 of 2)."
if run_part1; then
	log "Part 1 completed."
else
	status=$?
	err "Part 1 failed with exit code $status. Part 2 was not run."
	exit "$status"
fi

log "Running quality-tool detection and wiring (part 2 of 2)."
if run_part2; then
	log "Part 2 completed."
else
	status=$?
	err "Part 2 failed with exit code $status."
	exit "$status"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
	log "Preview complete. No changes were made."
else
	log "Setup complete. Restart Claude Code in this repository."
fi
