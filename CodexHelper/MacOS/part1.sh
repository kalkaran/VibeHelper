#!/usr/bin/env bash
# Bootstrap a token-efficient, quality-focused AI coding workflow for Codex.
# Installs/configures: RTK, Ponytail, Humanizer, code-review-graph, optional
# Context7/Impeccable, and repo-local Codex workflow files. Semgrep and project
# linters are handled by part2.sh.
#
# Version: 2026-07-27-v19
#
# Safe defaults:
# - Prompts before network installs unless --yes is passed.
# - Backs up existing files before overwriting unless --force is passed.
# - Does not install production app dependencies.
# - Creates project Codex hooks for edited-file checks, but does not auto-trust them.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_NAME="$(basename "$0")"
SCRIPT_VERSION="2026-07-27-v19"
YES=0
DRY_RUN=0
FORCE=0
FRESH_INSTALL=0
SKIP_GLOBAL=0
WITH_LLM_COUNCIL=0
RUN_CONTEXT7=0
RUN_IMPECCABLE=0
RUN_HUMANIZER=1
RUN_SECURITY_SCAN=0
RUN_CRG_BUILD=1
CREATE_CODEX_HOOKS=1
CREATE_RTK_HOOK=1
CODEX_CONFIG_MODE="ask"
INSTALL_BREW=0
INSTALL_UV=0
INSTALL_PIPX=0
INSTALL_PREREQS=0
PYTHON_INDEX_URL="${UV_DEFAULT_INDEX:-${PIP_INDEX_URL:-}}"

INSTALL_OK=()
INSTALL_FAILED=()
INSTALL_SKIPPED=()
PATH_FIXES=()
FINAL_EXIT_CODE=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
HELPER_ROOT="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

log() { printf '\033[1;34m[ai-bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

usage() {
	cat <<USAGE
Usage: $SCRIPT_NAME [options]

Options:
  --yes                 Do not prompt before installs/file writes.
  --dry-run             Print what would happen, but do not change files or install tools.
  --force               Overwrite managed files after backing them up.
  --fresh-install       Freshen repo files and explicitly install/check uv + pipx.
  --repo-only           Only create repo files; skip global tool installs.
  --skip-global         Alias for --repo-only.
  --with-llm-council    Clone Karpathy llm-council into ~/.local/share/llm-council.
  --context7            Run interactive Context7 setup with npx ctx7 setup.
  --impeccable          Install Impeccable design skill/hooks for Codex.
  --no-humanizer        Do not install Humanizer writing skill for Codex.
  --codex-hooks         Create local Codex hooks for edited-file checks and Git blocking. Enabled by default.
  --no-codex-hooks      Do not create Codex hook files/config.
  --rtk-hook            Install RTK and create its PreToolUse hook. Enabled by default with --codex-hooks.
  --no-rtk-hook         Do not install RTK or create its PreToolUse hook.
  --apply-codex-config  Search for ~/.codex/config.toml and add the Git-blocking hook automatically.
  --no-apply-codex-config
                        Do not prompt to apply the Codex hook config.
  --security-scan       Run semgrep scan after setup if semgrep is available.
  --install-prereqs     Explicitly install missing Homebrew, uv, and pipx before AI tools.
  --install-brew        Explicitly install Homebrew if missing.
  --install-uv          Explicitly install uv if missing, using Homebrew.
  --install-pipx        Explicitly install pipx if missing, using Homebrew.
  --python-index-url=URL
                        Use a Python package mirror for uv/pipx installs.
  --no-crg-build        Install/configure code-review-graph but skip initial graph build.
  -h, --help            Show this help.

Recommended first run:
  bash $SCRIPT_NAME

Fully non-interactive repo + tools setup:
  bash $SCRIPT_NAME --yes

Freshen an existing setup after backing up managed files and checking uv/pipx:
  bash $SCRIPT_NAME --fresh-install

Repo scaffolding only:
  bash $SCRIPT_NAME --repo-only

Notes:
  - Ponytail for Codex requires an interactive /plugins and /hooks step after marketplace add.
  - Context7 setup is interactive OAuth, so it only runs when --context7 is used.
  - Impeccable setup is optional and only runs with --impeccable. Codex hook trust still requires /hooks.
  - Humanizer is installed for this project by default; use --no-humanizer to skip it.
  - RTK and its shell-command rewriting hook are installed by default with Codex hooks.
  - Use --no-rtk-hook to skip RTK installation and hook creation. --repo-only skips the global install.
  - Graphify is detected but not installed because its install method depends on your current setup.
  - If uv is missing, the script offers to install it with Homebrew.
  - In --yes mode, missing uv is installed automatically if Homebrew is available.
  - Homebrew itself is still never installed silently with --yes alone.
USAGE
}

for arg in "$@"; do
	case "$arg" in
	--yes | -y) YES=1 ;;
	--dry-run) DRY_RUN=1 ;;
	--force) FORCE=1 ;;
	--fresh-install)
		FRESH_INSTALL=1
		FORCE=1
		INSTALL_BREW=1
		INSTALL_UV=1
		INSTALL_PIPX=1
		;;
	--skip-global | --repo-only) SKIP_GLOBAL=1 ;;
	--with-llm-council) WITH_LLM_COUNCIL=1 ;;
	--context7) RUN_CONTEXT7=1 ;;
	--impeccable) RUN_IMPECCABLE=1 ;;
	--no-humanizer) RUN_HUMANIZER=0 ;;
	--codex-hooks) CREATE_CODEX_HOOKS=1 ;;
	--no-codex-hooks) CREATE_CODEX_HOOKS=0 ;;
	--rtk-hook) CREATE_RTK_HOOK=1 ;;
	--no-rtk-hook) CREATE_RTK_HOOK=0 ;;
	--apply-codex-config) CODEX_CONFIG_MODE="apply" ;;
	--no-apply-codex-config) CODEX_CONFIG_MODE="skip" ;;
	--security-scan) RUN_SECURITY_SCAN=1 ;;
	--install-prereqs)
		INSTALL_PREREQS=1
		INSTALL_BREW=1
		INSTALL_UV=1
		INSTALL_PIPX=1
		;;
	--install-brew) INSTALL_BREW=1 ;;
	--install-uv) INSTALL_UV=1 ;;
	--install-pipx) INSTALL_PIPX=1 ;;
	--python-index-url=*) PYTHON_INDEX_URL="${arg#*=}" ;;
	--no-crg-build) RUN_CRG_BUILD=0 ;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		err "Unknown option: $arg"
		usage
		exit 2
		;;
	esac
done

if [[ -n "$PYTHON_INDEX_URL" ]]; then
	export UV_DEFAULT_INDEX="$PYTHON_INDEX_URL"
	export PIP_INDEX_URL="$PYTHON_INDEX_URL"
	log "Using configured Python package index mirror for uv/pipx installs."
fi

confirm() {
	local prompt="$1"
	if [[ "$YES" == "1" ]]; then
		return 0
	fi
	local reply
	read -r -p "$prompt [y/N] " reply || true
	[[ "$reply" =~ ^[Yy]$|^[Yy][Ee][Ss]$ ]]
}

run() {
	# Usage in this script: run <label> <command> [args...]
	# The label is only for grouping/logging; the actual command starts at $2.
	local label="${1:-command}"
	shift || true
	if [[ "$#" -eq 0 ]]; then
		warn "run called without a command for label: $label"
		return 1
	fi
	printf '\033[0;36m$'
	for arg in "$@"; do printf ' %q' "$arg"; done
	printf '\033[0m\n'
	if [[ "$DRY_RUN" == "1" ]]; then
		return 0
	fi
	"$@"
}

run_shell() {
	local cmd="$1"
	printf '\033[0;36m$ %s\033[0m\n' "$cmd"
	if [[ "$DRY_RUN" == "1" ]]; then
		return 0
	fi
	bash -lc "$cmd"
}

have() { command -v "$1" >/dev/null 2>&1; }

resolve_brew() {
	if command -v brew >/dev/null 2>&1; then
		command -v brew
		return 0
	fi

	local candidate
	for candidate in \
		"/opt/homebrew/bin/brew" \
		"/usr/local/bin/brew" \
		"/home/linuxbrew/.linuxbrew/bin/brew"; do
		if [[ -x "$candidate" ]]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done

	return 1
}

have_brew() { resolve_brew >/dev/null 2>&1; }

ensure_brew_path() {
	local brew_path dir
	brew_path="$(resolve_brew 2>/dev/null || true)"
	[[ -n "$brew_path" ]] || return 1
	dir="$(dirname "$brew_path")"
	case ":$PATH:" in
	*":$dir:"*) ;;
	*)
		export PATH="$dir:$PATH"
		remember_path_fix "$dir"
		;;
	esac
	return 0
}

run_brew() {
	local brew_path
	brew_path="$(resolve_brew 2>/dev/null || true)"
	if [[ -z "$brew_path" ]]; then
		warn "brew is not available; cannot run: brew $*"
		return 127
	fi
	ensure_brew_path || true
	run brew "$brew_path" "$@"
}

# Resolve a CLI even when uv/pipx installed it into a bin directory not yet on PATH.
resolve_binary() {
	local binary="$1"
	local candidate bin_dir

	if command -v "$binary" >/dev/null 2>&1; then
		command -v "$binary"
		return 0
	fi

	if command -v uv >/dev/null 2>&1; then
		bin_dir="$(uv tool dir --bin 2>/dev/null || true)"
		if [[ -n "$bin_dir" ]]; then
			candidate="$bin_dir/$binary"
			if [[ -x "$candidate" ]]; then
				printf '%s
' "$candidate"
				return 0
			fi
		fi
	fi

	for bin_dir in "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/Library/Python/3.12/bin" "$HOME/Library/Python/3.11/bin" "$HOME/Library/Python/3.10/bin" "/opt/homebrew/bin" "/usr/local/bin"; do
		candidate="$bin_dir/$binary"
		if [[ -x "$candidate" ]]; then
			printf '%s
' "$candidate"
			return 0
		fi
	done

	return 1
}

have_cli() { resolve_binary "$1" >/dev/null 2>&1; }

semgrep_cert_file() {
	if [[ -n "${SSL_CERT_FILE:-}" && -r "${SSL_CERT_FILE:-}" ]]; then
		printf '%s\n' "$SSL_CERT_FILE"
		return 0
	fi

	local candidate
	for candidate in \
		"/opt/homebrew/etc/ca-certificates/cert.pem" \
		"/usr/local/etc/ca-certificates/cert.pem" \
		"/opt/homebrew/etc/openssl@3/cert.pem" \
		"/usr/local/etc/openssl@3/cert.pem"; do
		if [[ -r "$candidate" ]]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done

	return 1
}

run_semgrep_cli() {
	local path cert
	path="$(resolve_binary semgrep 2>/dev/null || true)"
	[[ -n "$path" ]] || return 127
	local state_dir=".cache/semgrep"
	if [[ "$DRY_RUN" -eq 1 ]]; then
		state_dir="${TMPDIR:-/tmp}/ai-quality-semgrep-$$"
	fi
	mkdir -p "$state_dir"
	cert="$(semgrep_cert_file 2>/dev/null || true)"
	if [[ -n "$cert" ]]; then
		SSL_CERT_FILE="$cert" \
			SEMGREP_LOG_FILE="$state_dir/semgrep.log" \
			SEMGREP_SETTINGS_FILE="$state_dir/settings.yml" \
			SEMGREP_VERSION_CACHE_PATH="$state_dir/version-cache" \
			"$path" "$@"
	else
		SEMGREP_LOG_FILE="$state_dir/semgrep.log" \
			SEMGREP_SETTINGS_FILE="$state_dir/settings.yml" \
			SEMGREP_VERSION_CACHE_PATH="$state_dir/version-cache" \
			"$path" "$@"
	fi
}

cli_works() {
	local binary="$1"
	local path
	path="$(resolve_binary "$binary" 2>/dev/null || true)"
	[[ -n "$path" ]] || return 1
	if [[ "$binary" == "semgrep" ]]; then
		return 0
	fi
	"$path" --version >/dev/null 2>&1
}

remember_path_fix() {
	local dir="$1"
	case ":$PATH:" in
	*":$dir:"*) return 0 ;;
	esac
	PATH_FIXES+=("Add to shell PATH: export PATH=$dir:\$PATH")
}

ensure_current_script_path() {
	local binary="$1"
	local path dir
	path="$(resolve_binary "$binary" 2>/dev/null || true)"
	[[ -n "$path" ]] || return 1
	dir="$(dirname "$path")"
	case ":$PATH:" in
	*":$dir:"*) ;;
	*)
		export PATH="$dir:$PATH"
		remember_path_fix "$dir"
		;;
	esac
	return 0
}

maybe_add_bin_dir_to_zshrc() {
	local binary="$1"
	local path dir zshrc marker line
	path="$(resolve_binary "$binary" 2>/dev/null || true)"
	[[ -n "$path" ]] || return 0
	dir="$(dirname "$path")"

	case ":$PATH:" in
	*":$dir:"*) return 0 ;;
	esac

	zshrc="$HOME/.zshrc"
	marker="# ai-bootstrap: uv/pipx tool path"
	line="export PATH=$dir:\$PATH"

	warn "$binary exists at $path but that directory is not on your current PATH."
	warn "Without a PATH fix, zsh will say: command not found: $binary"

	if [[ "$DRY_RUN" == "1" ]]; then
		printf '[0;36m$ printf %q\n %q >> %q[0m
' "$marker" "$line" "$zshrc"
		record_install_skipped "$binary PATH fix would be added to $zshrc (dry-run)"
		return 0
	fi

	if confirm "Add $dir to ~/.zshrc so $binary works in new zsh shells?"; then
		touch "$zshrc"
		if ! grep -Fq "$line" "$zshrc" 2>/dev/null; then
			{
				printf '
%s
' "$marker"
				printf '%s
' "$line"
			} >>"$zshrc"
			log "Added $dir to $zshrc"
			record_install_ok "Added $dir to ~/.zshrc PATH"
		else
			log "$dir already present in $zshrc"
		fi
	else
		record_install_skipped "$binary PATH fix skipped by user; run: export PATH=$dir:\$PATH"
	fi
}

run_cli() {
	local binary="$1"
	shift
	local path
	path="$(resolve_binary "$binary" 2>/dev/null || true)"
	if [[ -z "$path" ]]; then
		warn "$binary is not available; cannot run: $binary $*"
		return 127
	fi
	ensure_current_script_path "$binary" || true
	run "$binary" "$path" "$@"
}

repo_root() {
	if have git && git rev-parse --show-toplevel >/dev/null 2>&1; then
		git rev-parse --show-toplevel
	else
		pwd
	fi
}

ROOT="$(repo_root)"
cd "$ROOT"
log "Working in repo/root: $ROOT"

if [[ "$DRY_RUN" == "1" ]]; then
	warn "Dry run mode: no files will be written and no installs will run."
fi

backup_file() {
	local path="$1"
	if [[ -e "$path" ]]; then
		local stamp
		stamp="$(date +%Y%m%d-%H%M%S)"
		local backup="$path.bak.$stamp"
		if [[ "$DRY_RUN" == "1" ]]; then
			log "Would back up $path -> $backup"
		else
			cp "$path" "$backup"
			log "Backed up $path -> $backup"
		fi
	fi
}

managed_file_version() {
	local path="$1"
	[[ -f "$path" ]] || return 1
	sed -n '1,5s/.*CodexHelper-Version:[[:space:]]*\([^[:space:]>]*\).*/\1/p' "$path" | head -n 1
}

version_marker_for_path() {
	local path="$1"
	case "$path" in
	*.md) printf '<!-- CodexHelper-Version: %s -->' "$SCRIPT_VERSION" ;;
	*) printf '# CodexHelper-Version: %s' "$SCRIPT_VERSION" ;;
	esac
}

add_managed_version_marker() {
	local path="$1"
	local content="$2"
	local marker
	marker="$(version_marker_for_path "$path")"

	case "$content" in
	'#!'*)
		printf '%s\n%s\n' "${content%%$'\n'*}" "$marker"
		printf '%s\n' "${content#*$'\n'}"
		;;
	*)
		printf '%s\n%s' "$marker" "$content"
		;;
	esac
}

move_conflicting_path() {
	local path="$1"
	local stamp backup
	stamp="$(date +%Y%m%d-%H%M%S)"
	backup="$path.bak.$stamp"
	mv "$path" "$backup"
	log "Moved conflicting non-directory $path -> $backup"
}

ensure_parent_dir() {
	local path="$1"
	local dir component current
	dir="$(dirname "$path")"
	if [[ "$dir" == "." || -z "$dir" ]]; then
		return 0
	fi

	current=""
	if [[ "$dir" == /* ]]; then
		current="/"
	fi

	IFS='/' read -r -a parts <<<"$dir"
	for component in "${parts[@]}"; do
		if [[ -z "$component" || "$component" == "." ]]; then
			continue
		fi
		if [[ "$current" == "/" ]]; then
			current="/$component"
		elif [[ -n "$current" ]]; then
			current="$current/$component"
		else
			current="$component"
		fi
		if [[ (-e "$current" || -L "$current") && ! -d "$current" ]]; then
			if [[ "$FORCE" == "1" ]]; then
				move_conflicting_path "$current"
			else
				err "Cannot create $dir because $current exists and is not a directory. Move or remove $current, then rerun this script, or rerun with --force to move it aside automatically."
				return 1
			fi
		fi
	done

	mkdir -p "$dir"
}

write_file() {
	local path="$1"
	local content="$2"
	local mode="${3:-0644}"
	local existing_version versioned_content

	versioned_content="$(add_managed_version_marker "$path" "$content")"

	if [[ -e "$path" && "$FORCE" != "1" ]]; then
		existing_version="$(managed_file_version "$path" 2>/dev/null || true)"
		if [[ "$existing_version" == "$SCRIPT_VERSION" ]]; then
			log "$path already exists at CodexHelper version $SCRIPT_VERSION; leaving it unchanged."
		else
			warn "$path already exists from CodexHelper version ${existing_version:-unknown}; current version is $SCRIPT_VERSION. Use --force to update after backup."
		fi
		return 0
	fi

	if [[ -e "$path" ]]; then
		backup_file "$path"
	fi

	if [[ "$DRY_RUN" == "1" ]]; then
		log "Would write $path"
		return 0
	fi

	ensure_parent_dir "$path"
	printf '%s\n' "$versioned_content" >"$path"
	chmod "$mode" "$path"
	log "Wrote $path"
}

append_if_missing() {
	local path="$1"
	local marker="$2"
	local content="$3"

	if [[ "$DRY_RUN" == "1" ]]; then
		log "Would append managed block to $path if marker missing"
		return 0
	fi

	touch "$path"
	if grep -Fq "$marker" "$path"; then
		log "$path already contains managed block: $marker"
	else
		backup_file "$path"
		printf '\n%s\n' "$content" >>"$path"
		log "Appended managed block to $path"
	fi
}

codex_plugin_marketplace_configured() {
	local name="$1"
	have codex || return 1
	codex plugin marketplace list 2>/dev/null | awk -v name="$name" '$1 == name { found = 1 } END { exit found ? 0 : 1 }'
}

codex_plugin_installed() {
	local plugin_id="$1"
	have codex || return 1
	codex plugin list --json 2>/dev/null | grep -Fq "\"pluginId\": \"$plugin_id\""
}

print_ponytail_status() {
	if ! have codex; then
		printf '  %-20s %s\n' "ponytail" "codex not found"
		return 0
	fi

	local plugin_status marketplace_status
	if codex_plugin_installed "ponytail@ponytail"; then
		plugin_status="installed"
	else
		plugin_status="not installed"
	fi
	if codex_plugin_marketplace_configured "ponytail"; then
		marketplace_status="marketplace configured"
	else
		marketplace_status="marketplace not configured"
	fi
	printf '  %-20s %s (%s)\n' "ponytail" "$plugin_status" "$marketplace_status"
}

check_prereqs() {
	log "Checking local prerequisites"

	local uname_s
	uname_s="$(uname -s 2>/dev/null || echo unknown)"
	case "$uname_s" in
	Linux | Darwin) : ;;
	*) warn "This script is written for macOS/Linux shells. On Windows, use WSL/Git Bash at your own risk." ;;
	esac

	if ! have python3; then warn "python3 not found. Python tools will not install."; fi
	if [[ -n "${VIRTUAL_ENV:-}" ]]; then
		warn "Active Python virtualenv detected: $VIRTUAL_ENV"
		warn "Global AI tools will only be installed with isolated uv tool/pipx, never plain pip."
	fi
	if ! have node; then warn "node not found. Ponytail hooks and Context7 setup require Node.js."; fi
	if ! have npm && ! have npx; then warn "npm/npx not found. Context7 and Impeccable setup will not run."; fi
	if ! have codex; then warn "codex CLI not found. Ponytail Codex plugin steps will be skipped."; fi

	log "Detected tools:"
	for cmd in git brew codex node npm npx python3 uv pipx graphify; do
		if have "$cmd"; then
			printf '  %-20s %s
' "$cmd" "$(command -v "$cmd")"
		else
			printf '  %-20s %s
' "$cmd" "not found"
		fi
	done
	print_cli_resolution semgrep
	print_cli_resolution code-review-graph
	print_cli_resolution rtk
	print_ponytail_status
}

binary_in_active_venv() {
	local binary="$1"
	local path
	path="$(command -v "$binary" 2>/dev/null || true)"
	[[ -n "${VIRTUAL_ENV:-}" && -n "$path" && "$path" == "$VIRTUAL_ENV"/* ]]
}

record_install_ok() { INSTALL_OK+=("$1"); }
record_install_failed() {
	INSTALL_FAILED+=("$1")
	FINAL_EXIT_CODE=1
}
record_install_skipped() { INSTALL_SKIPPED+=("$1"); }

require_cli_available() {
	# Mark a CLI as a hard failure if it is unavailable after setup.
	# Usage: require_cli_available <binary> <human-readable fix>
	local binary="$1"
	local fix="$2"
	if [[ "$DRY_RUN" == "1" || "$SKIP_GLOBAL" == "1" ]]; then
		return 0
	fi
	if have_cli "$binary" && ! binary_in_active_venv "$binary"; then
		ensure_current_script_path "$binary" || true
		return 0
	fi
	warn "$binary is still not available after setup."
	warn "$fix"
	record_install_failed "$binary missing after setup. $fix"
	return 1
}

print_cli_resolution() {
	local binary="$1"
	local path
	path="$(resolve_binary "$binary" 2>/dev/null || true)"
	if [[ -n "$path" ]]; then
		printf '  %-20s %s\n' "$binary" "$path"
	else
		printf '  %-20s %s\n' "$binary" "not found"
	fi
}

maybe_install_homebrew() {
	if have_brew; then
		ensure_brew_path || true
		log "Homebrew already installed: $(resolve_brew)"
		record_install_ok "Homebrew already present at $(resolve_brew)"
		return 0
	fi

	local uname_s
	uname_s="$(uname -s 2>/dev/null || echo unknown)"
	if [[ "$uname_s" != "Darwin" && "$uname_s" != "Linux" ]]; then
		warn "Homebrew install is only supported by this script on macOS/Linux/WSL. Skipping."
		record_install_skipped "Homebrew skipped: unsupported OS $uname_s"
		return 0
	fi

	if [[ "$INSTALL_BREW" != "1" && "$INSTALL_PREREQS" != "1" ]]; then
		if [[ "$YES" == "1" ]]; then
			warn "Homebrew is missing, but --yes alone will not install it. Use --install-brew or --install-prereqs."
			record_install_skipped "Homebrew skipped: explicit --install-brew/--install-prereqs not supplied"
			return 0
		fi
		if ! confirm "Homebrew is missing. Install Homebrew using the official installer?"; then
			record_install_skipped "Homebrew skipped by user"
			return 0
		fi
	fi

	if ! have curl; then
		warn "curl is required to install Homebrew. Install curl or install Homebrew manually from https://brew.sh/."
		record_install_failed "Homebrew install failed: curl missing"
		return 1
	fi

	warn "Homebrew's official installer may install Xcode Command Line Tools, write to /opt/homebrew or /usr/local, and update shell profile guidance."
	# shellcheck disable=SC2016
	local cmd='/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
	if [[ "$YES" == "1" ]]; then
		# shellcheck disable=SC2016
		cmd='NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
	fi

	if [[ "$DRY_RUN" == "1" ]]; then
		run_shell "$cmd"
		record_install_skipped "Homebrew would install (dry-run)"
		return 0
	fi

	if ! run_shell "$cmd"; then
		warn "Homebrew install failed. Continuing without brew-dependent installs."
		record_install_failed "Homebrew install failed"
		return 1
	fi

	if have_brew; then
		ensure_brew_path || true
		log "Homebrew installed: $(resolve_brew)"
		record_install_ok "Homebrew installed at $(resolve_brew)"
		return 0
	fi

	warn "Homebrew installer finished, but brew was not found in expected locations."
	record_install_failed "Homebrew installed but brew not found"
	return 1
}

maybe_install_uv() {
	if have uv; then
		log "uv already installed: $(command -v uv)"
		record_install_ok "uv already present at $(command -v uv)"
		return 0
	fi

	if ! confirm "uv is missing. Install uv with Homebrew?"; then
		record_install_skipped "uv skipped by user"
		return 0
	fi

	if ! have_brew; then
		maybe_install_homebrew || true
	fi

	if ! have_brew; then
		warn "Cannot install uv with Homebrew because brew is unavailable."
		record_install_skipped "uv skipped: brew unavailable"
		return 0
	fi

	if [[ "$DRY_RUN" == "1" ]]; then
		run_brew install uv
		record_install_skipped "uv would install via Homebrew (dry-run)"
		return 0
	fi

	if ! run_brew install uv; then
		warn "uv install failed via Homebrew. Continuing without uv."
		record_install_failed "uv failed via Homebrew"
		return 1
	fi

	if have uv; then
		log "uv installed: $(command -v uv)"
		record_install_ok "uv installed via Homebrew at $(command -v uv)"
		return 0
	fi

	warn "brew install uv finished, but uv is not on PATH."
	record_install_failed "uv installed but not found on PATH"
	return 1
}

maybe_install_pipx() {
	if have pipx; then
		log "pipx already installed: $(command -v pipx)"
		record_install_ok "pipx already present at $(command -v pipx)"
		return 0
	fi

	if [[ "$INSTALL_PIPX" != "1" && "$INSTALL_PREREQS" != "1" ]]; then
		if [[ "$YES" == "1" ]]; then
			record_install_skipped "pipx skipped: explicit --install-pipx/--install-prereqs not supplied"
			return 0
		fi
		if ! confirm "pipx is missing. Install pipx with Homebrew?"; then
			record_install_skipped "pipx skipped by user"
			return 0
		fi
	fi

	if ! have_brew; then
		maybe_install_homebrew || true
	fi

	if ! have_brew; then
		warn "Cannot install pipx with Homebrew because brew is unavailable."
		record_install_skipped "pipx skipped: brew unavailable"
		return 0
	fi

	if [[ "$DRY_RUN" == "1" ]]; then
		run_brew install pipx
		run pipx pipx ensurepath
		record_install_skipped "pipx would install via Homebrew (dry-run)"
		return 0
	fi

	if ! run_brew install pipx; then
		warn "pipx install failed via Homebrew. Continuing without pipx."
		record_install_failed "pipx failed via Homebrew"
		return 1
	fi

	if have pipx; then
		run pipx pipx ensurepath || true
		log "pipx installed: $(command -v pipx)"
		record_install_ok "pipx installed via Homebrew at $(command -v pipx)"
		return 0
	fi

	warn "brew install pipx finished, but pipx is not on PATH."
	record_install_failed "pipx installed but not found on PATH"
	return 1
}

maybe_install_rtk() {
	if have_cli rtk; then
		log "RTK already installed: $(resolve_binary rtk)"
		record_install_ok "RTK already present at $(resolve_binary rtk)"
		return 0
	fi

	if ! confirm "RTK is missing. Install RTK with Homebrew for the default Codex hook?"; then
		record_install_skipped "RTK skipped by user"
		return 0
	fi

	if ! have_brew; then
		maybe_install_homebrew || true
	fi

	if ! have_brew; then
		warn "Cannot install RTK with Homebrew because brew is unavailable."
		record_install_skipped "RTK skipped: brew unavailable"
		return 0
	fi

	if [[ "$DRY_RUN" == "1" ]]; then
		run_brew install rtk
		record_install_skipped "RTK would install via Homebrew (dry-run)"
		return 0
	fi

	if ! run_brew install rtk; then
		warn "RTK install failed via Homebrew. The generated hook will pass commands through unchanged."
		record_install_failed "RTK failed via Homebrew"
		return 1
	fi

	ensure_brew_path || true
	if have_cli rtk; then
		log "RTK installed: $(resolve_binary rtk)"
		record_install_ok "RTK installed via Homebrew at $(resolve_binary rtk)"
		return 0
	fi

	warn "brew install finished, but rtk was not found on PATH."
	record_install_failed "RTK installed but not found on PATH"
	return 1
}

bootstrap_prereqs() {
	if [[ "$SKIP_GLOBAL" == "1" && "$FRESH_INSTALL" != "1" ]]; then
		log "Skipping prerequisite installs (--repo-only/--skip-global)."
		return 0
	fi
	if [[ "$SKIP_GLOBAL" == "1" && "$FRESH_INSTALL" == "1" ]]; then
		log "Fresh install: checking uv/pipx prerequisites even with --repo-only."
	fi

	if [[ "$INSTALL_BREW" == "1" || "$INSTALL_PREREQS" == "1" ]]; then
		maybe_install_homebrew || true
	elif ! have_brew && [[ "$YES" != "1" ]]; then
		maybe_install_homebrew || true
	elif have_brew; then
		ensure_brew_path || true
	fi

	if [[ "$INSTALL_UV" == "1" || "$INSTALL_PREREQS" == "1" ]] || ! have uv; then
		maybe_install_uv || true
	fi

	if [[ "$INSTALL_PIPX" == "1" || "$INSTALL_PREREQS" == "1" ]]; then
		maybe_install_pipx || true
	elif ! have pipx && [[ "$YES" != "1" ]]; then
		maybe_install_pipx || true
	fi
}

install_with_uv_or_pipx() {
	local package="$1"
	local binary="$2"
	local extra_uv_args="${3:-}"
	local install_cmd=""

	if have_cli "$binary"; then
		if binary_in_active_venv "$binary"; then
			warn "$binary is installed inside the active virtualenv: $(command -v "$binary")"
			warn "Not treating this as a safe global install. Install via uv tool or pipx instead."
		else
			log "$binary already installed: $(resolve_binary "$binary")"
			record_install_ok "$package already present at $(resolve_binary "$binary")"
			maybe_add_bin_dir_to_zshrc "$binary"
			ensure_current_script_path "$binary" || true
			return 0
		fi
	fi

	if have uv; then
		if confirm "Install $package in an isolated uv tool environment?"; then
			if [[ "$DRY_RUN" == "1" ]]; then
				if [[ -n "$extra_uv_args" ]]; then
					run_shell "uv tool install $extra_uv_args $package"
				else
					run uv uv tool install "$package"
				fi
				record_install_skipped "$package would install via uv (dry-run)"
				return 0
			fi

			if [[ -n "$extra_uv_args" ]]; then
				install_cmd="uv tool install $extra_uv_args $package"
				if ! run_shell "$install_cmd"; then
					warn "$package install failed via uv. Continuing without it."
					record_install_failed "$package failed via uv"
					return 1
				fi
			else
				if ! run uv uv tool install "$package"; then
					warn "$package install failed via uv. Continuing without it."
					record_install_failed "$package failed via uv"
					return 1
				fi
			fi

			if have_cli "$binary" && ! binary_in_active_venv "$binary"; then
				ensure_current_script_path "$binary" || true
				local resolved
				resolved="$(resolve_binary "$binary")"
				log "$package installed successfully: $resolved"
				record_install_ok "$package installed via uv at $resolved"
				maybe_add_bin_dir_to_zshrc "$binary"
				return 0
			fi

			warn "$package install command finished, but $binary was not found even in uv/pipx bin locations."
			warn "Try: uv tool list | grep -i ${package%@latest}"
			warn "Then check: uv tool dir --bin"
			record_install_failed "$package installed but $binary not found"
			return 1
		else
			warn "Skipped $package install."
			record_install_skipped "$package skipped by user"
			return 0
		fi
	elif have pipx; then
		if confirm "Install $package in an isolated pipx environment?"; then
			if [[ "$DRY_RUN" == "1" ]]; then
				run pipx pipx install "$package"
				record_install_skipped "$package would install via pipx (dry-run)"
				return 0
			fi
			if ! run pipx pipx install "$package"; then
				warn "$package install failed via pipx. Continuing without it."
				record_install_failed "$package failed via pipx"
				return 1
			fi

			if have_cli "$binary" && ! binary_in_active_venv "$binary"; then
				ensure_current_script_path "$binary" || true
				local resolved
				resolved="$(resolve_binary "$binary")"
				log "$package installed successfully: $resolved"
				record_install_ok "$package installed via pipx at $resolved"
				maybe_add_bin_dir_to_zshrc "$binary"
				return 0
			fi

			warn "$package install command finished, but $binary was not found on PATH or known bin locations."
			warn "Try: pipx ensurepath"
			record_install_failed "$package installed but $binary not found"
			return 1
		else
			warn "Skipped $package install."
			record_install_skipped "$package skipped by user"
			return 0
		fi
	else
		warn "uv and pipx are not available. Skipping $package."
		warn "I will not fall back to python3 -m pip, because that can pollute your project/global Python environment."
		warn "Install uv or pipx first, or rerun this script with --install-uv or --install-prereqs."
		record_install_skipped "$package skipped: uv/pipx unavailable"
		return 0
	fi
}

setup_impeccable() {
	if [[ -f ".agents/skills/impeccable/SKILL.md" ]]; then
		log "Impeccable is already installed for Codex."
		patch_codex_impeccable_stop_hook
		record_install_ok "Impeccable already installed for Codex"
		return 0
	fi
	if [[ "$RUN_IMPECCABLE" == "1" || "$YES" != "1" ]]; then
		if have npx; then
			warn "Impeccable install may write .agents/, .codex/hooks.json, .impeccable/, PRODUCT.md, and DESIGN.md."
			warn "Codex still requires opening /hooks and approving the Impeccable project hook before it runs automatically."
			if confirm "Install Impeccable design skill/hooks for Codex?"; then
				if [[ "$DRY_RUN" == "1" ]]; then
					run npx npx impeccable install -y --providers=codex --scope=project
					record_install_skipped "Impeccable install would run (dry-run)"
				elif run npx npx impeccable install -y --providers=codex --scope=project && [[ -f ".agents/skills/impeccable/SKILL.md" ]]; then
					patch_codex_impeccable_stop_hook
					record_install_ok "Impeccable install completed"
					record_install_skipped "Impeccable still needs /impeccable init and Codex /hooks approval"
				else
					warn "Impeccable install failed, was cancelled, or did not create .agents/skills/impeccable/SKILL.md."
					record_install_failed "Impeccable install failed/cancelled"
				fi
			else
				record_install_skipped "Impeccable install skipped by user"
			fi
		else
			warn "Cannot run Impeccable setup because npx was not found."
			record_install_skipped "Impeccable skipped: npx unavailable"
		fi
	else
		warn "Impeccable setup not run in --yes mode. Use --impeccable to approve it non-interactively."
	fi
}

patch_codex_impeccable_stop_hook() {
	local hook_lib=".agents/skills/impeccable/scripts/hook-lib.mjs"
	[[ -f "$hook_lib" ]] || return 0
	if ! grep -Fq "JSON.stringify({ decision: 'block', reason: text })" "$hook_lib"; then
		return 0
	fi
	if [[ "$DRY_RUN" == "1" ]]; then
		log "Would patch Impeccable Codex Stop hook JSON compatibility."
		return 0
	fi
	if ! python3 - "$hook_lib" <<'PY'; then
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "return JSON.stringify({ decision: 'block', reason: text });"
new = "return JSON.stringify({ continue: false, stopReason: text });"
if old in text:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY
		warn "Could not patch Impeccable Codex Stop hook compatibility."
		return 0
	fi
	log "Patched Impeccable Codex Stop hook JSON compatibility."
}

humanizer_installed() {
	[[ -f ".agents/skills/humanizer/SKILL.md" ]] ||
		[[ -f "$HOME/.codex/skills/humanizer/SKILL.md" ]]
}

setup_humanizer() {
	[[ "$RUN_HUMANIZER" == "1" ]] || return 0

	if humanizer_installed; then
		log "Humanizer is already installed for Codex."
		record_install_ok "Humanizer already installed"
		return 0
	fi
	if ! have npx; then
		warn "Cannot install Humanizer because npx was not found."
		record_install_failed "Humanizer skipped: npx unavailable"
		return 0
	fi

	warn "Humanizer installs project skill files under .agents/skills/."
	if ! confirm "Install the Humanizer writing skill for Codex?"; then
		record_install_skipped "Humanizer install skipped by user"
		return 0
	fi
	if [[ "$DRY_RUN" == "1" ]]; then
		run npx npx --yes skills add blader/humanizer --agent codex --yes
		record_install_skipped "Humanizer install would run (dry-run)"
	elif run npx npx --yes skills add blader/humanizer --agent codex --yes; then
		record_install_ok "Humanizer install completed"
	else
		warn "Humanizer install failed."
		record_install_failed "Humanizer install failed"
	fi
}

install_global_tools() {
	if [[ "$SKIP_GLOBAL" == "1" ]]; then
		log "Skipping global tool installs (--repo-only/--skip-global)."
		setup_humanizer
		setup_impeccable
		return 0
	fi

	log "Global tool setup"

	if [[ "$CREATE_CODEX_HOOKS" == "1" && "$CREATE_RTK_HOOK" == "1" ]]; then
		maybe_install_rtk || true
	fi

	# Ponytail: Codex plugin marketplace add can be automated, but actual install/trust is interactive.
	if have codex; then
		local ponytail_installed=0 ponytail_marketplace_configured=0
		if codex_plugin_installed "ponytail@ponytail"; then
			ponytail_installed=1
		fi
		if codex_plugin_marketplace_configured "ponytail"; then
			ponytail_marketplace_configured=1
		fi

		if [[ "$ponytail_installed" == "1" ]]; then
			log "Ponytail Codex plugin already installed."
			record_install_ok "Ponytail Codex plugin already installed"
		elif [[ "$ponytail_marketplace_configured" == "1" ]]; then
			log "Ponytail Codex marketplace already configured."
			record_install_ok "Ponytail Codex marketplace already configured"
			record_install_skipped "Ponytail plugin install still requires /plugins and hook trust review"
		elif confirm "Add Ponytail marketplace to Codex? This does not auto-trust hooks."; then
			if [[ "$DRY_RUN" == "1" ]]; then
				run codex codex plugin marketplace add DietrichGebert/ponytail
				record_install_skipped "Ponytail Codex marketplace add would run (dry-run)"
			elif run codex codex plugin marketplace add DietrichGebert/ponytail; then
				record_install_ok "Ponytail Codex marketplace added"
				record_install_skipped "Ponytail plugin install still requires /plugins and hook trust review"
			else
				warn "Ponytail marketplace add failed. You can run it manually."
				record_install_failed "Ponytail Codex marketplace add failed"
			fi
		fi
	else
		warn "Skipping Ponytail Codex marketplace add because codex CLI was not found."
	fi

	# code-review-graph is a core part of this bootstrap. Do not silently continue if it is missing.
	install_with_uv_or_pipx "code-review-graph" "code-review-graph" || true
	require_cli_available "code-review-graph" "Install it with: uv tool install code-review-graph" || true

	if have_cli code-review-graph && ! binary_in_active_venv code-review-graph; then
		ensure_current_script_path code-review-graph || true
		log "Verified code-review-graph executable: $(resolve_binary code-review-graph)"
		if confirm "Configure code-review-graph for Codex MCP?"; then
			if run_cli code-review-graph install --platform codex; then
				record_install_ok "code-review-graph Codex integration configured"
			else
				warn "code-review-graph Codex install failed."
				record_install_failed "code-review-graph Codex integration failed"
			fi
		fi
		if [[ "$RUN_CRG_BUILD" == "1" ]] && confirm "Build code-review-graph index for this repo now?"; then
			if run_cli code-review-graph build; then
				record_install_ok "code-review-graph repo index built"
			else
				warn "code-review-graph build failed."
				record_install_failed "code-review-graph repo index build failed"
			fi
		fi
	else
		warn "Skipping code-review-graph Codex setup because the executable is missing."
	fi

	# Semgrep is a repo quality/security tool. This bootstrap only recommends it;
	# part2.sh handles installation and Makefile wiring.
	if have_cli semgrep; then
		record_install_ok "semgrep already present at $(resolve_binary semgrep)"
	else
		record_install_skipped "semgrep not installed; part2.sh can install/wire it"
	fi

	if has_source_file \( -name '*.sh' -o -name '*.bash' -o -name '*.zsh' \); then
		if have_cli shellcheck; then
			record_install_ok "shellcheck already present at $(resolve_binary shellcheck)"
		else
			record_install_skipped "shellcheck not installed; part2.sh can install/wire it"
		fi
		if have_cli shfmt; then
			record_install_ok "shfmt already present at $(resolve_binary shfmt)"
		else
			record_install_skipped "shfmt not installed; part2.sh can install/wire it"
		fi
	fi

	# Context7: interactive OAuth, ask in normal interactive runs; require explicit flag in --yes mode.
	if [[ "$RUN_CONTEXT7" == "1" || "$YES" != "1" ]]; then
		if have npx; then
			if confirm "Run interactive Context7 setup with npx ctx7 setup?"; then
				if [[ "$DRY_RUN" == "1" ]]; then
					run npx npx ctx7 setup
					record_install_skipped "Context7 setup would run (dry-run)"
				elif run npx npx ctx7 setup; then
					record_install_ok "Context7 setup completed"
				else
					warn "Context7 setup failed or was cancelled."
					record_install_failed "Context7 setup failed/cancelled"
				fi
			fi
		else
			warn "Cannot run Context7 setup because npx was not found."
		fi
	else
		warn "Context7 setup not run in --yes mode. Use --context7 to approve it non-interactively."
	fi

	setup_humanizer
	setup_impeccable

	# LLM Council optional clone, not installed as app dependency.
	if [[ "$WITH_LLM_COUNCIL" == "1" ]]; then
		if have git; then
			local dest="$HOME/.local/share/llm-council"
			if [[ -d "$dest/.git" ]]; then
				log "llm-council already cloned at $dest"
				record_install_ok "llm-council already present at $dest"
			elif confirm "Clone karpathy/llm-council into $dest?"; then
				if [[ "$DRY_RUN" != "1" ]]; then mkdir -p "$(dirname "$dest")"; fi
				if [[ "$DRY_RUN" == "1" ]]; then
					run git git clone https://github.com/karpathy/llm-council.git "$dest"
					record_install_skipped "llm-council clone would run (dry-run)"
				elif run git git clone https://github.com/karpathy/llm-council.git "$dest"; then
					record_install_ok "llm-council cloned to $dest"
				else
					warn "llm-council clone failed."
					record_install_failed "llm-council clone failed"
				fi
			fi
		else
			warn "Skipping llm-council clone: git not found."
		fi
	fi
}

has_source_file() {
	local match
	match="$(find . \
		\( -name '.git' \
		-o -name 'node_modules' \
		-o -name 'vendor' \
		-o -name 'dist' \
		-o -name 'build' \
		-o -name 'coverage' \
		-o -name '.cache' \
		-o -name '.venv' \) -prune \
		-o "$@" -print -quit 2>/dev/null)"
	[[ -n "$match" ]]
}

detect_file_signals() {
	PY_FILES=0
	PY_NOTEBOOKS=0
	PY_CONFIGS=0
	JS_FILES=0
	JSX_FILES=0
	TS_FILES=0
	TSX_FILES=0
	HTML_FILES=0
	CSS_FILES=0
	NODE_MANIFESTS=0
	ANGULAR_MANIFESTS=0
	PHP_FILES=0
	PHP_MANIFESTS=0
	SHELL_FILES=0
	MARKDOWN_FILES=0
	GO_FILES=0
	GO_MANIFESTS=0
	RUST_FILES=0
	RUST_MANIFESTS=0
	DOTNET_FILES=0
	DOTNET_MANIFESTS=0
	has_node=0
	has_angular=0
	has_python=0
	has_php=0
	has_static_web=0
	has_shell=0
	has_markdown=0
	has_go=0
	has_rust=0
	has_dotnet=0

	local path base lower first_line
	while IFS= read -r -d '' path; do
		path="${path#./}"
		base="${path##*/}"
		lower="$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')"

		case "$base" in
		package.json) NODE_MANIFESTS=$((NODE_MANIFESTS + 1)) ;;
		angular.json) ANGULAR_MANIFESTS=$((ANGULAR_MANIFESTS + 1)) ;;
		composer.json) PHP_MANIFESTS=$((PHP_MANIFESTS + 1)) ;;
		go.mod) GO_MANIFESTS=$((GO_MANIFESTS + 1)) ;;
		Cargo.toml) RUST_MANIFESTS=$((RUST_MANIFESTS + 1)) ;;
		pyproject.toml | requirements*.txt | setup.py | setup.cfg | Pipfile | poetry.lock | uv.lock | ruff.toml | .ruff.toml | mypy.ini | pyrightconfig.json)
			PY_CONFIGS=$((PY_CONFIGS + 1))
			;;
		esac

		case "$lower" in
		*.py) PY_FILES=$((PY_FILES + 1)) ;;
		*.ipynb) PY_NOTEBOOKS=$((PY_NOTEBOOKS + 1)) ;;
		*.js) JS_FILES=$((JS_FILES + 1)) ;;
		*.jsx) JSX_FILES=$((JSX_FILES + 1)) ;;
		*.ts) TS_FILES=$((TS_FILES + 1)) ;;
		*.tsx) TSX_FILES=$((TSX_FILES + 1)) ;;
		*.html | *.htm) HTML_FILES=$((HTML_FILES + 1)) ;;
		*.css) CSS_FILES=$((CSS_FILES + 1)) ;;
		*.php) PHP_FILES=$((PHP_FILES + 1)) ;;
		*.md | *.markdown) MARKDOWN_FILES=$((MARKDOWN_FILES + 1)) ;;
		*.sh | *.bash | *.zsh) SHELL_FILES=$((SHELL_FILES + 1)) ;;
		*.go) GO_FILES=$((GO_FILES + 1)) ;;
		*.rs) RUST_FILES=$((RUST_FILES + 1)) ;;
		*.cs) DOTNET_FILES=$((DOTNET_FILES + 1)) ;;
		*.csproj | *.sln) DOTNET_MANIFESTS=$((DOTNET_MANIFESTS + 1)) ;;
		*)
			if [[ -x "$path" && "$base" != *.* ]]; then
				first_line="$(sed -n '1p' "$path" 2>/dev/null || true)"
				case "$first_line" in
				'#!'*sh) SHELL_FILES=$((SHELL_FILES + 1)) ;;
				esac
			fi
			;;
		esac
	done < <(find . \
		\( -name '.git' \
		-o -name 'node_modules' \
		-o -name 'vendor' \
		-o -name 'dist' \
		-o -name 'build' \
		-o -name 'coverage' \
		-o -name '.cache' \
		-o -name '.venv' \
		-o -name '__pycache__' \
		-o -name '.mypy_cache' \
		-o -name '.pytest_cache' \
		-o -name '.ruff_cache' \
		-o -name 'obsidian' \) -prune \
		-o -type f -print0 2>/dev/null)

	[[ "$NODE_MANIFESTS" -gt 0 ]] && has_node=1
	[[ "$ANGULAR_MANIFESTS" -gt 0 ]] && has_angular=1
	[[ "$PY_FILES" -gt 0 || "$PY_NOTEBOOKS" -gt 0 || "$PY_CONFIGS" -gt 0 ]] && has_python=1
	[[ "$PHP_FILES" -gt 0 || "$PHP_MANIFESTS" -gt 0 ]] && has_php=1
	[[ "$HTML_FILES" -gt 0 || "$CSS_FILES" -gt 0 || "$JS_FILES" -gt 0 || "$JSX_FILES" -gt 0 || "$TS_FILES" -gt 0 || "$TSX_FILES" -gt 0 ]] && has_static_web=1
	[[ "$SHELL_FILES" -gt 0 ]] && has_shell=1
	[[ "$MARKDOWN_FILES" -gt 0 ]] && has_markdown=1
	[[ "$GO_FILES" -gt 0 || "$GO_MANIFESTS" -gt 0 ]] && has_go=1
	[[ "$RUST_FILES" -gt 0 || "$RUST_MANIFESTS" -gt 0 ]] && has_rust=1
	[[ "$DOTNET_FILES" -gt 0 || "$DOTNET_MANIFESTS" -gt 0 ]] && has_dotnet=1
	return 0
}

create_agent_files() {
	log "Creating repo AI workflow files"

	local agents_md
	agents_md=$(
		cat <<'EOF_AGENTS'
# Agent instructions

Use this repository with minimal, verifiable changes.

Before editing:
- Read relevant `/codebase-wiki/` pages for durable repo memory.
- Use Graphify if available for broad repo discovery, unfamiliar areas, architecture decisions, or high-risk changes.
- Use code-review-graph for affected files, callers, dependents, tests, and blast radius.
- Before writing code, state the change plan and check that it directly solves the issue without creating broader side effects.
- Apply Ponytail discipline: skip unnecessary work, reuse existing code, prefer stdlib/native features, avoid new dependencies, and make the smallest safe change.
- Use Context7 for library/API/framework docs, setup, configuration, or unfamiliar APIs.
- Use Humanizer before finalizing user-facing prose, docs, README copy, PR descriptions, release notes, or other natural-language text when the skill is available.
- Use `$impeccable` for frontend design work when available: UI creation, visual polish, layout, typography, color, responsive behavior, UX copy, design-system drift, accessibility, or frontend design audits.
- When an Impeccable critique would benefit from subagents, ask: "Use subagents for the Impeccable critique? Reply yes to approve." Treat a plain "yes" as approval only when it directly answers that question; otherwise continue single-agent.
- Read `/agent/index.md` for workflow details.
- Read only the minimal files needed.

Repo memory workflow:
- Treat `graphify-out/` as generated analysis, not everyday context.
- Promote only stable, reusable facts into `codebase-wiki/`; do not copy raw Graphify output.
- Use `make wiki-ai` to draft concise wiki updates from Graphify/session context, then review the result before relying on it.
- After changes, update `codebase-wiki/` only when the task reveals durable architecture, testing, security, or integration knowledge.
- Use `make skills-check` after helper upgrades or when managed skills/plugins may be stale; use `make skills-update` only when the user asks to refresh them or an installer explicitly allows it.

Implementation rules:
- Prefer small diffs.
- Do not introduce new production dependencies unless clearly justified.
- Do not rewrite architecture unless explicitly asked.
- Do not remove validation, error handling, security checks, accessibility, or data-loss protection to make code shorter.
- Write or update tests for behavior changes.
- When touching frontend files, apply the frontend design touch gate in `/agent/coding-rules.md` even if no design skill is invoked.

Before final answer:
- Run the relevant verification command.
- Summarize files changed.
- Summarize tests/checks run.
- Explain remaining risks.

Session logging:
- Append each user query to `notes/queries.md` with the current date and keep prior entries intact.
- Add a `Session ID:` line immediately after each query entry when a terminal session ID is available.
- Append each user query and assistant reply to `notes/conversation-log.md` with the current date and keep prior entries intact.
- Include the current terminal session ID on each conversation-log `User:` line when available.
EOF_AGENTS
	)

	write_file "AGENTS.md" "$agents_md"

	local agent_index
	agent_index=$(
		cat <<'EOF_AGENT_INDEX'
# Agent workflow index

This folder contains durable instructions for AI coding agents. Keep `AGENTS.md` short and put details here.

Read order for non-trivial tasks:
1. `AGENTS.md`
2. `/agent/commands.md`
3. `/agent/context-strategy.md`
4. `/agent/coding-rules.md`
5. `/agent/verify.md`
6. Relevant pages in `/codebase-wiki/`

Default workflow:
1. Understand the task and expected behavior.
2. Read the relevant `/codebase-wiki/` page for durable repo memory.
3. Use Graphify and/or code-review-graph to identify the smallest affected area when the task is broad or unfamiliar.
4. Use Context7 for framework/library/API details.
5. Use `$impeccable` for frontend design tasks when available; run `/impeccable init` first when PRODUCT.md or DESIGN.md is missing.
6. Before writing code, state the planned files/behavior change and why it should solve the issue without broadening scope or creating new risk.
7. Apply Ponytail: reuse existing code, prefer stdlib/native capabilities, avoid new dependencies, and make the smallest safe change.
8. Add or update tests when behavior changes.
9. Run `make edited-ai` after edits so changed files are formatted, linted, and typechecked. Use `make verify-ai` for broader AI-safe repo checks when risk warrants it.
10. Run `make wiki-ai` when Graphify/session work reveals durable knowledge, then review the generated wiki sections.
11. Run `make skills-check` after helper upgrades or when managed skills/plugins may be stale; run `make skills-update` only on explicit refresh requests.
EOF_AGENT_INDEX
	)
	write_file "agent/index.md" "$agent_index"

	local commands_md
	commands_md=$(
		cat <<'EOF_COMMANDS'
# Repository commands for agents

Prefer existing project commands. These standard targets should exist after bootstrap:

```bash
make setup
make edited-ai
make wiki-ai
make lint-ai
make typecheck
make test
make security
make verify-ai
make skills-check
make skills-update
```

Useful AI tooling commands:

```bash
code-review-graph build
code-review-graph update
code-review-graph detect-changes --brief
make security-ai
make wiki-ai
make skills-check
make skills-update
```

Managed skills/plugins:

```bash
make skills-check
make skills-update
python3 vibe_scripts/sync-skills.py --check
python3 vibe_scripts/sync-skills.py --update --dry-run
```

Use `skills-check` after helper upgrades or when skill/plugin behavior seems stale. Use `skills-update` only when the user asks to refresh managed skills/plugins or an installer explicitly allows it.

Ponytail in Codex:

```bash
codex plugin marketplace add DietrichGebert/ponytail
codex
# Then open /plugins, install Ponytail, open /hooks, review/trust hooks, start a new thread.
```

Context7:

```bash
npx ctx7 setup
```

Use Context7 when work depends on external library, framework, API, setup, or configuration details.

RTK:

```bash
rtk git diff
rtk git status
rtk gain
```

The generated Codex RTK hook rewrites eligible literal read-only/noisy Bash commands through `rtk` when RTK is installed. Use `rtk proxy <cmd>` only when raw, unfiltered output is needed.

Impeccable for frontend design:

```bash
npx impeccable install -y --providers=codex --scope=project
/impeccable init
/impeccable polish the page or component
/impeccable audit the frontend area
npx impeccable detect src/
```

After installing Impeccable for Codex, restart Codex, open `/hooks`, approve the project hook, and start a new thread. Use `$impeccable` or `/impeccable` for frontend design creation, review, and refinement.
EOF_COMMANDS
	)
	write_file "agent/commands.md" "$commands_md"

	local verify_md
	verify_md=$(
		cat <<'EOF_VERIFY'
# Verification policy

Before saying work is done, run the narrowest reliable checks, then broader checks when risk warrants it.

Minimum for code changes:
- formatter/linter where available
- type checker/compiler where available
- relevant tests

Preferred final check:

```bash
make edited-ai
```

Security-sensitive changes:

```bash
make security
make security-ai
```

If a check is unavailable, say exactly which check was unavailable and why. Do not claim success without running or explaining the relevant checks.
EOF_VERIFY
	)
	write_file "agent/verify.md" "$verify_md"

	local coding_rules
	coding_rules=$(
		cat <<'EOF_CODING'
# Coding rules

Code-write planning gate:
Before creating or editing code, pause and state:
1. The exact problem being solved.
2. The files or modules expected to change.
3. Why this change should solve the issue.
4. What could break or be affected nearby.
5. The verification that will show the fix worked.

If this check shows the proposed change is too broad, risky, or only indirectly related to the issue, narrow the plan before editing.

Ponytail discipline:
1. Does this need to exist? If not, skip it.
2. Is it already in this codebase? Reuse it.
3. Does the standard library do it? Use that.
4. Does the platform/browser/framework provide it natively? Use that.
5. Is an existing dependency already installed that does it? Use that.
6. Can it safely be one line? Keep it one line.
7. Only then write the minimum custom code that works.

Never shorten code by removing:
- validation at trust boundaries
- security checks
- accessibility
- data-loss protection
- error handling that users or operators need
- tests that capture real behavior

Context7 rule:
Use Context7 whenever library/API documentation, framework behavior, setup, configuration, or generated code depends on external package details. Prefer exact library IDs and versions when known.

Impeccable rule:
Use `$impeccable` whenever the task changes or evaluates frontend design, including layout, typography, color, visual hierarchy, component polish, responsive behavior, motion, onboarding, UX copy, accessibility-facing UI, or design-system consistency. If PRODUCT.md or DESIGN.md is missing, initialize design context with `/impeccable init` before relying on design recommendations. Prefer `/impeccable shape` before building new UI, `/impeccable polish` for existing UI, `/impeccable audit` before shipping, and `npx impeccable detect` for deterministic design checks.

Frontend design touch gate:
Whenever editing frontend code or styles (`.tsx`, `.jsx`, `.html`, `.vue`, `.svelte`, `.astro`, `.css`, `.scss`, `.sass`, `.less`, UI-facing `.ts`/`.js`), first decide whether `$impeccable` or an Impeccable command should be used. Prefer text/CLI commands (`shape`, `polish`, `audit`, `critique`, `typeset`, `layout`, `colorize`, `adapt`, `harden`, `detect`) and do not use Live Mode or visual-overlay tooling unless the user asks for it. If Impeccable is unavailable, still apply these standards manually:
- preserve existing tokens, components, spacing scales, and conventions before inventing new ones
- match the surface type: dense, predictable, utilitarian product UI versus expressive brand/marketing UI
- avoid AI-design tells such as purple-blue gradients, generic Inter-only typography, nested cards, over-rounded controls, gray text on colored backgrounds, ornamental blobs, and card-heavy layouts
- check hierarchy, contrast, alignment, spacing rhythm, responsive behavior, text overflow, empty/loading/error states, keyboard/focus states, touch targets, and accessibility
- keep diffs small and use existing CSS/component patterns unless the task explicitly calls for a redesign
EOF_CODING
	)
	write_file "agent/coding-rules.md" "$coding_rules"

	local context_strategy
	context_strategy=$(
		cat <<'EOF_CONTEXT'
# Context strategy

Goal: minimize tokens while preserving enough context to make correct changes.

Before large edits:
1. Read the relevant `/codebase-wiki/` page for durable architecture/testing/security knowledge.
2. Use Graphify if available for high-level repo understanding when the task is broad, unfamiliar, or high-risk.
3. Use code-review-graph for affected files, callers, dependents, tests, and blast radius.
4. Read only files returned by the graph unless the task requires more.
5. Avoid loading unrelated docs, old generated code, build artifacts, or vendored dependencies.

After editing:
1. Run `code-review-graph detect-changes --brief` when available.
2. Run targeted tests.
3. Run `make wiki-ai` only when new stable knowledge was discovered, then review the generated wiki section before relying on it.
EOF_CONTEXT
	)
	write_file "agent/context-strategy.md" "$context_strategy"

	local wiki_index
	wiki_index=$(
		cat <<'EOF_WIKI_INDEX'
# Codebase wiki

This is durable, human-readable memory for AI agents and developers.

Pages:
- [Architecture](architecture.md)
- [Testing](testing.md)
- [Conventions](conventions.md)
- [Known issues](known-issues.md)
- [Risky areas](risky-areas.md)

Maintenance rule:
Use `make wiki-ai` to draft concise updates from repo guidance and Graphify output.
Promote only stable, reusable facts into this wiki.
Do not add temporary task notes, raw linter output, raw Graphify output, or speculation.
Review generated sections before relying on them for future work.
EOF_WIKI_INDEX
	)
	write_file "codebase-wiki/index.md" "$wiki_index"

	write_file "codebase-wiki/architecture.md" "# Architecture

Run \`make wiki-ai\` to draft this page from repo guidance and Graphify output."
	write_file "codebase-wiki/testing.md" "# Testing

Run \`make wiki-ai\` to draft this page from repo guidance and available Makefile targets."
	write_file "codebase-wiki/conventions.md" "# Conventions

Run \`make wiki-ai\` to draft this page from repo guidance."
	write_file "codebase-wiki/known-issues.md" "# Known issues

Run \`make wiki-ai\` to draft review candidates. Promote only confirmed durable issues."
	write_file "codebase-wiki/risky-areas.md" "# Risky areas

Run \`make wiki-ai\` to draft this page from repo guidance and Graphify output."

	local llm_council_md
	llm_council_md=$(
		cat <<'EOF_LLM_COUNCIL'
# Optional LLM Council review

Use a multi-model or multi-role review only for high-value decisions:
- architecture choices
- security-sensitive design
- database schema decisions
- API boundary design
- major refactors

Suggested roles:
1. implementation reviewer
2. security reviewer
3. maintainability reviewer
4. chair/synthesizer

Do not use this for routine edits; it increases tokens and cost.
EOF_LLM_COUNCIL
	)
	write_file "agent/llm-council.md" "$llm_council_md"
}

create_session_logging_files() {
	log "Creating session logging files"

	local query_header conversation_header
	query_header=$(
		cat <<'EOF_QUERY_HEADER'
# Query Log

Append each user query with the current date. Keep prior entries intact and add
a `Session ID:` line immediately after each query entry when available.
EOF_QUERY_HEADER
	)
	conversation_header=$(
		cat <<'EOF_CONVERSATION_HEADER'
# Conversation Log

Append each user query and assistant reply with the current date. Keep prior
entries intact and include the current terminal session ID on each `User:` line
when available.
EOF_CONVERSATION_HEADER
	)

	if [[ "$DRY_RUN" == "1" ]]; then
		log "Would create notes/queries.md and notes/conversation-log.md if missing"
		return 0
	fi

	mkdir -p notes
	touch notes/queries.md notes/conversation-log.md

	if [[ ! -s notes/queries.md ]]; then
		printf '%s\n' "$query_header" >notes/queries.md
		log "Initialized notes/queries.md"
	else
		log "notes/queries.md already exists; leaving prior entries intact"
	fi

	if [[ ! -s notes/conversation-log.md ]]; then
		printf '%s\n' "$conversation_header" >notes/conversation-log.md
		log "Initialized notes/conversation-log.md"
	else
		log "notes/conversation-log.md already exists; leaving prior entries intact"
	fi
}

ensure_cache_gitignored() {
	local marker="# VibeHelper local AI/dev tooling"
	local block
	block=$'# VibeHelper local AI/dev tooling\n*.bak\n*.bak.*\n.cache/\n.agents/\n.claude/\n.codex/\n.mcp.json\nAGENTS.md\nCLAUDE.md\nMakefile\nagent/\nagents/\ncodebase-wiki/\ngraphify-out/\nnode_modules/\nnotes/\nobsidian/\nvendor/\nvibe_scripts/\nskills-lock.json\nbiome.json\n'
	if [[ "$DRY_RUN" == "1" ]]; then
		log "Would ensure .gitignore has VibeHelper local-file entries"
		return 0
	fi

	touch .gitignore
	if ! grep -Fq "$marker" .gitignore; then
		backup_file .gitignore
		printf '\n%s' "$block" >>.gitignore
		log "Updated .gitignore"
	fi
}

create_makefile() {
	log "Creating or updating Makefile targets"
	detect_file_signals

	local marker="# >>> ai-quality targets >>>"
	local endmarker="# <<< ai-quality targets <<<"

	local setup_cmd='@echo "No setup command configured yet."'
	local lint_cmd='@echo "No lint command configured yet."'
	local typecheck_cmd='@echo "No typecheck command configured yet."'
	local test_cmd='@echo "No test command configured yet."'
	# shellcheck disable=SC2016
	local security_cmd='mkdir -p .cache/semgrep; cert=""; for p in /opt/homebrew/etc/ca-certificates/cert.pem /usr/local/etc/ca-certificates/cert.pem /opt/homebrew/etc/openssl@3/cert.pem /usr/local/etc/openssl@3/cert.pem; do [ -r "$$p" ] && cert="$$p" && break; done; if command -v semgrep >/dev/null 2>&1; then if [ -n "$$cert" ]; then SSL_CERT_FILE="$$cert" SEMGREP_LOG_FILE=.cache/semgrep/semgrep.log SEMGREP_SETTINGS_FILE=.cache/semgrep/settings.yml SEMGREP_VERSION_CACHE_PATH=.cache/semgrep/version-cache semgrep --version >/dev/null 2>&1 && SSL_CERT_FILE="$$cert" SEMGREP_LOG_FILE=.cache/semgrep/semgrep.log SEMGREP_SETTINGS_FILE=.cache/semgrep/settings.yml SEMGREP_VERSION_CACHE_PATH=.cache/semgrep/version-cache semgrep scan || echo "semgrep unavailable or failing; skipping security scan"; else SEMGREP_LOG_FILE=.cache/semgrep/semgrep.log SEMGREP_SETTINGS_FILE=.cache/semgrep/settings.yml SEMGREP_VERSION_CACHE_PATH=.cache/semgrep/version-cache semgrep --version >/dev/null 2>&1 && SEMGREP_LOG_FILE=.cache/semgrep/semgrep.log SEMGREP_SETTINGS_FILE=.cache/semgrep/settings.yml SEMGREP_VERSION_CACHE_PATH=.cache/semgrep/version-cache semgrep scan || echo "semgrep unavailable or failing; skipping security scan"; fi; else echo "semgrep not installed; skipping security scan"; fi'

	local -a setup_parts=()
	local -a lint_parts=()
	local -a typecheck_parts=()
	local -a test_parts=()
	if [[ "$has_python" -eq 1 ]]; then
		setup_parts+=('@echo "Install Python dependencies using the project package manager: uv, poetry, pip, or pip-tools."')
		lint_parts+=('command -v ruff >/dev/null 2>&1 && ruff check . || echo "ruff not installed; skipping Python lint"')
		typecheck_parts+=('command -v mypy >/dev/null 2>&1 && mypy . || command -v pyright >/dev/null 2>&1 && pyright . || echo "mypy/pyright not installed; skipping Python typecheck"')
		test_parts+=('command -v pytest >/dev/null 2>&1 && pytest -q || echo "pytest not installed; skipping Python tests"')
	fi
	if [[ "$has_node" -eq 1 ]]; then
		setup_parts+=('npm install')
		lint_parts+=('npm run lint --if-present')
		typecheck_parts+=('npm run typecheck --if-present')
		test_parts+=('npm test --if-present')
	elif [[ "$has_static_web" -eq 1 ]]; then
		lint_parts+=('command -v biome >/dev/null 2>&1 && biome check . || echo "biome not installed; skipping web lint"')
	fi
	if [[ "$HTML_FILES" -gt 0 ]]; then
		lint_parts+=('command -v htmlhint >/dev/null 2>&1 && htmlhint --ignore "**/.git/**,**/.agents/**,**/.claude/**,**/.codex/**,**/node_modules/**,**/vendor/**,**/dist/**,**/build/**,**/coverage/**,**/graphify-out/**,**/.cache/**,**/.venv/**" "**/*.html" || echo "htmlhint not installed; skipping HTML lint"')
	fi
	if [[ "$has_markdown" -eq 1 ]]; then
		lint_parts+=('command -v markdownlint-cli2 >/dev/null 2>&1 && markdownlint-cli2 "**/*.md" "**/*.markdown" "#**/.agents/**" "#**/.claude/**" "#**/.codex/**" "#**/node_modules/**" "#**/vendor/**" "#**/dist/**" "#**/build/**" "#**/coverage/**" "#**/graphify-out/**" "#**/.git/**" "#**/.cache/**" "#**/.venv/**" "#**/obsidian/**" || echo "markdownlint-cli2 not installed; skipping Markdown lint"')
	fi
	if [[ "$has_php" -eq 1 ]]; then
		setup_parts+=('command -v composer >/dev/null 2>&1 && [ -f composer.json ] && composer install || echo "composer not installed or composer.json missing; skipping PHP dependency install"')
		lint_parts+=('if command -v php >/dev/null 2>&1; then find . \( -name .agents -o -name .claude -o -name .codex -o -name vendor -o -name node_modules -o -name dist -o -name build -o -name coverage -o -name graphify-out -o -name .git -o -name .cache -o -name .venv \) -prune -o -name "*.php" -print0 | xargs -0 -n1 php -l; else echo "php not installed; skipping PHP syntax lint"; fi')
		lint_parts+=('if [ -x vendor/bin/phpcs ]; then vendor/bin/phpcs --standard=PSR12 --extensions=php --ignore=.agents/*,.claude/*,.codex/*,vendor/*,node_modules/*,dist/*,build/*,coverage/*,graphify-out/*,.git/*,.cache/*,.venv/* .; else echo "vendor/bin/phpcs not installed; skipping PHPCS"; fi')
		typecheck_parts+=('if [ -x vendor/bin/phpstan ]; then find . \( -name .agents -o -name .claude -o -name .codex -o -name vendor -o -name node_modules -o -name dist -o -name build -o -name coverage -o -name graphify-out -o -name .git -o -name .cache -o -name .venv \) -prune -o -name "*.php" -print0 | xargs -0 vendor/bin/phpstan analyse --memory-limit=1G --no-progress --; else echo "vendor/bin/phpstan not installed; skipping PHPStan"; fi')
	fi
	if [[ "$has_shell" -eq 1 ]]; then
		lint_parts+=('if command -v shellcheck >/dev/null 2>&1; then find . \( -name .agents -o -name .claude -o -name .codex -o -name vendor -o -name node_modules -o -name dist -o -name build -o -name coverage -o -name graphify-out -o -name .git -o -name .cache -o -name .venv \) -prune -o \( -name "*.sh" -o -name "*.bash" -o -name "*.zsh" \) -print0 | xargs -0 -r shellcheck; else echo "shellcheck not installed; skipping shell lint"; fi')
	fi
	if [[ "$has_go" -eq 1 ]]; then
		setup_parts+=('go mod download')
		lint_parts+=('go vet ./...')
		typecheck_parts+=('go test ./...')
		test_parts+=('go test ./...')
	fi
	if [[ "$has_rust" -eq 1 ]]; then
		setup_parts+=('cargo fetch')
		lint_parts+=('cargo clippy --all-targets --all-features -- -D warnings')
		typecheck_parts+=('cargo check')
		test_parts+=('cargo test')
	fi
	if [[ "$has_dotnet" -eq 1 ]]; then
		setup_parts+=('dotnet restore')
		lint_parts+=('dotnet format --verify-no-changes')
		typecheck_parts+=('dotnet build')
		test_parts+=('dotnet test')
	fi

	join_make_cmds() {
		if [[ "$#" -eq 0 ]]; then
			return 1
		fi
		local joined="$1"
		shift
		local part
		for part in "$@"; do
			joined="$joined; $part"
		done
		printf '%s\n' "$joined"
	}
	[[ "${#setup_parts[@]}" -gt 0 ]] && setup_cmd="$(join_make_cmds "${setup_parts[@]}")"
	[[ "${#lint_parts[@]}" -gt 0 ]] && lint_cmd="$(join_make_cmds "${lint_parts[@]}")"
	[[ "${#typecheck_parts[@]}" -gt 0 ]] && typecheck_cmd="$(join_make_cmds "${typecheck_parts[@]}")"
	[[ "${#test_parts[@]}" -gt 0 ]] && test_cmd="$(join_make_cmds "${test_parts[@]}")"

	local block
	block=$(
		cat <<EOF_MAKE
$marker
.PHONY: setup lint typecheck test security verify edited-ai wiki-ai skills-check skills-update agent-verify
export PATH := \$(HOME)/.local/bin:\$(PATH)

setup:
	$setup_cmd

lint:
	$lint_cmd

typecheck:
	$typecheck_cmd

test:
	$test_cmd

security:
	$security_cmd

verify: lint typecheck test security

edited-ai:
	python3 vibe_scripts/agent-check-edited.py

wiki-ai:
	python3 vibe_scripts/update-codebase-wiki.py

skills-check:
	python3 vibe_scripts/sync-skills.py --check

skills-update:
	python3 vibe_scripts/sync-skills.py --update

agent-verify:
	./vibe_scripts/agent-verify.sh
$endmarker
EOF_MAKE
	)

	if [[ -f Makefile ]]; then
		if grep -Fq "$marker" Makefile; then
			if [[ "$FORCE" == "1" ]]; then
				backup_file Makefile
				if [[ "$DRY_RUN" == "1" ]]; then
					log "Would replace managed Makefile block"
				else
					python3 - <<PY
from pathlib import Path
path = Path('Makefile')
text = path.read_text()
start = text.index('$marker')
end = text.index('$endmarker') + len('$endmarker')
path.write_text(text[:start] + '''$block''' + text[end:])
PY
					log "Updated managed Makefile block"
				fi
			else
				log "Makefile already has managed block; use --force to regenerate it."
			fi
		else
			local wiki_marker="# >>> codebase-wiki target >>>"
			local wiki_block
			wiki_block=$(
				cat <<'EOF_WIKI_MAKE'
# >>> codebase-wiki target >>>
.PHONY: wiki-ai skills-check skills-update
wiki-ai:
	python3 vibe_scripts/update-codebase-wiki.py

skills-check:
	python3 vibe_scripts/sync-skills.py --check

skills-update:
	python3 vibe_scripts/sync-skills.py --update
# <<< codebase-wiki target <<<
EOF_WIKI_MAKE
			)
			if grep -Eq '^wiki-ai:' Makefile; then
				log "Makefile already has wiki-ai target."
			elif grep -Fq "$wiki_marker" Makefile; then
				log "Makefile already has managed codebase-wiki target block."
			elif [[ "$DRY_RUN" == "1" ]]; then
				log "Would append wiki-ai target to existing Makefile"
			else
				backup_file Makefile
				{
					printf '\n'
					printf '%s\n' "$wiki_block"
				} >>Makefile
				log "Appended wiki-ai target to existing Makefile"
			fi
			log "Makefile already exists; leaving other targets unchanged. Run part2.sh to install, wire, and apply safe fixes."
		fi
	else
		log "No Makefile found; not creating placeholder quality targets. Run part2.sh to install, wire, and apply safe fixes."
	fi
}

create_codebase_wiki_script() {
	local script
	script=$(
		cat <<'EOF_WIKI_SCRIPT'
#!/usr/bin/env python3
"""Draft concise codebase-wiki pages from repo guidance and Graphify output."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WIKI_DIR = ROOT / "codebase-wiki"
GRAPH_REPORT = ROOT / "graphify-out" / "GRAPH_REPORT.md"
AGENTS_MD = ROOT / "AGENTS.md"


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def clean_line(line: str) -> str:
    line = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", line)
    line = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line)
    tick = chr(96)
    line = re.sub(tick + r"([^" + tick + r"]+)" + tick, r"\1", line)
    return line.strip()


def markdown_section(text: str, heading: str) -> list[str]:
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
    if not match:
        return []
    start = match.end()
    next_match = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return [clean_line(line) for line in text[start:end].splitlines() if line.strip()]


def heading_block(text: str, heading: str, limit: int = 12) -> list[str]:
    lines = markdown_section(text, heading)
    bullets: list[str] = []
    for line in lines:
        if line.startswith("- "):
            bullets.append(line)
        elif line and not line.startswith("#"):
            bullets.append(f"- {line}")
        if len(bullets) >= limit:
            break
    return bullets


def graph_communities(text: str, limit: int = 18) -> list[str]:
    communities = []
    for line in text.splitlines():
        if line.startswith("### Community "):
            communities.append("- " + clean_line(line.removeprefix("### ")))
        if len(communities) >= limit:
            break
    return communities


def keep(items: list[str], limit: int, placeholder: str) -> str:
    trimmed = [item for item in items if item and not item.startswith("Cohesion:")][:limit]
    if not trimmed:
        return f"- {placeholder}"
    return "\n".join(trimmed)


def makefile_targets() -> list[str]:
    try:
        result = subprocess.run(
            ["make", "-qp"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    output = result.stdout
    found = set()
    for line in output.splitlines():
        if ":" not in line or line.startswith("\t") or line.startswith("."):
            continue
        name = line.split(":", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            found.add(name)
    preferred = [
        "edited-ai",
        "wiki-ai",
        "skills-check",
        "skills-update",
        "verify-ai",
        "lint-ai",
        "typecheck-ai",
        "security-ai",
        "verify",
        "lint",
        "typecheck",
        "test",
        "security",
    ]
    return [target for target in preferred if target in found]


def auto_block(key: str, body: str) -> str:
    return f"<!-- BEGIN AUTO-WIKI:{key} -->\n{body.rstrip()}\n<!-- END AUTO-WIKI:{key} -->\n"


def replace_section(path: Path, title: str, key: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = read_text(path)
    block = auto_block(key, body)
    begin = f"<!-- BEGIN AUTO-WIKI:{key} -->"
    end = f"<!-- END AUTO-WIKI:{key} -->"
    if begin in current and end in current:
        pattern = re.compile(rf"{re.escape(begin)}.*?{re.escape(end)}\n?", re.DOTALL)
        updated = pattern.sub(block, current)
    elif "TODO:" in current or "Run make wiki-ai" in current or len(current.strip()) < 140:
        updated = f"# {title}\n\n{block}"
    elif current.strip():
        updated = current.rstrip() + "\n\n" + block
    else:
        updated = f"# {title}\n\n{block}"
    path.write_text(updated, encoding="utf-8")


def build_index() -> str:
    return """# Codebase wiki

This is durable, human-readable memory for AI agents and developers.

Pages:
- [Architecture](architecture.md)
- [Testing](testing.md)
- [Conventions](conventions.md)
- [Known issues](known-issues.md)
- [Risky areas](risky-areas.md)

Maintenance rule:
- Use make wiki-ai to draft concise updates from repo guidance and Graphify output.
- Promote only stable, reusable facts into this wiki.
- Do not add temporary task notes, raw linter output, raw Graphify output, or speculation.
- Review generated sections before relying on them for future work.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    agents = read_text(AGENTS_MD)
    graph = read_text(GRAPH_REPORT)
    targets = [f"- make {target}" for target in makefile_targets()]
    graph_summary = markdown_section(graph, "Summary")[:6]
    hubs = markdown_section(graph, "Community Hubs (Navigation)")[:16]
    god_nodes = markdown_section(graph, "God Nodes (most connected - your core abstractions)")[:12]
    hyperedges = markdown_section(graph, "Hyperedges (group relationships)")[:12]
    surprising = markdown_section(graph, "Surprising Connections (you probably didn't know these)")[:8]
    communities = graph_communities(graph)

    risk_terms = ("security", "csrf", "tracking", "dashboard", "endpoint", "email", "signup", "auth", "config", "rate")
    risk_items = []
    for item in hubs + hyperedges + communities:
        if any(term in item.lower() for term in risk_terms):
            risk_items.append(item)

    pages = {
        "architecture.md": (
            "Architecture",
            "architecture",
            f"""## Generated Repo Map

Source: AGENTS.md and graphify-out/GRAPH_REPORT.md.

### Project Shape
{keep(heading_block(agents, "Project Structure & Module Organization"), 10, "No project-structure guidance found in AGENTS.md.")}

### Graphify Summary
{keep(graph_summary, 6, "Run Graphify to populate repository structure.")}

### Main Areas
{keep(hubs, 16, "No Graphify community hubs found.")}

### Core Abstractions
{keep(god_nodes, 12, "No core abstraction list found.")}

### Group Relationships
{keep(hyperedges, 12, "No grouped relationships found.")}
""",
        ),
        "testing.md": (
            "Testing",
            "testing",
            f"""## Generated Verification Memory

Source: AGENTS.md and current Makefile targets.

### Manual Checks
{keep(heading_block(agents, "Testing Guidelines"), 10, "No testing guidance found in AGENTS.md.")}

### Development Commands
{keep(heading_block(agents, "Build, Test, and Development Commands"), 10, "No development commands found in AGENTS.md.")}

### Make Targets
{keep(targets, 12, "No Makefile quality targets detected.")}

### Agent Rule
- Prefer make edited-ai after edits so formatter, linter, and typechecker output stays capped for AI use.
- Use make verify-ai when the change is broad, security-sensitive, or crosses multiple workflows.
- Use make wiki-ai after Graphify or substantial discovery work to draft durable memory updates.
""",
        ),
        "conventions.md": (
            "Conventions",
            "conventions",
            f"""## Generated Conventions

Source: AGENTS.md.

### Code Style
{keep(heading_block(agents, "Coding Style & Naming Conventions"), 12, "No coding-style guidance found in AGENTS.md.")}

### Repo Memory
{keep(heading_block(agents, "Repo Memory Workflow"), 12, "No repo-memory workflow found in AGENTS.md.")}

### Security Conventions
{keep(heading_block(agents, "Security & Configuration Tips"), 8, "No security conventions found in AGENTS.md.")}
""",
        ),
        "known-issues.md": (
            "Known issues",
            "known-issues",
            f"""## Generated Known-Issue Candidates

Source: graphify-out/GRAPH_REPORT.md. These are review prompts, not confirmed bugs.

### Surprising Connections To Review
{keep(surprising, 8, "No surprising connections found.")}

### Maintenance Rule
- Move an item into a permanent known issue only after a task confirms the behavior and its impact.
- Remove stale items when the underlying code or workflow changes.
""",
        ),
        "risky-areas.md": (
            "Risky areas",
            "risky-areas",
            f"""## Generated Risk Map

Source: AGENTS.md and graphify-out/GRAPH_REPORT.md.

### Security Baseline
{keep(heading_block(agents, "Security & Configuration Tips"), 8, "No security baseline found in AGENTS.md.")}

### Areas Requiring Extra Care
{keep(list(dict.fromkeys(risk_items)), 18, "No Graphify risk candidates found.")}

### Agent Rule
- For these areas, read the relevant implementation files and run targeted checks before finalizing.
- Do not weaken validation, CSRF, auth, rate limiting, escaping, logging, or private-file protections.
""",
        ),
    }

    if args.dry_run:
        print(build_index().rstrip())
        for filename, (_title, key, body) in pages.items():
            print(f"\n--- codebase-wiki/{filename} ---")
            print(auto_block(key, body).rstrip())
        return 0

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    (WIKI_DIR / "index.md").write_text(build_index(), encoding="utf-8")
    for filename, (title, key, body) in pages.items():
        replace_section(WIKI_DIR / filename, title, key, body)
    print("Updated codebase-wiki generated sections.")
    if not GRAPH_REPORT.exists():
        print("Graphify report not found; run Graphify for richer repo memory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
EOF_WIKI_SCRIPT
	)
	write_file "vibe_scripts/update-codebase-wiki.py" "$script" "0755"
}

create_skill_sync_script() {
	local src
	for src in "$HELPER_ROOT/templates/vibe_scripts/sync-skills.py" "$HELPER_ROOT/../templates/vibe_scripts/sync-skills.py"; do
		if [[ -f "$src" ]]; then
			if [[ "$DRY_RUN" == "1" ]]; then
				log "Would copy $src -> vibe_scripts/sync-skills.py"
			else
				mkdir -p vibe_scripts
				cp "$src" vibe_scripts/sync-skills.py
				chmod 0755 vibe_scripts/sync-skills.py
				log "Wrote vibe_scripts/sync-skills.py"
			fi
			return 0
		fi
	done
	warn "sync-skills.py template not found; skills-check/skills-update targets will be skipped until it exists."
}

create_skills_lock() {
	local src
	for src in "$HELPER_ROOT/templates/skills-lock.json" "$HELPER_ROOT/../templates/skills-lock.json"; do
		if [[ -f "$src" ]]; then
			if [[ -f skills-lock.json && "$FORCE" != "1" ]]; then
				log "skills-lock.json already exists; leaving it unchanged."
				return 0
			fi
			if [[ "$DRY_RUN" == "1" ]]; then
				log "Would copy $src -> skills-lock.json"
			else
				[[ -f skills-lock.json ]] && backup_file skills-lock.json
				cp "$src" skills-lock.json
				log "Wrote skills-lock.json"
			fi
			return 0
		fi
	done
	warn "skills-lock.json template not found; sync-skills.py will use built-in defaults."
}

create_verify_script() {
	local script
	script=$(
		cat <<'EOF_VERIFY_SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

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
  make test
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
EOF_VERIFY_SCRIPT
	)
	write_file "vibe_scripts/agent-verify.sh" "$script" "0755"
}

create_ai_quality_wrapper_script() {
	local script
	script=$(
		cat <<'EOF_AI_WRAPPER'
#!/usr/bin/env python3
"""Run a quality command with AI-safe output."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return value.strip("-") or "quality-command"


def trim_lines(text: str, max_lines: int) -> tuple[list[str], bool]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return lines, False
    head_count = max_lines // 2
    tail_count = max_lines - head_count
    return lines[:head_count] + ["... output truncated ..."] + lines[-tail_count:], True


def summarize_known_success(output: str, returncode: int) -> list[str] | None:
    if returncode != 0:
        return None
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if lines and all(line.startswith("No syntax errors detected in ") for line in lines):
        return [f"PHP syntax check passed for {len(lines)} files."]
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a command and print a capped AI-safe summary.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--log-dir", default=".cache/ai-quality")
    parser.add_argument("--max-lines", type=int, default=30)
    parser.add_argument("--shell", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing command after --")
    if args.max_lines < 4:
        parser.error("--max-lines must be at least 4")
    return args


def main() -> int:
    args = parse_args()
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(args.label)}.log"
    if args.shell:
        command_display = args.command[0]
        run_command: str | list[str] = args.command[0]
    else:
        command_display = " ".join(args.command)
        run_command = args.command
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    completed = subprocess.run(run_command, shell=args.shell, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, check=False)
    output = completed.stdout or ""
    log_path.write_text(f"$ {command_display}\nexit_code={completed.returncode}\n\n{output}", encoding="utf-8")
    status = "ok" if completed.returncode == 0 else f"failed ({completed.returncode})"
    print(f"[ai-quality] {args.label}: {status}")
    print(f"[ai-quality] full log: {log_path}")
    known_success = summarize_known_success(output, completed.returncode)
    if known_success is not None:
        print("[ai-quality] summary:")
        for line in known_success:
            print(line)
        return completed.returncode
    summary_lines, truncated = trim_lines(output, args.max_lines)
    if summary_lines:
        print(f"[ai-quality] capped output ({len(summary_lines)} lines):")
        for line in summary_lines:
            print(line)
    else:
        print("[ai-quality] no output")
    if truncated:
        print(f"[ai-quality] output was truncated for the AI transcript; inspect {log_path} for the full output.")
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
EOF_AI_WRAPPER
	)
	write_file "vibe_scripts/ai-quality-wrapper.py" "$script" "0755"
}

create_edited_check_script() {
	local script
	script=$(
		cat <<'EOF_EDITED_CHECK'
#!/usr/bin/env python3
"""Format and verify files edited by the agent with capped AI output."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


EXCLUDED_DIRS = {".agents", ".cache", ".claude", ".codex", ".git", ".venv", "build", "coverage", "dist", "graphify-out", "node_modules", "obsidian", "vendor"}
EXCLUDED_FILES = {".mcp.json"}
BIOME_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".css", ".json", ".jsonc"}
HTML_EXTS = {".html", ".htm"}
MARKDOWN_EXTS = {".md", ".markdown"}
FRONTEND_DESIGN_EXTS = {
    ".astro",
    ".css",
    ".htm",
    ".html",
    ".js",
    ".jsx",
    ".less",
    ".sass",
    ".scss",
    ".svelte",
    ".ts",
    ".tsx",
    ".vue",
}
PHP_EXTS = {".php"}
SHELL_EXTS = {".sh", ".bash", ".zsh"}


def run_capture(args: list[str], cwd: Path) -> str:
    completed = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return completed.stdout


def repo_root() -> Path:
    output = run_capture(["git", "rev-parse", "--show-toplevel"], Path.cwd()).strip()
    return Path(output) if output else Path.cwd()


def git_changed_files(root: Path) -> list[str]:
    changed = run_capture(["git", "diff", "--name-only", "--diff-filter=ACMRTUXB", "HEAD"], root).splitlines()
    untracked = run_capture(["git", "ls-files", "--others", "--exclude-standard"], root).splitlines()
    return sorted(set(changed + untracked))


def is_excluded(path: str) -> bool:
    parts = Path(path).parts
    return path in EXCLUDED_FILES or any(part in EXCLUDED_DIRS for part in parts)


def existing_project_files(root: Path, files: list[str]) -> list[str]:
    result: list[str] = []
    for file in files:
        normalized = file.strip()
        if not normalized or is_excluded(normalized):
            continue
        full_path = (root / normalized).resolve()
        try:
            full_path.relative_to(root.resolve())
        except ValueError:
            continue
        if full_path.is_file():
            result.append(normalized)
    return sorted(set(result))


def split_by_ext(files: list[str]) -> dict[str, list[str]]:
    groups = {"biome": [], "html": [], "markdown": [], "design": [], "php": [], "shell": []}
    for file in files:
        suffix = Path(file).suffix.lower()
        if suffix in BIOME_EXTS:
            groups["biome"].append(file)
        if suffix in HTML_EXTS:
            groups["html"].append(file)
        if suffix in MARKDOWN_EXTS:
            groups["markdown"].append(file)
        if suffix in FRONTEND_DESIGN_EXTS:
            groups["design"].append(file)
        if suffix in PHP_EXTS:
            groups["php"].append(file)
        if suffix in SHELL_EXTS:
            groups["shell"].append(file)
    return groups


def have_path(root: Path, path: str) -> bool:
    return (root / path).exists()


def have_command(command: str, root: Path) -> bool:
    return shutil.which(command) is not None


def npm_has_script(root: Path, name: str) -> bool:
    package_json = root / "package.json"
    if not package_json.is_file():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    scripts = data.get("scripts")
    return isinstance(scripts, dict) and isinstance(scripts.get(name), str)


def local_impeccable_command(root: Path) -> list[str] | None:
    local_bin = root / "node_modules" / ".bin" / "impeccable"
    if local_bin.exists():
        return ["npx", "--no-install", "impeccable"]
    executable = shutil.which("impeccable")
    if executable:
        return [executable]
    return None


def markdownlint_command(root: Path) -> list[str] | None:
    local_bin = root / "node_modules" / ".bin" / "markdownlint-cli2"
    if local_bin.exists():
        return ["npx", "--no-install", "markdownlint-cli2"]
    executable = shutil.which("markdownlint-cli2")
    if executable:
        return [executable]
    return None


def quote_files(files: list[str]) -> str:
    return " ".join(shlex.quote(file) for file in files)


def run_wrapped(root: Path, label: str, command: list[str], *, shell: bool = False, max_lines: int = 24) -> int:
    wrapper = root / "vibe_scripts" / "ai-quality-wrapper.py"
    args = [sys.executable, str(wrapper), "--label", label, "--max-lines", str(max_lines)]
    if shell:
        args.append("--shell")
    args.append("--")
    args.extend(command)
    return subprocess.run(args, cwd=root, check=False).returncode


def run_phase(root: Path, phase: str, commands: list[tuple[str, list[str], bool, int, bool]]) -> int:
    if not commands:
        print(f"[edited-check] {phase}: no applicable commands")
        return 0
    print(f"[edited-check] {phase}")
    sys.stdout.flush()
    failed = 0
    for label, command, shell, max_lines, allow_failure in commands:
        code = run_wrapped(root, label, command, shell=shell, max_lines=max_lines)
        if code != 0 and not allow_failure:
            failed = 1
    return failed


def build_commands(root: Path, groups: dict[str, list[str]]) -> tuple[list[tuple[str, list[str], bool, int, bool]], list[tuple[str, list[str], bool, int, bool]], list[tuple[str, list[str], bool, int, bool]]]:
    format_cmds: list[tuple[str, list[str], bool, int, bool]] = []
    lint_cmds: list[tuple[str, list[str], bool, int, bool]] = []
    type_cmds: list[tuple[str, list[str], bool, int, bool]] = []

    if groups["biome"] and have_path(root, "node_modules/.bin/biome"):
        files = quote_files(groups["biome"])
        format_cmds.append(("format-biome-edited", [f"npx biome check --write {files}"], True, 20, False))
        lint_cmds.append(("lint-biome-edited", [f"npx biome check --colors=off --max-diagnostics=20 {files}"], True, 24, False))
    if groups["html"] and have_path(root, "node_modules/.bin/htmlhint"):
        files = quote_files(groups["html"])
        lint_cmds.append(("lint-html-edited", [f"npx htmlhint --nocolor --format compact {files}"], True, 24, False))
    markdownlint = markdownlint_command(root)
    if groups["markdown"] and markdownlint:
        files = quote_files(groups["markdown"])
        command = " ".join(shlex.quote(part) for part in markdownlint)
        format_cmds.append(("format-markdownlint-edited", [f"{command} --fix {files}"], True, 20, False))
        lint_cmds.append(("lint-markdown-edited", [f"{command} {files}"], True, 24, False))
    impeccable = local_impeccable_command(root)
    if groups["design"] and impeccable:
        detector_args = " ".join(shlex.quote(part) for part in impeccable + ["detect", *groups["design"]])
        lint_cmds.append(("design-impeccable-edited", [detector_args], True, 30, False))
    if groups["php"]:
        files = quote_files(groups["php"])
        syntax_loop = "for file in " + files + "; do php -l \"$file\"; done"
        if have_command("php", root):
            lint_cmds.append(("lint-php-syntax-edited", [syntax_loop], True, 20, False))
        if have_path(root, "vendor/bin/phpcbf"):
            format_cmds.append(("format-phpcbf-edited", [f"vendor/bin/phpcbf --standard=PSR12 --extensions=php {files} || true"], True, 20, True))
        if have_path(root, "vendor/bin/phpcs"):
            lint_cmds.append(("lint-phpcs-edited", [f"vendor/bin/phpcs --standard=PSR12 --extensions=php --report=summary -q {files}"], True, 24, False))
        if have_path(root, "vendor/bin/phpstan"):
            type_cmds.append(("type-phpstan-edited", [f"vendor/bin/phpstan analyse --memory-limit=1G --no-progress --error-format=table -- {files}"], True, 30, False))
    if groups["shell"]:
        files = quote_files(groups["shell"])
        if have_command("shfmt", root):
            format_cmds.append(("format-shfmt-edited", [f"shfmt -w {files}"], True, 20, False))
        if have_command("shellcheck", root):
            lint_cmds.append(("lint-shellcheck-edited", [f"shellcheck {files}"], True, 24, False))
    if (groups["biome"] or groups["html"]) and npm_has_script(root, "typecheck"):
        type_cmds.append(("type-npm-edited", ["npm run typecheck"], False, 30, False))
    return format_cmds, lint_cmds, type_cmds


def main() -> int:
    parser = argparse.ArgumentParser(description="Format, lint, and typecheck edited files with capped AI output.")
    parser.add_argument("files", nargs="*", help="Specific files to check. Defaults to Git changed/untracked files.")
    args = parser.parse_args()
    root = repo_root()
    os.chdir(root)
    files = existing_project_files(root, args.files if args.files else git_changed_files(root))
    groups = split_by_ext(files)
    relevant = sorted(set(groups["biome"] + groups["html"] + groups["markdown"] + groups["design"] + groups["php"] + groups["shell"]))
    print(f"[edited-check] root: {root}")
    if not relevant:
        print("[edited-check] no edited frontend/Markdown/PHP/shell files to check")
        return 0
    print(f"[edited-check] files: {len(relevant)}")
    for file in relevant[:30]:
        print(f"  - {file}")
    if len(relevant) > 30:
        print(f"  ... {len(relevant) - 30} more files hidden from AI output")
    sys.stdout.flush()
    format_cmds, lint_cmds, type_cmds = build_commands(root, groups)
    failed = 0
    failed |= run_phase(root, "format edited files", format_cmds)
    failed |= run_phase(root, "lint edited files", lint_cmds)
    failed |= run_phase(root, "typecheck edited files", type_cmds)
    if failed:
        print("[edited-check] errors found after formatting/linting/typechecking edited files")
        return 1
    print("[edited-check] edited-file checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF_EDITED_CHECK
	)
	write_file "vibe_scripts/agent-check-edited.py" "$script" "0755"
}

create_codex_hooks_templates() {
	if [[ "$CREATE_CODEX_HOOKS" != "1" ]]; then
		log "Skipping Codex hook templates (--no-codex-hooks)."
		return 0
	fi

	log "Creating Codex hook templates with Git-writing command blockers"
	if [[ "$CREATE_RTK_HOOK" == "1" ]]; then
		warn "RTK PreToolUse hook is enabled by default. It rewrites eligible literal read-only/noisy Bash commands through 'rtk' when RTK is installed."
		warn "Use --no-rtk-hook to skip it. Codex still requires /hooks review/trust before project hooks run."
		if ! have_cli rtk; then
			warn "rtk was not found after setup. The generated hook will pass commands through until RTK is installed and on PATH."
		fi
	fi

	local pre_hook
	pre_hook=$(
		cat <<'EOF_PRE_HOOK'
#!/usr/bin/env python3
"""Conservative Codex PreToolUse hook for Bash commands.

Purpose:
- Block Git-writing commands that stage, commit, push, rewrite history, change branches,
  or mutate the working tree/index.
- Block a few obviously destructive shell commands.

Scope:
- This protects Codex Bash tool calls when this hook is enabled in Codex config.
- It does not prevent normal file edits by Codex edit tools.
- It does not replace OS permissions, containers, read-only mounts, or manual review.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_hook_error(reason: str, *, raw: str = "", error: Exception | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": "pre_tool_use_policy.py",
        "reason": reason,
    }
    if error is not None:
        entry["error"] = str(error)
    if raw:
        entry["stdin_preview"] = raw[:8000]
    try:
        log_path = Path(__file__).resolve().parents[2] / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[codex-helper] failed to log hook error: {exc}", file=sys.stderr)


try:
    raw_payload = sys.stdin.read()
    payload: Any = json.loads(raw_payload) if raw_payload.strip() else {}
    if not raw_payload.strip():
        log_hook_error("stdin-empty")
except json.JSONDecodeError as exc:
    log_hook_error("json-decode-failed", raw=raw_payload, error=exc)
    payload = {}
except Exception as exc:
    log_hook_error("stdin-read-failed", error=exc)
    payload = {}


def find_command(obj: Any) -> str:
    """Find a shell command string in common Codex/agent hook payload shapes."""
    if isinstance(obj, dict):
        tool_input = obj.get("tool_input")
        if isinstance(tool_input, dict):
            for key in ("command", "cmd"):
                value = tool_input.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        for key in ("command", "cmd"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value
        for value in obj.values():
            found = find_command(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_command(value)
            if found:
                return found
    return ""


cmd = find_command(payload)
raw = json.dumps(payload)
search_text = cmd if cmd else raw

blocked = [
    # Git staging, committing, pushing, branch/history/worktree mutations.
    (r"\bgit\s+add\b", "git add is blocked for AI tools; stage files manually."),
    (r"\bgit\s+commit\b", "git commit is blocked for AI tools; commit manually."),
    (r"\bgit\s+push\b", "git push is blocked for AI tools; push manually."),
    (r"\bgit\s+pull\b", "git pull can mutate the working tree; run it manually."),
    (r"\bgit\s+checkout\b", "git checkout can mutate branches/files; run it manually."),
    (r"\bgit\s+switch\b", "git switch is blocked for AI tools; switch branches manually."),
    (r"\bgit\s+reset\b", "git reset is blocked for AI tools."),
    (r"\bgit\s+clean\b", "git clean is blocked for AI tools."),
    (r"\bgit\s+merge\b", "git merge is blocked for AI tools."),
    (r"\bgit\s+rebase\b", "git rebase is blocked for AI tools."),
    (r"\bgit\s+tag\b", "git tag is blocked for AI tools."),
    (r"\bgit\s+stash\b", "git stash is blocked for AI tools."),
    (r"\bgit\s+update-ref\b", "git update-ref is blocked for AI tools."),
    (r"\bgit\s+commit-tree\b", "git commit-tree is blocked for AI tools."),
    (r"\bgit\s+worktree\s+(add|remove|move|prune)\b", "git worktree mutations are blocked for AI tools."),

    # Obvious destructive shell commands.
    (r"\brm\s+-rf\s+(/|~|\$HOME|\.)", "dangerous rm -rf command blocked."),
    (r"\bsudo\s+rm\b", "sudo rm command blocked."),
    (r"\bmkfs\b", "mkfs command blocked."),
    (r"\bdd\s+if=", "dd command blocked."),
    (r"\bchmod\s+-R\s+777\b", "chmod -R 777 blocked."),
    (r"\bchown\s+-R\b.*(/|~|\$HOME)", "broad chown -R command blocked."),
    (r"curl\b.*\|\s*(sh|bash)", "curl pipe-to-shell command blocked."),
    (r"wget\b.*\|\s*(sh|bash)", "wget pipe-to-shell command blocked."),
]

for pattern, reason in blocked:
    if re.search(pattern, search_text):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        sys.exit(0)

print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse"}}))
EOF_PRE_HOOK
	)
	write_file ".codex/hooks/pre_tool_use_policy.py" "$pre_hook" "0755"

	if [[ "$CREATE_RTK_HOOK" == "1" ]]; then
		local rtk_hook
		rtk_hook=$(
			cat <<'EOF_RTK_HOOK'
#!/usr/bin/env python3
"""Codex PreToolUse hook that routes eligible Bash commands through RTK.

The hook is deliberately conservative. It rewrites simple read-only/noisy shell
commands when `rtk` is available, and otherwise lets the original command run.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COMMAND_KEYS = ("command", "cmd")
READ_ONLY_GIT_SUBCOMMANDS = {"diff", "log", "show", "status"}
DIRECT_COMMANDS = {"grep", "ls"}
INTERACTIVE_COMMANDS = {"less", "man", "more", "nano", "ssh", "tail", "top", "vim", "vi", "watch"}
SHELL_META_RE = re.compile(r"[|;&<>\x60$*?\[\]{}()\r\n]")
UNSAFE_GIT_OPTIONS = {"--ext-diff", "--output", "--textconv"}


def log_hook_error(reason: str, *, raw: str = "", error: Exception | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": "rtk_pre_tool_use.py",
        "reason": reason,
    }
    if error is not None:
        entry["error"] = str(error)
    if raw:
        entry["stdin_preview"] = raw[:8000]
    try:
        log_path = Path(__file__).resolve().parents[2] / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[codex-helper] failed to log hook error: {exc}", file=sys.stderr)


def load_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        log_hook_error("stdin-read-failed", error=exc)
        return {}
    if not raw.strip():
        log_hook_error("stdin-empty")
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        log_hook_error("json-decode-failed", raw=raw, error=exc)
        return {}
    return value if isinstance(value, dict) else {}


def find_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        return tool_input
    tool_input = payload.get("toolInput")
    if isinstance(tool_input, dict):
        return tool_input
    return payload


def command_key_and_value(tool_input: dict[str, Any]) -> tuple[str | None, str]:
    for key in COMMAND_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return key, value.strip()
    return None, ""


def has_unsafe_git_option(parts: list[str]) -> bool:
    for part in parts[2:]:
        if part in UNSAFE_GIT_OPTIONS or part.startswith("--output="):
            return True
    return False


def should_wrap(command: str) -> bool:
    if not command or SHELL_META_RE.search(command):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False

    executable = parts[0]
    if executable == "rtk" or executable in INTERACTIVE_COMMANDS:
        return False
    if executable in DIRECT_COMMANDS:
        return True
    if executable == "git" and len(parts) >= 2:
        return parts[1] in READ_ONLY_GIT_SUBCOMMANDS and not has_unsafe_git_option(parts)
    return False


def emit_passthrough() -> int:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse"}}))
    return 0


def main() -> int:
    if shutil.which("rtk") is None:
        return emit_passthrough()

    payload = load_payload()
    tool_input = find_tool_input(payload)
    key, command = command_key_and_value(tool_input)
    if key is None or not should_wrap(command):
        return emit_passthrough()

    wrapped = f"rtk {command}"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Routing eligible Bash command through RTK to reduce Codex token usage.",
            "updatedInput": {"command": wrapped},
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
EOF_RTK_HOOK
		)
		write_file ".codex/hooks/rtk_pre_tool_use.py" "$rtk_hook" "0755"
	fi

	local post_hook
	post_hook=$(
		cat <<'EOF_POST_HOOK'
#!/usr/bin/env python3
"""Codex PostToolUse hook placeholder.
It currently does not block anything; use it for future logging/verification context.
"""

# Successful command hooks may exit 0 without writing a JSON response.
EOF_POST_HOOK
	)
	write_file ".codex/hooks/post_tool_use.py" "$post_hook" "0755"

	local post_edit_hook
	post_edit_hook=$(
		cat <<'EOF_POST_EDIT_HOOK'
#!/usr/bin/env python3
"""Run edited-file checks after Codex edit tools."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PATH_KEYS = {"file", "file_path", "filename", "path", "target_file"}


def log_hook_error(reason: str, *, raw: str = "", error: Exception | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": "post_edit_check.py",
        "reason": reason,
    }
    if error is not None:
        entry["error"] = str(error)
    if raw:
        entry["stdin_preview"] = raw[:8000]
    try:
        log_path = Path(__file__).resolve().parents[2] / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[codex-helper] failed to log hook error: {exc}", file=sys.stderr)


def emit_failure(reason: str) -> None:
    print(json.dumps({"continue": False, "stopReason": reason}))


def collect_paths(value: Any, paths: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PATH_KEYS and isinstance(child, str):
                paths.add(child)
            else:
                collect_paths(child, paths)
    elif isinstance(value, list):
        for child in value:
            collect_paths(child, paths)


def git_root() -> Path:
    completed = subprocess.run(["git", "rev-parse", "--show-toplevel"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip())
    return Path(__file__).resolve().parents[2]


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        log_hook_error("stdin-read-failed", error=exc)
        payload = {}
    else:
        if not raw.strip():
            log_hook_error("stdin-empty")
            payload = {}
        else:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                log_hook_error("json-decode-failed", raw=raw, error=exc)
                payload = {}
            if not isinstance(payload, dict):
                payload = {}

    paths: set[str] = set()
    collect_paths(payload, paths)
    root = git_root()
    checker = root / "vibe_scripts" / "agent-check-edited.py"
    if not checker.is_file():
        print("[post-edit-check] vibe_scripts/agent-check-edited.py not found; skipping", file=sys.stderr)
        return 0

    existing_paths: list[str] = []
    for path in sorted(paths):
        full_path = (root / path).resolve()
        try:
            relative = full_path.relative_to(root.resolve())
        except ValueError:
            continue
        if full_path.is_file():
            existing_paths.append(str(relative))

    if not existing_paths:
        print("[post-edit-check] no edited file paths found in hook payload; skipping per-edit check", file=sys.stderr)
        return 0

    cache_file = root / ".cache" / "codex-edited-files.txt"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    previous = set(cache_file.read_text(encoding="utf-8").splitlines()) if cache_file.is_file() else set()
    cache_file.write_text("\n".join(sorted(previous | set(existing_paths))) + "\n", encoding="utf-8")

    print("[post-edit-check] checking edited files:", file=sys.stderr)
    for path in existing_paths:
        print(f"  - {path}", file=sys.stderr)

    completed = subprocess.run([sys.executable, str(checker), *existing_paths], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if completed.stdout:
        print(completed.stdout, file=sys.stderr, end="")
    if completed.returncode != 0:
        emit_failure("Edited-file quality gate failed; review hook stderr output and fix the reported issues.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[post-edit-check] unexpected error: {exc}", file=sys.stderr)
        emit_failure(f"Post-edit hook failed unexpectedly: {exc}")
        sys.exit(0)
EOF_POST_EDIT_HOOK
	)
	write_file ".codex/hooks/post_edit_check.py" "$post_edit_hook" "0755"

	local stop_hook
	stop_hook=$(
		cat <<'EOF_STOP_HOOK'
#!/usr/bin/env python3
"""Run the edited-file quality gate before Codex stops a turn."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def log_hook_error(reason: str, *, error: Exception | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": "stop_edited_check.py",
        "reason": reason,
    }
    if error is not None:
        entry["error"] = str(error)
    try:
        log_path = Path(__file__).resolve().parents[2] / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[codex-helper] failed to log hook error: {exc}", file=sys.stderr)


def emit_response(exit_code: int, reason: str | None = None) -> None:
    if exit_code != 0:
        print(json.dumps({"continue": False, "stopReason": reason or "Edited-file quality gate failed; review hook stderr output and fix it before stopping."}))


def git_root() -> Path:
    completed = subprocess.run(["git", "rev-parse", "--show-toplevel"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if completed.returncode == 0 and completed.stdout.strip():
        return Path(completed.stdout.strip())
    return Path(__file__).resolve().parents[2]


def main() -> int:
    root = git_root()
    checker = root / "vibe_scripts" / "agent-check-edited.py"
    if not checker.is_file():
        print("[stop-edited-check] vibe_scripts/agent-check-edited.py not found; skipping", file=sys.stderr)
        return 0
    cache_file = root / ".cache" / "codex-edited-files.txt"
    if not cache_file.is_file():
        print("[stop-edited-check] no recorded edited files; skipping", file=sys.stderr)
        return 0
    files = [line.strip() for line in cache_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not files:
        print("[stop-edited-check] recorded edited file list is empty; skipping", file=sys.stderr)
        cache_file.unlink(missing_ok=True)
        return 0
    print("[stop-edited-check] checking recorded edited files before stop", file=sys.stderr)
    completed = subprocess.run([sys.executable, str(checker), *files], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if completed.stdout:
        print(completed.stdout, file=sys.stderr, end="")
    result = completed.returncode
    if result == 0:
        cache_file.unlink(missing_ok=True)
    return result


if __name__ == "__main__":
    exit_code = 1
    reason = None
    try:
        exit_code = main()
    except Exception as exc:
        reason = f"Stop hook failed unexpectedly: {exc}"
        log_hook_error("unexpected-error", error=exc)
        print(f"[stop-edited-check] unexpected error: {exc}", file=sys.stderr)
    finally:
        emit_response(exit_code, reason)
    sys.exit(0)
EOF_STOP_HOOK
	)
	write_file ".codex/hooks/stop_edited_check.py" "$stop_hook" "0755"

	local rtk_config=""
	local rtk_warning=""
	if [[ "$CREATE_RTK_HOOK" == "1" ]]; then
		rtk_warning=$(
			cat <<'EOF_RTK_WARNING'
# WARNING: RTK command rewriting is enabled by default.
# It rewrites eligible literal read-only/noisy Bash commands through 'rtk' when RTK is installed.
# Rerun part1.sh with --no-rtk-hook if you do not want this behavior.
EOF_RTK_WARNING
		)
		rtk_config=$(
			cat <<'EOF_RTK_CONFIG'
[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = 'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; hook="$root/.codex/hooks/rtk_pre_tool_use.py"; [ -f "$hook" ] || exit 0; exec python3 "$hook"'
timeout = 30
statusMessage = "Routing eligible shell output through RTK"

EOF_RTK_CONFIG
		)
	fi

	local config_snippet
	config_snippet=$(
		{
			cat <<'EOF_CODEX_CONFIG_HEAD'
# Codex hook config snippet.
# Review before copying into ~/.codex/config.toml.
EOF_CODEX_CONFIG_HEAD
			[[ -n "$rtk_warning" ]] && printf '%s\n' "$rtk_warning"
			cat <<'EOF_CODEX_CONFIG_HEAD'
# This blocks Codex Bash tool calls that try to stage, commit, push, rewrite history,
# change branches, or run obviously destructive commands.

[features]
hooks = true

EOF_CODEX_CONFIG_HEAD
			[[ -n "$rtk_config" ]] && printf '%s\n\n' "$rtk_config"
			cat <<'EOF_CODEX_CONFIG_TAIL'
[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = 'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; hook="$root/.codex/hooks/pre_tool_use_policy.py"; [ -f "$hook" ] || exit 0; exec python3 "$hook"'
timeout = 30
statusMessage = "Blocking Git-writing/destructive commands"

[[hooks.PostToolUse]]
matcher = "^Bash$"

[[hooks.PostToolUse.hooks]]
type = "command"
command = 'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; hook="$root/.codex/hooks/post_tool_use.py"; [ -f "$hook" ] || exit 0; exec python3 "$hook"'
timeout = 30
statusMessage = "Recording command result"

[[hooks.PostToolUse]]
matcher = "^(apply_patch|Edit|Write)$"

[[hooks.PostToolUse.hooks]]
type = "command"
command = 'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; hook="$root/.codex/hooks/post_edit_check.py"; [ -f "$hook" ] || exit 0; exec python3 "$hook"'
timeout = 120
statusMessage = "Formatting and checking edited file"

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = 'root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0; hook="$root/.codex/hooks/stop_edited_check.py"; [ -f "$hook" ] || exit 0; exec python3 "$hook"'
timeout = 300
statusMessage = "Running edited-file quality gate"
EOF_CODEX_CONFIG_TAIL
		}
	)
	write_file ".codex/config-snippet.toml" "$config_snippet"
	write_file ".codex/config.toml" "$config_snippet"
}

codex_config_candidates() {
	# Print likely Codex config locations, one per line, without duplicates.
	{
		local candidate
		if [[ -n "${CODEX_CONFIG:-}" ]]; then printf '%s\n' "$CODEX_CONFIG"; fi
		printf '%s\n' "$HOME/.codex/config.toml"
		if [[ -n "${XDG_CONFIG_HOME:-}" ]]; then printf '%s\n' "$XDG_CONFIG_HOME/codex/config.toml"; fi
		printf '%s\n' "$HOME/.config/codex/config.toml"
		for candidate in "$HOME/.codex"/*/config.toml "$HOME/.config/codex"/*/config.toml; do
			[[ -f "$candidate" ]] && printf '%s\n' "$candidate"
		done
	} | awk 'NF && !seen[$0]++'
}

choose_codex_config_target() {
	local candidate
	while IFS= read -r candidate; do
		if [[ -f "$candidate" ]]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done < <(codex_config_candidates)
	printf '%s\n' "$HOME/.codex/config.toml"
}

codex_hook_config_implemented() {
	local config_path="$1"
	local global_hook="$HOME/.codex/hooks/ai_git_blocker.py"
	[[ -f "$config_path" ]] || return 1
	[[ -f "$global_hook" ]] || return 1
	grep -Fq "$global_hook" "$config_path" || return 1
	grep -Eq '^[[:space:]]*hooks[[:space:]]*=[[:space:]]*true[[:space:]]*$' "$config_path" || return 1
}

apply_codex_hook_config() {
	if [[ "$CREATE_CODEX_HOOKS" != "1" ]]; then
		log "Skipping Codex config application because hook templates are disabled."
		return 0
	fi

	if [[ ! -f ".codex/hooks/pre_tool_use_policy.py" && "$DRY_RUN" != "1" ]]; then
		warn "Cannot apply Codex config because .codex/hooks/pre_tool_use_policy.py does not exist."
		return 0
	fi

	log "Searching for Codex config file"
	local candidate target global_hook
	while IFS= read -r candidate; do
		if [[ -f "$candidate" ]]; then
			printf '  found:   %s\n' "$candidate"
		else
			printf '  missing: %s\n' "$candidate"
		fi
	done < <(codex_config_candidates)

	target="$(choose_codex_config_target)"
	global_hook="$HOME/.codex/hooks/ai_git_blocker.py"

	if [[ -f "$target" ]]; then
		log "Selected Codex config: $target"
	else
		warn "No Codex config.toml found. Default target will be created if approved: $target"
	fi

	if [[ "$DRY_RUN" != "1" ]] && codex_hook_config_implemented "$target"; then
		log "Codex Git-command blocker already appears to be implemented in $target"
		return 0
	fi

	if [[ "$CODEX_CONFIG_MODE" == "skip" ]]; then
		warn "Codex config application skipped (--no-apply-codex-config)."
		return 0
	fi

	local should_apply=1
	if [[ "$CODEX_CONFIG_MODE" == "apply" ]]; then
		should_apply=0
	elif [[ "$YES" == "1" ]]; then
		warn "--yes was supplied, but global Codex config will not be edited without --apply-codex-config."
		should_apply=1
	elif confirm "Implement Codex Git-command blocker in $target now?"; then
		should_apply=0
	fi

	if [[ "$should_apply" != "0" ]]; then
		warn "Codex config not modified. You can later run this script with --apply-codex-config."
		return 0
	fi

	log "Preparing global Codex hook file: $global_hook"
	if [[ "$DRY_RUN" == "1" ]]; then
		log "Would copy .codex/hooks/pre_tool_use_policy.py -> $global_hook"
		log "Would update $target with hooks = true and a managed PreToolUse block"
		return 0
	fi

	mkdir -p "$(dirname "$global_hook")" "$(dirname "$target")"
	cp ".codex/hooks/pre_tool_use_policy.py" "$global_hook"
	chmod 0755 "$global_hook"

	python3 - "$target" "$global_hook" <<'PY_CODEX_CONFIG'
import datetime
import pathlib
import re
import shutil
import sys

config_path = pathlib.Path(sys.argv[1]).expanduser()
hook_path = pathlib.Path(sys.argv[2]).expanduser()
marker_start = "# >>> ai-quality codex git blocker"
marker_end = "# <<< ai-quality codex git blocker"

config_path.parent.mkdir(parents=True, exist_ok=True)
text = config_path.read_text() if config_path.exists() else ""

if config_path.exists():
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(config_path, config_path.with_suffix(config_path.suffix + f".bak.{stamp}"))


def ensure_features_hooks_enabled(src: str) -> str:
    lines = src.splitlines()
    table_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    features_idx = None
    next_table_idx = len(lines)
    for i, line in enumerate(lines):
        m = table_re.match(line)
        if m and m.group(1).strip() == "features":
            features_idx = i
            for j in range(i + 1, len(lines)):
                if table_re.match(lines[j]):
                    next_table_idx = j
                    break
            break

    if features_idx is None:
        prefix = src.rstrip()
        extra = "\n\n[features]\nhooks = true\n" if prefix else "[features]\nhooks = true\n"
        return prefix + extra

    hook_line_re = re.compile(r"^\s*hooks\s*=")
    for i in range(features_idx + 1, next_table_idx):
        if hook_line_re.match(lines[i]):
            lines[i] = "hooks = true"
            return "\n".join(lines) + "\n"

    lines.insert(features_idx + 1, "hooks = true")
    return "\n".join(lines) + "\n"


text = ensure_features_hooks_enabled(text)

# Remove any previous managed block so reruns are idempotent.
pattern = re.compile(re.escape(marker_start) + r".*?" + re.escape(marker_end) + r"\n?", re.S)
text = pattern.sub("", text).rstrip() + "\n\n"

block = f"""{marker_start}
[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = '/usr/bin/env python3 "{hook_path}"'
timeout = 30
statusMessage = "Blocking Git-writing/destructive commands"
{marker_end}
"""

# If user already has the exact hook path in an unmanaged block, avoid duplicating it.
if str(hook_path) not in text:
    text += block

config_path.write_text(text)
PY_CODEX_CONFIG

	if codex_hook_config_implemented "$target"; then
		log "Verified: Codex Git-command blocker is implemented in $target"
	else
		warn "Could not verify Codex hook implementation in $target. Review it manually."
	fi
}

run_final_checks() {
	log "Final local checks"

	if [[ "$DRY_RUN" == "1" ]]; then
		log "Dry run: skipping final checks."
		return 0
	fi

	bash -n vibe_scripts/agent-verify.sh || warn "vibe_scripts/agent-verify.sh has a syntax error."
	if [[ -f .codex/hooks/pre_tool_use_policy.py ]]; then
		local hook_files=(
			.codex/hooks/pre_tool_use_policy.py
			.codex/hooks/post_tool_use.py
			.codex/hooks/post_edit_check.py
			.codex/hooks/stop_edited_check.py
		)
		[[ -f .codex/hooks/rtk_pre_tool_use.py ]] && hook_files+=(.codex/hooks/rtk_pre_tool_use.py)
		python3 -m py_compile "${hook_files[@]}" || warn "Codex hook template Python syntax failed."
	fi

	if have_cli code-review-graph; then
		run_cli code-review-graph detect-changes --brief || true
	fi

	if [[ "$RUN_SECURITY_SCAN" == "1" ]] && have_cli semgrep && cli_works semgrep; then
		run_semgrep_cli scan || true
	elif [[ "$RUN_SECURITY_SCAN" == "1" ]] && have_cli semgrep; then
		warn "Skipping requested security scan because semgrep exists but is not runnable."
	fi
}

print_install_summary() {
	cat <<EOF_SUMMARY

Install summary
===============
EOF_SUMMARY

	if [[ "${#INSTALL_OK[@]}" -gt 0 ]]; then
		printf 'Succeeded / already present:\n'
		printf '  - %s\n' "${INSTALL_OK[@]}"
	fi

	if [[ "${#INSTALL_SKIPPED[@]}" -gt 0 ]]; then
		printf 'Skipped:\n'
		printf '  - %s\n' "${INSTALL_SKIPPED[@]}"
	fi

	if [[ "${#INSTALL_FAILED[@]}" -gt 0 ]]; then
		printf 'Failed or needs manual PATH fix:\n' >&2
		printf '  - %s\n' "${INSTALL_FAILED[@]}" >&2
		printf '\nThe script continued, but the failed tools will not be available until fixed.\n' >&2
	fi

	if [[ "${#PATH_FIXES[@]}" -gt 0 ]]; then
		printf 'PATH fixes needed in future shells:\n'
		printf '  - %s\n' "${PATH_FIXES[@]}"
	fi

	if [[ -n "$PYTHON_INDEX_URL" ]]; then
		printf '\nUse the Python package mirror when running part2:\n'
		printf '  bash part2.sh --fresh-install --python-index-url=%q\n' "$PYTHON_INDEX_URL"
		printf '\nIf you need the mirror in this already-open terminal for manual Python package commands, run:\n'
		printf '  export UV_DEFAULT_INDEX=%q\n' "$PYTHON_INDEX_URL"
		printf '  export PIP_INDEX_URL=%q\n' "$PYTHON_INDEX_URL"
	fi
}

print_quality_install_step() {
	detect_file_signals

	cat <<EOF_QUALITY_HEADER

Next manual steps
=================

Detected file signals:
EOF_QUALITY_HEADER

	[[ "$has_python" -eq 1 ]] && echo "  - Python: $PY_FILES .py, $PY_NOTEBOOKS .ipynb, $PY_CONFIGS config/manifest files"
	[[ "$has_node" -eq 1 || "$has_angular" -eq 1 || "$JS_FILES" -gt 0 || "$JSX_FILES" -gt 0 || "$TS_FILES" -gt 0 || "$TSX_FILES" -gt 0 ]] && echo "  - Node/JS/TS: $NODE_MANIFESTS package.json, $ANGULAR_MANIFESTS angular.json, $JS_FILES .js, $JSX_FILES .jsx, $TS_FILES .ts, $TSX_FILES .tsx"
	[[ "$HTML_FILES" -gt 0 || "$CSS_FILES" -gt 0 ]] && echo "  - Web markup/styles: $HTML_FILES HTML, $CSS_FILES CSS"
	[[ "$has_php" -eq 1 ]] && echo "  - PHP: $PHP_FILES .php, $PHP_MANIFESTS composer.json"
	[[ "$has_shell" -eq 1 ]] && echo "  - Shell: $SHELL_FILES shell scripts"
	[[ "$has_go" -eq 1 ]] && echo "  - Go: $GO_FILES .go, $GO_MANIFESTS go.mod"
	[[ "$has_rust" -eq 1 ]] && echo "  - Rust: $RUST_FILES .rs, $RUST_MANIFESTS Cargo.toml"
	[[ "$has_dotnet" -eq 1 ]] && echo "  - .NET: $DOTNET_FILES .cs, $DOTNET_MANIFESTS project/solution files"
	if [[ "$has_python" -eq 0 && "$has_node" -eq 0 && "$has_angular" -eq 0 && "$has_static_web" -eq 0 && "$has_php" -eq 0 && "$has_shell" -eq 0 && "$has_go" -eq 0 && "$has_rust" -eq 0 && "$has_dotnet" -eq 0 ]]; then
		echo "  - No project manifests or source-file signals found"
	fi

	if [[ "${CODEXHELPER_UNIFIED_INSTALL:-0}" == "1" ]]; then
		cat <<'EOF_QUALITY_UNIFIED'

1. Quality bootstrap
   The unified installer will run part 2 automatically after part 1 succeeds.

EOF_QUALITY_UNIFIED
		return
	fi

	cat <<EOF_QUALITY_INTRO

1. Run the quality bootstrap
   part2.sh owns quality-tool installation and wiring. It detects this repo's
   files first, then asks before installing linting, formatting, and security
   tools that match those findings.

EOF_QUALITY_INTRO

	if [[ -n "$PYTHON_INDEX_URL" ]]; then
		cat <<'EOF_QUALITY_MIRROR'
   Use the same Python package mirror during quality setup:
EOF_QUALITY_MIRROR
		printf '   bash part2.sh --dry-run --fresh-install --python-index-url=%q\n' "$PYTHON_INDEX_URL"
		printf '   bash part2.sh --fresh-install --python-index-url=%q\n\n' "$PYTHON_INDEX_URL"

	else
		cat <<'EOF_QUALITY_NO_MIRROR'
   Preview first:
   bash part2.sh --dry-run --fresh-install

   If the preview is correct, run:
   bash part2.sh --fresh-install

EOF_QUALITY_NO_MIRROR
	fi

	cat <<'EOF_QUALITY_NOTES'
Notes:
  - Do not install Ruff, ShellCheck, shfmt, Semgrep, Biome, HTMLHint, PHP_CodeSniffer,
    PHPStan, or similar quality tools from part1.
  - part2.sh installs or wires those only after detecting matching files in this repo.
  - Use --no-install with part2.sh if you only want recommendations and wiring for
    tools that are already available.

EOF_QUALITY_NOTES
}

print_next_steps() {
	cat <<'EOF_NEXT'

2. Verify the repo quality gate
   make edited-ai
   make verify-ai
   ./vibe_scripts/agent-verify.sh

3. Populate the repo memory wiki
   make wiki-ai
   Review codebase-wiki/ before relying on generated sections.

4. Check managed skills and plugins
   make skills-check
   Use make skills-update only when you intentionally want policy-approved refreshes.

5. Trust Codex enforcement hooks when enabled
   Unless --no-codex-hooks was used, this bootstrap writes project hooks under .codex/ that make Codex run the
   formatter, linter, and typechecker on edited files:
   - PreToolUse routes eligible shell output through RTK when that hook is enabled and RTK is installed.
   - PostToolUse for apply_patch/Edit/Write checks each edited file.
   - Stop rechecks recorded edited files before Codex finishes a turn.

   Restart Codex in this repo, open /hooks, review/trust the project hooks,
   then start a new thread. Codex will skip changed non-managed hooks until
   you trust them.

6. Finish optional Ponytail setup
EOF_NEXT

	if ! have codex; then
		cat <<'EOF_PONYTAIL_MISSING_CODEX'
   codex CLI was not found, so this script could not check or configure Ponytail.
   Install Codex first, then rerun this bootstrap or add Ponytail manually.

EOF_PONYTAIL_MISSING_CODEX
	elif codex_plugin_installed "ponytail@ponytail"; then
		cat <<'EOF_PONYTAIL_INSTALLED'
   Ponytail is already installed in Codex.
   Open /hooks only if Codex reports hooks that still need review/trust.

EOF_PONYTAIL_INSTALLED
	elif codex_plugin_marketplace_configured "ponytail"; then
		cat <<'EOF_PONYTAIL_MARKETPLACE'
   Ponytail marketplace is already configured.
   Run codex, open /plugins, install Ponytail, open /hooks, review/trust its hooks, and start a new thread.

EOF_PONYTAIL_MARKETPLACE
	else
		cat <<'EOF_PONYTAIL_NOT_CONFIGURED'
   If Ponytail is not already active:
   codex plugin marketplace add DietrichGebert/ponytail
   codex
   Then open /plugins, install Ponytail, open /hooks, review/trust its hooks, and start a new thread.

EOF_PONYTAIL_NOT_CONFIGURED
	fi

	cat <<'EOF_NEXT'
7. Context7
   Run when ready for interactive OAuth/API setup:
   npx ctx7 setup

EOF_NEXT

	if [[ "$CREATE_CODEX_HOOKS" != "1" ]]; then
		cat <<'EOF_RTK_NEXT_NO_HOOKS'
8. RTK automatic shell-output reduction
   RTK command rewriting was skipped because --no-codex-hooks was used.
   Rerun part1.sh with Codex hooks enabled to create the project RTK hook.

EOF_RTK_NEXT_NO_HOOKS
	elif [[ "$CREATE_RTK_HOOK" == "1" ]] && have_cli rtk; then
		cat <<'EOF_RTK_NEXT'
8. RTK automatic shell-output reduction
   RTK is installed and the project RTK PreToolUse hook was created.
   Restart Codex and approve the project hook in /hooks. A separate
   rtk init --codex is not required for this project hook.

   To avoid command rewriting, rerun part1.sh with --no-rtk-hook.

EOF_RTK_NEXT
	elif [[ "$CREATE_RTK_HOOK" == "1" ]]; then
		cat <<'EOF_RTK_NEXT_MISSING'
8. RTK automatic shell-output reduction
   The project RTK PreToolUse hook was created, but rtk is still missing.
   A full macOS setup normally installs it automatically with Homebrew.
   If the install was skipped or failed, run:
   brew install rtk

   Then restart Codex and approve the project hook in /hooks. A separate
   rtk init --codex is not required for this project hook.

EOF_RTK_NEXT_MISSING
	else
		cat <<'EOF_RTK_NEXT_DISABLED'
8. RTK automatic shell-output reduction
   RTK command rewriting was skipped because --no-rtk-hook was used.
   Rerun part1.sh without --no-rtk-hook to create the project RTK PreToolUse hook.

EOF_RTK_NEXT_DISABLED
	fi

	cat <<'EOF_NEXT'
9. Impeccable frontend design skill
   Run only when you want Codex to use Impeccable for frontend design tasks:
   bash part1.sh --impeccable
   /impeccable init

   After install, restart Codex, open /hooks, approve the Impeccable project hook,
   and start a new thread. Frontend design tasks should then use $impeccable or
   /impeccable for design context, polish, critique, audit, and detector checks.

10. Humanizer writing skill
   Humanizer is installed for this project by default. To skip it, use:
   bash part1.sh --no-humanizer

   Restart Codex after installation, then invoke $humanizer or ask Codex to humanize prose.

11. code-review-graph
EOF_NEXT

	if have_cli code-review-graph && ! binary_in_active_venv code-review-graph; then
		cat <<EOF_CRG_INSTALLED
   code-review-graph is already installed at $(resolve_binary code-review-graph).
   Only run these manually if the bootstrap reported Codex integration or graph build failed:
   "$(uv tool dir --bin 2>/dev/null || dirname "$(resolve_binary code-review-graph)")/code-review-graph" install --platform codex
   "$(uv tool dir --bin 2>/dev/null || dirname "$(resolve_binary code-review-graph)")/code-review-graph" build

EOF_CRG_INSTALLED
	else
		cat <<'EOF_CRG_MISSING'
   Only run these manually if the bootstrap reported code-review-graph missing or failed:
   uv tool list | grep -i code-review || uv tool install code-review-graph
   export PATH="$(uv tool dir --bin):$PATH"
   "$(uv tool dir --bin)/code-review-graph" install --platform codex
   "$(uv tool dir --bin)/code-review-graph" build

EOF_CRG_MISSING
	fi

	cat <<'EOF_NEXT'
12. Codex Git-command blocker
   The script searches for ~/.codex/config.toml and can apply the hook when you approve it.
   To apply non-interactively, rerun with --apply-codex-config.
   This blocks Codex Bash calls for git add/commit/push/reset/checkout/etc.

EOF_NEXT
}

main() {
	check_prereqs
	bootstrap_prereqs
	check_prereqs
	install_global_tools
	create_agent_files
	create_session_logging_files
	ensure_cache_gitignored
	create_codebase_wiki_script
	create_skill_sync_script
	create_skills_lock
	create_ai_quality_wrapper_script
	create_edited_check_script
	create_verify_script
	create_codex_hooks_templates
	apply_codex_hook_config
	run_final_checks
	print_install_summary
	print_quality_install_step
	print_next_steps
	if [[ "$FINAL_EXIT_CODE" != "0" ]]; then
		err "Finished with setup failures. See the 'Failed or needs manual PATH fix' section above."
		exit "$FINAL_EXIT_CODE"
	fi
	log "Done."
}

main "$@"
