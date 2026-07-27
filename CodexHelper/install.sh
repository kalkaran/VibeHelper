#!/usr/bin/env bash
# Unified entry point for the Codex Helper repository and quality setup phases.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
YES=0
FORCE=0
FRESH_INSTALL=0
REPO_ONLY=0
REPO_ROOT=""
CODEX_CONFIG_FLAG="--no-apply-codex-config"
QUALITY_INSTALL_MODE=""
QUALITY_WIRE_MODE=""
QUALITY_FIX_MODE=""
PART1_ONLY_ARGS=()

log() { printf '\033[1;34m[codex-helper]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

usage() {
	cat <<USAGE
Usage: $SCRIPT_NAME [options]

Runs the matching platform's part1.sh and part2.sh in order. Part 2 runs only
when part 1 succeeds.

Options:
  --dry-run             Preview both phases without making changes.
  --yes, -y             Accept non-interactive defaults in both phases.
  --fresh-install       Refresh managed files and install/wire missing tools.
  --force               Back up and refresh managed files in both phases.
  --repo PATH           Configure PATH instead of the current repository.
  --repo-only           Skip global and quality-tool installs; wire existing tools.
  --python-index-url URL
                        Use a Python package mirror in both phases.
  --install             Install missing quality tools in part 2.
  --no-install          Do not install missing quality tools in part 2.
  --wire                Write/update the quality Makefile in part 2.
  --no-wire             Do not write the quality Makefile in part 2.
  --fix                 Run safe automatic formatters after part 2 wiring.
  --no-fix              Do not run automatic formatters during setup.
  --apply-codex-config  Allow part 1 to update the global Codex config.
  --no-apply-codex-config
                        Keep the global Codex config unchanged (default).
  --no-codex-hooks      Do not create project Codex hooks.
  --no-rtk-hook         Do not install RTK or create its project hook.
  --install-prereqs     Install/check platform prerequisites in part 1.
  --context7            Run the optional interactive Context7 setup in part 1.
  --impeccable          Install the optional Impeccable integration in part 1.
  --no-humanizer        Do not install the Humanizer writing skill in part 1.
  --with-llm-council    Install the optional LLM Council integration in part 1.
  --security-scan       Run Semgrep after part 1 when Semgrep is available.
  --no-crg-build        Skip the initial code-review-graph build in part 1.
  -h, --help            Show this help.

Examples:
  bash /path/to/CodexHelper/install.sh --dry-run
  bash /path/to/CodexHelper/install.sh
  bash /path/to/CodexHelper/install.sh --fresh-install --yes
  bash /path/to/CodexHelper/install.sh --repo /path/to/project
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
	--yes | -y)
		YES=1
		shift
		;;
	--fresh-install)
		FRESH_INSTALL=1
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
	--repo-only)
		REPO_ONLY=1
		shift
		;;
	--python-index-url)
		require_value "$1" "${2:-}"
		PART1_ONLY_ARGS+=("--python-index-url=$2")
		PYTHON_INDEX_URL="$2"
		shift 2
		;;
	--python-index-url=*)
		PYTHON_INDEX_URL="${1#*=}"
		require_value "--python-index-url" "$PYTHON_INDEX_URL"
		PART1_ONLY_ARGS+=("--python-index-url=$PYTHON_INDEX_URL")
		shift
		;;
	--install)
		QUALITY_INSTALL_MODE="--install"
		shift
		;;
	--no-install)
		QUALITY_INSTALL_MODE="--no-install"
		shift
		;;
	--wire)
		QUALITY_WIRE_MODE="--wire"
		shift
		;;
	--no-wire)
		QUALITY_WIRE_MODE="--no-wire"
		shift
		;;
	--fix)
		QUALITY_FIX_MODE="--fix"
		shift
		;;
	--no-fix)
		QUALITY_FIX_MODE="--no-fix"
		shift
		;;
	--apply-codex-config)
		CODEX_CONFIG_FLAG="--apply-codex-config"
		shift
		;;
	--no-apply-codex-config)
		CODEX_CONFIG_FLAG="--no-apply-codex-config"
		shift
		;;
	--no-codex-hooks | --no-rtk-hook | --install-prereqs | --context7 | --impeccable | --no-humanizer | --with-llm-council | --security-scan | --no-crg-build)
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

if [[ "$REPO_ONLY" -eq 1 && "$QUALITY_INSTALL_MODE" == "--install" ]]; then
	err "--repo-only cannot be combined with --install."
	exit 2
fi

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

PART1_ARGS=()
PART2_ARGS=()

if [[ "$DRY_RUN" -eq 1 ]]; then
	PART1_ARGS+=("--dry-run")
	PART2_ARGS+=("--dry-run")
fi
if [[ "$YES" -eq 1 ]]; then
	PART1_ARGS+=("--yes")
	PART2_ARGS+=("--yes")
fi

if [[ "$FRESH_INSTALL" -eq 1 ]]; then
	if [[ "$REPO_ONLY" -eq 1 ]]; then
		PART1_ARGS+=("--force")
		PART2_ARGS+=("--force" "--wire")
	else
		PART1_ARGS+=("--fresh-install")
		PART2_ARGS+=("--fresh-install")
	fi
elif [[ "$FORCE" -eq 1 ]]; then
	PART1_ARGS+=("--force")
	PART2_ARGS+=("--force")
fi

if [[ "$REPO_ONLY" -eq 1 ]]; then
	PART1_ARGS+=("--repo-only")
	QUALITY_INSTALL_MODE="--no-install"
fi

if [[ "${#PART1_ONLY_ARGS[@]}" -gt 0 ]]; then
	PART1_ARGS+=("${PART1_ONLY_ARGS[@]}")
fi
PART1_ARGS+=("$CODEX_CONFIG_FLAG")

if [[ -n "${PYTHON_INDEX_URL:-}" ]]; then
	PART2_ARGS+=("--python-index-url=$PYTHON_INDEX_URL")
fi
[[ -n "$QUALITY_INSTALL_MODE" ]] && PART2_ARGS+=("$QUALITY_INSTALL_MODE")
if [[ -z "$QUALITY_WIRE_MODE" && "$FRESH_INSTALL" -ne 1 ]]; then
	QUALITY_WIRE_MODE="--wire"
fi
[[ -n "$QUALITY_WIRE_MODE" ]] && PART2_ARGS+=("$QUALITY_WIRE_MODE")
[[ -n "$QUALITY_FIX_MODE" ]] && PART2_ARGS+=("$QUALITY_FIX_MODE")

log "Platform: $PLATFORM_DIR"
log "Target repository: $(pwd)"
log "Running repository workflow setup (part 1 of 2)."
if CODEXHELPER_UNIFIED_INSTALL=1 bash "$PART1" "${PART1_ARGS[@]}"; then
	log "Part 1 completed."
else
	status=$?
	err "Part 1 failed with exit code $status. Part 2 was not run."
	exit "$status"
fi

log "Running quality-tool detection and wiring (part 2 of 2)."
run_part2() {
	if [[ "${#PART2_ARGS[@]}" -gt 0 ]]; then
		CODEXHELPER_UNIFIED_INSTALL=1 bash "$PART2" "${PART2_ARGS[@]}"
	else
		CODEXHELPER_UNIFIED_INSTALL=1 bash "$PART2"
	fi
}
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
	log "Setup complete. Restart Codex here, review/trust the project hooks in /hooks, then start a new thread."
fi
