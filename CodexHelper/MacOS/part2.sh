#!/usr/bin/env bash
set -euo pipefail
INSTALLER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# part2.sh
# Quality gate setup for AI coding workflows.
# It detects likely repo types, offers to install missing dev quality tools,
# then wires Makefile targets for tools/scripts that exist.
#
# Version: 2026-10-12-v36

SCRIPT_VERSION="2026-10-12-v36"
DRY_RUN=0
YES=0
FORCE=0
REFRESH_MANAGED=0
FALLOW_INSTALL=1
FINAL_EXIT_CODE=0
WIRE_MODE="ask"    # ask | yes | no
INSTALL_MODE="ask" # ask | yes | no
FIX_MODE="ask"     # ask | yes | no
FRESH_INSTALL=0
REPO_ROOT=""
PYTHON_INDEX_URL="${UV_DEFAULT_INDEX:-${PIP_INDEX_URL:-}}"

log() { printf '[ai-quality] %s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }
err() { printf '[error] %s\n' "$*" >&2; }

usage() {
	cat <<'USAGE'
Usage: part2.sh [options]

Options:
  --dry-run          Show what would happen without writing files.
  --yes              Non-interactive defaults. Continues with available checks.
  --refresh-managed Refresh managed files; never install or upgrade packages.
  --force            Refresh installed helper-managed tools and managed files.
  --fresh-install    Install missing tools, refresh installed tools, and wire Makefile.
  --install          Install missing recommended quality tools without prompting.
  --no-install       Do not install tools; recommendations/wiring only.
  --wire             Write/update Makefile using only currently available checks.
  --no-wire          Do not write Makefile; recommendations only.
  --fix              Run safe automatic format/fix commands after wiring.
  --no-fix           Do not run automatic format/fix commands.
  --python-index-url URL
                     Use a Python package mirror for uv/pipx installs.
  --repo PATH        Run against a specific repo/path.
  -h, --help         Show this help.

Design:
  - Prompts before installing missing dev quality tools unless --yes/--install is used.
  - --fresh-install is equivalent to --install --wire; existing Makefile is backed up.
  - Automatic format/fix commands run only after an interactive prompt or explicit --fix.
  - Refreshes tool detection after installation before wiring checks.
  - Prompts to run safe automatic fixers after wiring unless --no-fix is used.
  - Keeps installs scoped to dev tooling.
  - Creates Makefile targets only for checks that actually exist.
  - Creates AI-capped *-ai targets that write full logs under .cache/ai-quality/.
  - Creates edited-ai to format, lint, and typecheck only edited files.
  - Preserves/adds wiki-ai when vibe_scripts/codex/update-codebase-wiki.py exists.
  - Never creates fake lint/typecheck/test targets.
USAGE
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--yes)
		YES=1
		shift
		;;
	--security-scan)
		err "Unsupported option --security-scan: Semgrep integration has been retired."
		exit 2
		;;
	--refresh-managed)
		REFRESH_MANAGED=1
		shift
		;;
	--force)
		FORCE=1
		shift
		;;
	--fresh-install)
		FRESH_INSTALL=1
		FORCE=1
		INSTALL_MODE="yes"
		WIRE_MODE="yes"
		shift
		;;
	--install)
		INSTALL_MODE="yes"
		shift
		;;
	--no-install)
		INSTALL_MODE="no"
		shift
		;;
	--wire)
		WIRE_MODE="yes"
		shift
		;;
	--no-wire)
		WIRE_MODE="no"
		shift
		;;
	--fix)
		FIX_MODE="yes"
		shift
		;;
	--no-fix)
		FIX_MODE="no"
		shift
		;;
	--python-index-url)
		PYTHON_INDEX_URL="${2:-}"
		if [[ -z "$PYTHON_INDEX_URL" ]]; then
			err "--python-index-url requires a URL"
			exit 2
		fi
		shift 2
		;;
	--python-index-url=*)
		PYTHON_INDEX_URL="${1#*=}"
		shift
		;;
	--repo)
		REPO_ROOT="${2:-}"
		shift 2
		;;
	-h | --help)
		usage
		exit "$FINAL_EXIT_CODE"
		;;
	*)
		err "Unknown option: $1"
		usage
		exit 2
		;;
	esac
done

if [[ "$REFRESH_MANAGED" == "1" ]]; then
	FORCE=1
	FRESH_INSTALL=0
	INSTALL_MODE="no"
	FIX_MODE="no"
	WIRE_MODE="yes"
	log "explicitly skipped: all package changes (--refresh-managed)."
fi

ask_yn() {
	local prompt="$1"
	local default="${2:-n}" # y or n
	if [[ "$YES" -eq 1 ]]; then
		if [[ "$default" == "y" ]]; then return 0; else return 1; fi
	fi
	local suffix="[y/N]"
	[[ "$default" == "y" ]] && suffix="[Y/n]"
	local reply
	printf '%s %s ' "$prompt" "$suffix"
	read -r reply || reply=""
	reply="${reply:-$default}"
	[[ "$reply" =~ ^[Yy]$ ]]
}

find_repo_root() {
	if [[ -n "$REPO_ROOT" ]]; then
		cd "$REPO_ROOT"
	fi
	if command -v git >/dev/null 2>&1 && git rev-parse --show-toplevel >/dev/null 2>&1; then
		git rev-parse --show-toplevel
	else
		pwd
	fi
}

configure_fallow() {
	local source="$INSTALLER_DIR/../../templates/vibe_scripts/fallow.py"
	[[ -f "$source" ]] || source="$INSTALLER_DIR/../templates/vibe_scripts/fallow.py"
	local flags=("install" "--provider" "codex")
	[[ "$DRY_RUN" != "1" ]] || flags+=("--dry-run")
	if [[ "$REFRESH_MANAGED" == "1" || "$INSTALL_MODE" == "no" ]]; then FALLOW_INSTALL=0; fi
	python3 "$source" "${flags[@]}" --allow-install "$FALLOW_INSTALL"
}

configure_design_hook() {
	[[ "$DRY_RUN" != "1" ]] || return 0
	local source="$INSTALLER_DIR/../../templates/vibe_scripts/quality-hook.py"
	[[ -f "$source" ]] || source="$INSTALLER_DIR/../templates/vibe_scripts/quality-hook.py"
	python3 "$source" --provider codex --install-design-scope
}

install_runtime_assets() {
	local name source destination found
	for name in ai-quality-wrapper.py agent-check-edited.py agent-verify.sh quality-hook.py test-verification.py fallow.py; do
		found=0
		for source in "$INSTALLER_DIR/../../templates/vibe_scripts/$name" "$INSTALLER_DIR/../templates/vibe_scripts/$name"; do
			[[ -f "$source" ]] || continue
			found=1
			destination="vibe_scripts/codex/$name"
			if [[ "$DRY_RUN" == "1" ]]; then
				log "Would install shared runtime: $destination"
			elif [[ ! -f "$destination" || "$FORCE" == "1" ]]; then
				if [[ -L "$destination" || -L "vibe_scripts/codex" || -L vibe_scripts ]]; then
					err "Refusing symlinked runtime destination: $destination"
					return 1
				fi
				mkdir -p vibe_scripts/codex
				[[ ! -f "$destination" ]] || cp -p "$destination" "$destination.bak.$(date +%s).$$"
				cp "$source" "$destination"
				chmod 755 "$destination"
			fi
			break
		done
		if [[ "$found" != "1" ]]; then
			err "Required shared runtime asset missing: $name"
			return 1
		fi
	done
}

finish_managed_lifecycle() {
	local result="$1"
	trap - EXIT
	if [[ "${#MANAGED_FLAGS[@]}" -gt 0 ]]; then
		python3 "$MANAGED_ASSET" finish --provider codex --run-id "$$" "${MANAGED_FLAGS[@]}" || result=1
	else
		python3 "$MANAGED_ASSET" finish --provider codex --run-id "$$" || result=1
	fi
	exit "$result"
}

start_managed_lifecycle() {
	[[ "${CODEXHELPER_UNIFIED_INSTALL:-0}" != "1" ]] || return 0
	local asset
	for asset in "$INSTALLER_DIR/../../templates/vibe_scripts/managed-install.py" "$INSTALLER_DIR/../templates/vibe_scripts/managed-install.py"; do
		if [[ -f "$asset" ]]; then
			MANAGED_ASSET="$asset"
			break
		fi
	done
	if [[ -z "${MANAGED_ASSET:-}" ]]; then
		err "Required managed-install.py asset missing."
		return 1
	fi
	MANAGED_FLAGS=("--quality-only")
	[[ "$DRY_RUN" != "1" ]] || MANAGED_FLAGS+=("--dry-run")
	if [[ "${#MANAGED_FLAGS[@]}" -gt 0 ]]; then
		python3 "$MANAGED_ASSET" begin --provider codex --run-id "$$" "${MANAGED_FLAGS[@]}" || return 1
	else
		python3 "$MANAGED_ASSET" begin --provider codex --run-id "$$" || return 1
	fi
	trap 'finish_managed_lifecycle "$?"' EXIT
}

ROOT="$(find_repo_root)"
cd "$ROOT"
log "Working in: $ROOT"
[[ "$DRY_RUN" -eq 1 ]] && warn "Dry run: no files will be written."
[[ "$FRESH_INSTALL" -eq 1 ]] && log "Fresh quality setup enabled: install missing tools, wire Makefile, run safe fixes."
if [[ -n "$PYTHON_INDEX_URL" ]]; then
	export UV_DEFAULT_INDEX="$PYTHON_INDEX_URL"
	export PIP_INDEX_URL="$PYTHON_INDEX_URL"
	log "Using configured Python package index mirror for uv/pipx installs."
fi

cmd_exists() { command -v "$1" >/dev/null 2>&1; }
tool_path() {
	local binary="$1"
	local candidate
	for candidate in "node_modules/.bin/$binary" ".venv/bin/$binary" "vendor/bin/$binary"; do
		if [[ -x "$candidate" ]]; then
			printf '%s\n' "$candidate"
			return 0
		fi
	done
	if command -v "$binary" >/dev/null 2>&1; then
		command -v "$binary"
		return 0
	fi
	return 1
}

tool_exists() {
	local binary="$1"
	tool_path "$binary" >/dev/null 2>&1
}

brew_formula_installed() { cmd_exists brew && HOMEBREW_NO_AUTO_UPDATE=1 brew list --formula --versions "$1" >/dev/null 2>&1; }

uv_tool_installed() {
	cmd_exists uv && uv tool list 2>/dev/null | awk -v package="$1" '$1 == package { found=1 } END { exit !found }'
}

pipx_package_installed() {
	cmd_exists pipx && pipx list --short 2>/dev/null | awk -v package="$1" '$1 == package { found=1 } END { exit !found }'
}

tool_cmd() {
	local binary="$1"
	local path
	path="$(tool_path "$binary" 2>/dev/null || true)"
	case "$path" in
	node_modules/.bin/* | .venv/bin/* | vendor/bin/*) printf '%s\n' "$path" ;;
	*) printf '%s\n' "$binary" ;;
	esac
}

ensure_user_tool_path() {
	local dir="${UV_TOOL_BIN_DIR:-$HOME/.local/bin}"
	case ":$PATH:" in
	*":$dir:"*) ;;
	*) export PATH="$dir:$PATH" ;;
	esac
}

UV_TOOL_BIN_DIR="${UV_TOOL_BIN_DIR:-$HOME/.local/bin}"
if [[ -d "$UV_TOOL_BIN_DIR" ]]; then
	ensure_user_tool_path
fi

has_any() {
	local match
	match="$(find . \
		\( -name '.git' \
		-o -name '.agents' \
		-o -name '.claude' \
		-o -name '.codex' \
		-o -name 'node_modules' \
		-o -name 'vendor' \
		-o -name 'dist' \
		-o -name 'build' \
		-o -name 'coverage' \
		-o -name '.graphify' -o -name 'graphify-out' \
		-o -name '.cache' \
		-o -name '.venv' \
		-o -name 'obsidian' \) -prune \
		-o "$@" -print -quit 2>/dev/null)"
	[[ -n "$match" ]]
}

local_bin_exists() {
	[[ -x "node_modules/.bin/$1" ]]
}

safe_package_name() {
	local base name
	base="${ROOT##*/}"
	name="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')"
	name="${name//%20/-}"
	name="$(printf '%s' "$name" | tr ' _%+' '----' | tr -cd 'a-z0-9._-')"
	name="$(printf '%s' "$name" | sed 's/^[._-]*//; s/[._-]*$//; s/--*/-/g')"
	name="$(printf '%.200s' "$name" | sed 's/[._-]*$//')"
	[[ -n "$name" ]] || name="codex-helper-project"
	case "$name" in
	[0-9]* | .* | _* | node_modules | favicon.ico) name="project-$name" ;;
	esac
	printf '%s\n' "$name"
}

create_minimal_package_json() {
	if [[ -f package.json ]]; then
		return 0
	fi
	local name
	name="$(safe_package_name)"
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would create package.json with safe private package name: $name"
		return 0
	fi
	cat >package.json <<EOF_PACKAGE_JSON
{
  "name": "$name",
  "version": "0.0.0",
  "private": true
}
EOF_PACKAGE_JSON
	log "Created package.json with safe private package name: $name"
}

managed_file_version() {
	local path="$1"
	[[ -f "$path" ]] || return 1
	sed -n '1,5s/.*CodexHelper-Version:[[:space:]]*\([^[:space:]>]*\).*/\1/p' "$path" | head -n 1
}

write_managed_temp() {
	local path="$1"
	local source="$2"
	local mode="${3:-0644}"
	local versioned first existing_version stamp backup suffix
	versioned="$(mktemp)"
	first=""
	IFS= read -r first <"$source" || true

	if [[ "$first" == '#!'* ]]; then
		{
			IFS= read -r first || true
			printf '%s\n# CodexHelper-Version: %s\n' "$first" "$SCRIPT_VERSION"
			cat
		} <"$source" >"$versioned"
	else
		{
			printf '# CodexHelper-Version: %s\n' "$SCRIPT_VERSION"
			cat "$source"
		} >"$versioned"
	fi

	if [[ -e "$path" && "$FORCE" -ne 1 ]]; then
		existing_version="$(managed_file_version "$path" 2>/dev/null || true)"
		if [[ "$existing_version" == "$SCRIPT_VERSION" ]]; then
			if cmp -s "$path" "$versioned"; then
				log "$path is already current at CodexHelper version $SCRIPT_VERSION."
			else
				warn "$path has CodexHelper version $SCRIPT_VERSION but different generated content. Use --force to regenerate it after backup."
			fi
		else
			warn "$path is from CodexHelper version ${existing_version:-unknown}; version $SCRIPT_VERSION is available. Use --force to update it after backup."
		fi
		rm -f "$source" "$versioned"
		return 0
	fi

	if [[ "$DRY_RUN" -eq 1 ]]; then
		if [[ -e "$path" ]]; then
			log "Would back up and update $path to CodexHelper version $SCRIPT_VERSION."
		else
			log "Would write $path at CodexHelper version $SCRIPT_VERSION."
		fi
		rm -f "$source" "$versioned"
		return 0
	fi

	if [[ -e "$path" ]]; then
		stamp="$(date +%Y%m%d-%H%M%S)"
		backup="$path.bak.$stamp"
		suffix=1
		while [[ -e "$backup" ]]; do
			backup="$path.bak.$stamp.$suffix"
			suffix=$((suffix + 1))
		done
		cp -p "$path" "$backup"
		log "Backed up $path -> $backup"
	fi

	mkdir -p "$(dirname "$path")"
	mv "$versioned" "$path"
	rm -f "$source"
	chmod "$mode" "$path"
	log "Wrote $path at CodexHelper version $SCRIPT_VERSION."
}

has_biome_config() {
	[[ -f biome.json || -f biome.jsonc ]]
}

selected_files_command() {
	printf 'bash -o pipefail -c %q' "python3 vibe_scripts/codex/agent-check-edited.py --list-files $1 | xargs -0 -r $2"
}

should_install() {
	local prompt="$1"
	if [[ "$INSTALL_MODE" == "no" ]]; then
		return 1
	fi
	if [[ "$INSTALL_MODE" == "yes" || "$YES" -eq 1 ]]; then
		return 0
	fi
	ask_yn "$prompt" "y"
}

run_install() {
	local label="$1"
	shift
	printf '+'
	local arg
	for arg in "$@"; do printf ' %q' "$arg"; done
	printf '\n'
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would run: $label"
		return 0
	fi
	if "$@"; then log "configured: $label"; else
		FINAL_EXIT_CODE=1
		err "failed: $label"
		return 1
	fi
}

install_missing_quality_tools() {
	if [[ "$INSTALL_MODE" == "no" ]]; then
		log "Tool installation disabled (--no-install)."
		return 0
	fi

	if [[ "$FORCE" -eq 1 ]]; then
		cmd_exists brew && run_install "Homebrew package metadata refresh" brew update || true

		if [[ "$PYTHON" -eq 1 ]] && tool_exists ruff; then
			if uv_tool_installed ruff; then
				run_install "Ruff refresh" uv tool upgrade ruff || warn "Ruff refresh failed."
			elif pipx_package_installed ruff; then
				run_install "Ruff refresh" pipx upgrade ruff || warn "Ruff refresh failed."
			else
				warn "ruff is installed but is not owned by uv or pipx; leaving it unchanged."
			fi
		fi

		local npm_refresh=()
		[[ "$STATIC_WEB" -eq 1 || "$NODE_PKG" -eq 1 || "$TS" -eq 1 ]] && local_bin_exists biome && npm_refresh+=("@biomejs/biome@latest")
		[[ "$STATIC_WEB" -eq 1 || "$NODE_PKG" -eq 1 || "$TS" -eq 1 ]] && local_bin_exists htmlhint && npm_refresh+=("htmlhint@latest")
		[[ "$MARKDOWN" -eq 1 ]] && local_bin_exists markdownlint-cli2 && npm_refresh+=("markdownlint-cli2@latest")
		[[ "$STATIC_WEB" -eq 1 || "$NODE_PKG" -eq 1 || "$TS" -eq 1 ]] && tool_exists biome && ! local_bin_exists biome && warn "biome is not project-local; leaving the unmanaged executable unchanged."
		[[ "$STATIC_WEB" -eq 1 || "$NODE_PKG" -eq 1 || "$TS" -eq 1 ]] && tool_exists htmlhint && ! local_bin_exists htmlhint && warn "htmlhint is not project-local; leaving the unmanaged executable unchanged."
		[[ "$MARKDOWN" -eq 1 ]] && tool_exists markdownlint-cli2 && ! local_bin_exists markdownlint-cli2 && warn "markdownlint-cli2 is not project-local; leaving the unmanaged executable unchanged."
		if [[ ${#npm_refresh[@]} -gt 0 ]]; then
			if cmd_exists npm; then
				run_install "npm quality tool refresh" npm install --save-dev --save-exact "${npm_refresh[@]}" || warn "npm quality tool refresh failed."
			else
				warn "npm-managed quality tools were found, but npm is unavailable."
			fi
		fi

		if [[ "$PHP_LANG" -eq 1 ]] && cmd_exists composer; then
			local composer_refresh=()
			composer show squizlabs/php_codesniffer >/dev/null 2>&1 && composer_refresh+=("squizlabs/php_codesniffer")
			composer show phpstan/phpstan >/dev/null 2>&1 && composer_refresh+=("phpstan/phpstan")
			tool_exists phpcs && ! composer show squizlabs/php_codesniffer >/dev/null 2>&1 && warn "phpcs is not owned by this project's Composer setup; leaving it unchanged."
			tool_exists phpstan && ! composer show phpstan/phpstan >/dev/null 2>&1 && warn "phpstan is not owned by this project's Composer setup; leaving it unchanged."
			[[ ${#composer_refresh[@]} -gt 0 ]] && run_install "Composer quality tool refresh" composer update "${composer_refresh[@]}" --with-dependencies || true
			brew_formula_installed composer && run_install "Composer refresh" brew upgrade composer || true
		fi

		if [[ "$SHELL_LANG" -eq 1 ]]; then
			local brew_refresh=()
			brew_formula_installed shellcheck && brew_refresh+=("shellcheck")
			brew_formula_installed shfmt && brew_refresh+=("shfmt")
			tool_exists shellcheck && ! brew_formula_installed shellcheck && warn "shellcheck is not owned by Homebrew; leaving it unchanged."
			tool_exists shfmt && ! brew_formula_installed shfmt && warn "shfmt is not owned by Homebrew; leaving it unchanged."
			[[ ${#brew_refresh[@]} -gt 0 ]] && run_install "Shell quality tool refresh" brew upgrade "${brew_refresh[@]}" || true
		fi
	fi

	if [[ "$PYTHON" -eq 1 ]] && ! tool_exists ruff; then
		if cmd_exists uv && should_install "Install Ruff with uv?"; then
			run_install "Ruff" uv tool install ruff || warn "Ruff install failed."
			ensure_user_tool_path
		else
			warn "ruff missing; install it with: uv tool install ruff"
		fi
	fi

	local node_packages=()
	if [[ "$STATIC_WEB" -eq 1 || "$NODE_PKG" -eq 1 || "$TS" -eq 1 ]]; then
		tool_exists biome || node_packages+=("@biomejs/biome")
		tool_exists htmlhint || node_packages+=("htmlhint")
	fi
	if [[ "$MARKDOWN" -eq 1 ]]; then
		tool_exists markdownlint-cli2 || node_packages+=("markdownlint-cli2")
	fi

	if [[ ${#node_packages[@]} -gt 0 ]]; then
		if ! cmd_exists npm; then
			warn "npm is missing; cannot install missing npm-based quality tools automatically."
		elif should_install "Install missing npm-based quality tools?"; then
			if [[ ! -f package.json ]]; then
				create_minimal_package_json || warn "package.json creation failed."
			fi
			run_install "Missing npm-based quality tools" npm install --save-dev --save-exact "${node_packages[@]}" || warn "npm quality tool install failed."
		fi
	fi

	if [[ "$PHP_LANG" -eq 1 ]]; then
		if ! cmd_exists composer; then
			if cmd_exists brew && should_install "Composer is missing. Install Composer with Homebrew?"; then
				run_install "Composer" brew install composer || warn "Composer install failed."
			else
				warn "composer is missing; cannot install PHPCS/PHPStan automatically."
			fi
		fi

		local php_packages=()
		tool_exists phpcs || php_packages+=("squizlabs/php_codesniffer")
		tool_exists phpstan || php_packages+=("phpstan/phpstan")
		if cmd_exists composer && [[ ${#php_packages[@]} -gt 0 ]]; then
			if should_install "Install missing PHP quality tools with Composer?"; then
				run_install "Missing PHP quality tools" composer require --dev "${php_packages[@]}" || warn "Composer quality tool install failed."
			fi
		fi
	fi

	if [[ "$SHELL_LANG" -eq 1 ]]; then
		local shell_packages=()
		tool_exists shellcheck || shell_packages+=("shellcheck")
		tool_exists shfmt || shell_packages+=("shfmt")
		if [[ ${#shell_packages[@]} -gt 0 ]]; then
			if cmd_exists brew && should_install "Install missing shell quality tools with Homebrew?"; then
				run_install "Missing shell quality tools" brew install "${shell_packages[@]}" || warn "Shell tool install failed."
			else
				warn "shellcheck/shfmt missing and Homebrew unavailable or install skipped."
			fi
		fi
	fi

}

# Language/project detection
detect_file_signals() {
	PYTHON=0
	NODE_PKG=0
	STATIC_WEB=0
	PHP_LANG=0
	TS=0
	SHELL_LANG=0
	MARKDOWN=0
	GO_LANG=0
	RUST_LANG=0
	DOTNET=0
	UNKNOWN=1
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
		-o -name '.agents' \
		-o -name '.claude' \
		-o -name '.codex' \
		-o -name 'node_modules' \
		-o -name 'vendor' \
		-o -name 'dist' \
		-o -name 'build' \
		-o -name 'coverage' \
		-o -name '.graphify' -o -name 'graphify-out' \
		-o -name '.cache' \
		-o -name '.venv' \
		-o -name '__pycache__' \
		-o -name '.mypy_cache' \
		-o -name '.pytest_cache' \
		-o -name '.ruff_cache' \
		-o -name 'obsidian' \
		-o \( -type d -exec test -f '{}/pyvenv.cfg' \; \) \) -prune \
		-o -type f -print0 2>/dev/null)

	[[ "$PY_FILES" -gt 0 || "$PY_NOTEBOOKS" -gt 0 || "$PY_CONFIGS" -gt 0 ]] && PYTHON=1
	[[ "$NODE_MANIFESTS" -gt 0 ]] && NODE_PKG=1
	[[ "$TS_FILES" -gt 0 || "$TSX_FILES" -gt 0 ]] && TS=1
	[[ "$HTML_FILES" -gt 0 || "$CSS_FILES" -gt 0 || "$JS_FILES" -gt 0 || "$JSX_FILES" -gt 0 || "$TS_FILES" -gt 0 || "$TSX_FILES" -gt 0 ]] && STATIC_WEB=1
	[[ "$PHP_FILES" -gt 0 || "$PHP_MANIFESTS" -gt 0 ]] && PHP_LANG=1
	[[ "$SHELL_FILES" -gt 0 ]] && SHELL_LANG=1
	[[ "$MARKDOWN_FILES" -gt 0 ]] && MARKDOWN=1
	[[ "$GO_FILES" -gt 0 || "$GO_MANIFESTS" -gt 0 ]] && GO_LANG=1
	[[ "$RUST_FILES" -gt 0 || "$RUST_MANIFESTS" -gt 0 ]] && RUST_LANG=1
	[[ "$DOTNET_FILES" -gt 0 || "$DOTNET_MANIFESTS" -gt 0 ]] && DOTNET=1
	if [[ "$PYTHON" -eq 1 || "$NODE_PKG" -eq 1 || "$STATIC_WEB" -eq 1 || "$PHP_LANG" -eq 1 || "$SHELL_LANG" -eq 1 || "$MARKDOWN" -eq 1 || "$GO_LANG" -eq 1 || "$RUST_LANG" -eq 1 || "$DOTNET" -eq 1 ]]; then
		UNKNOWN=0
	fi
	return 0
}

detect_file_signals

print_detection() {
	echo
	log "Detected repo signals:"
	[[ "$PYTHON" -eq 1 ]] && echo "  - Python: $PY_FILES .py, $PY_NOTEBOOKS .ipynb, $PY_CONFIGS config/manifest files"
	[[ "$NODE_PKG" -eq 1 || "$TS" -eq 1 || "$JS_FILES" -gt 0 || "$JSX_FILES" -gt 0 ]] && echo "  - Node/JS/TS: $NODE_MANIFESTS package.json, $ANGULAR_MANIFESTS angular.json, $JS_FILES .js, $JSX_FILES .jsx, $TS_FILES .ts, $TSX_FILES .tsx"
	[[ "$HTML_FILES" -gt 0 || "$CSS_FILES" -gt 0 ]] && echo "  - Web markup/styles: $HTML_FILES HTML, $CSS_FILES CSS"
	[[ "$PHP_LANG" -eq 1 ]] && echo "  - PHP: $PHP_FILES .php, $PHP_MANIFESTS composer.json"
	[[ "$SHELL_LANG" -eq 1 ]] && echo "  - Shell: $SHELL_FILES shell scripts"
	[[ "$MARKDOWN" -eq 1 ]] && echo "  - Markdown: $MARKDOWN_FILES Markdown files"
	[[ "$GO_LANG" -eq 1 ]] && echo "  - Go: $GO_FILES .go, $GO_MANIFESTS go.mod"
	[[ "$RUST_LANG" -eq 1 ]] && echo "  - Rust: $RUST_FILES .rs, $RUST_MANIFESTS Cargo.toml"
	[[ "$DOTNET" -eq 1 ]] && echo "  - .NET: $DOTNET_FILES .cs, $DOTNET_MANIFESTS project/solution files"
	if [[ "$UNKNOWN" -eq 1 ]]; then echo "  - No clear project type detected yet"; fi
	return 0
}

print_recommendations() {
	echo
	log "Recommended quality tools."

	if [[ "$PYTHON" -eq 1 ]]; then
		cat <<'PYREC'

Python:
  Linter/formatter: Ruff
    uv tool install ruff

  If your org uses an internal Python package mirror, rerun with:
    bash part2.sh --fresh-install --python-index-url https://your-python-mirror.example.com/simple

  Tests, only when the project has a real Python environment:
    pytest
    - pyproject.toml project: uv add --dev pytest
    - requirements project: create/use .venv, then uv pip install pytest

  Type checking, optional later:
    mypy or pyright
    - pyproject.toml project: uv add --dev mypy
    - requirements project: create/use .venv, then uv pip install mypy
PYREC
	fi

	if [[ "$NODE_PKG" -eq 1 ]]; then
		cat <<'NODEREC'

Node/JavaScript/TypeScript with package.json:
  Prefer existing package scripts if present:
    npm run lint
    npm run typecheck
    npm test

  If missing, this script can install:
    npm install --save-dev --save-exact @biomejs/biome htmlhint
    # or ESLint/Prettier if the project already uses those
NODEREC
	elif [[ "$STATIC_WEB" -eq 1 ]]; then
		cat <<'WEBREC'

JS/TS/HTML/CSS files without package.json:
  This script can create package.json when you approve npm tool installation,
  so Biome and HTMLHint can be installed as repo-local dev dependencies.

  If you install them yourself:
    create a package.json with a valid lowercase package name
    npm install --save-dev --save-exact @biomejs/biome htmlhint
WEBREC
	fi

	if [[ "$MARKDOWN" -eq 1 ]]; then
		cat <<'MDREC'

Markdown:
  Recommended:
    npm install --save-dev --save-exact markdownlint-cli2
MDREC
	fi

	if [[ "$PHP_LANG" -eq 1 ]]; then
		cat <<'PHPREC'

PHP:
  Recommended:
    composer require --dev squizlabs/php_codesniffer phpstan/phpstan

  Then you can wire:
    php -l path/to/file.php
    vendor/bin/phpcs --standard=PSR12 --extensions=php --ignore=.agents/*,.claude/*,.codex/*,vendor/*,node_modules/*,dist/*,build/*,coverage/*,.graphify/*,graphify-out/*,.git/*,.cache/*,.venv/*,obsidian/* .
    find . \( -name '.agents' -o -name '.claude' -o -name '.codex' -o -name 'vendor' -o -name 'node_modules' -o -name 'dist' -o -name 'build' -o -name 'coverage' -o -name '.graphify' -o -name 'graphify-out' -o -name '.git' -o -name '.cache' -o -name '.venv' -o -name 'obsidian' \) -prune -o -name '*.php' -print0 | xargs -0 vendor/bin/phpstan analyse --memory-limit=1G --no-progress --
PHPREC
	fi

	if [[ "$SHELL_LANG" -eq 1 ]]; then
		cat <<'SHREC'

Shell scripts:
  Recommended:
    brew install shellcheck shfmt
SHREC
	fi

	if [[ "$GO_LANG" -eq 1 ]]; then
		cat <<'GOREC'

Go:
  Recommended:
    gofmt
    go vet ./...
    go test ./...
  Optional stronger linter:
    staticcheck ./...
GOREC
	fi

	if [[ "$RUST_LANG" -eq 1 ]]; then
		cat <<'RUSTREC'

Rust:
  Recommended:
    cargo fmt --check
    cargo clippy -- -D warnings
    cargo test
RUSTREC
	fi

	if [[ "$DOTNET" -eq 1 ]]; then
		cat <<'DOTNETREC'

.NET:
  Recommended:
    dotnet format --verify-no-changes
    dotnet build
    dotnet test
DOTNETREC
	fi

	if [[ "$UNKNOWN" -eq 1 ]]; then
		cat <<'UNKNOWNREC'

No clear project type yet:
  Start building the repo first, or decide which tools fit the files manually.
  The script will not create fake lint/test/typecheck targets.
UNKNOWNREC
	fi

	return 0
}

# Package.json script detection without jq. Prefer npm pkg if available.
npm_has_script() {
	local script="$1"
	[[ -f package.json ]] || return 1
	if cmd_exists npm; then
		local value
		value="$(npm pkg get "scripts.${script}" 2>/dev/null || true)"
		[[ -n "$value" && "$value" != "{}" && "$value" != "null" ]]
	else
		grep -Eq '"'"$script"'"[[:space:]]*:' package.json
	fi
}

# Detect currently available checks. Only these may be wired.
declare -a LINT_TARGETS=()
declare -a FORMAT_TARGETS=()
declare -a TYPE_TARGETS=()
declare -a TEST_TARGETS=()
declare -a SECURITY_TARGETS=()

declare -a LINT_CMDS=()
declare -a FORMAT_CMDS=()
declare -a TYPE_CMDS=()
declare -a TEST_CMDS=()
declare -a SECURITY_CMDS=()
declare -a LINT_AI_CMDS=()
declare -a TYPE_AI_CMDS=()
declare -a TEST_AI_CMDS=()
declare -a SECURITY_AI_CMDS=()
declare -a LINT_ADAPTERS=()
declare -a TYPE_ADAPTERS=()
declare -a TEST_ADAPTERS=()
declare -a SECURITY_ADAPTERS=()

add_check() {
	local kind name cmd adapter ai_cmd
	kind="$1"
	name="$2"
	cmd="$3"
	adapter="${4:-generic}"
	ai_cmd="${5:-$cmd}"
	case "$kind" in
	lint)
		LINT_TARGETS+=("$name")
		LINT_CMDS+=("$cmd")
		LINT_AI_CMDS+=("$ai_cmd")
		LINT_ADAPTERS+=("$adapter")
		;;
	format)
		FORMAT_TARGETS+=("$name")
		FORMAT_CMDS+=("$cmd")
		;;
	type)
		TYPE_TARGETS+=("$name")
		TYPE_CMDS+=("$cmd")
		TYPE_AI_CMDS+=("$ai_cmd")
		TYPE_ADAPTERS+=("$adapter")
		;;
	test)
		TEST_TARGETS+=("$name")
		TEST_CMDS+=("$cmd")
		TEST_AI_CMDS+=("$ai_cmd")
		TEST_ADAPTERS+=("$adapter")
		;;
	security)
		SECURITY_TARGETS+=("$name")
		SECURITY_CMDS+=("$cmd")
		SECURITY_AI_CMDS+=("$ai_cmd")
		SECURITY_ADAPTERS+=("$adapter")
		;;
	esac
}

reset_checks() {
	LINT_TARGETS=()
	FORMAT_TARGETS=()
	TYPE_TARGETS=()
	TEST_TARGETS=()
	SECURITY_TARGETS=()
	LINT_CMDS=()
	FORMAT_CMDS=()
	TYPE_CMDS=()
	TEST_CMDS=()
	SECURITY_CMDS=()
	LINT_AI_CMDS=()
	TYPE_AI_CMDS=()
	TEST_AI_CMDS=()
	SECURITY_AI_CMDS=()
	LINT_ADAPTERS=()
	TYPE_ADAPTERS=()
	TEST_ADAPTERS=()
	SECURITY_ADAPTERS=()
}

detect_available_checks() {
	reset_checks

	# Python checks
	if [[ "$PYTHON" -eq 1 ]]; then
		if tool_exists ruff; then
			add_check lint ruff "$(tool_cmd ruff) check --respect-gitignore ." rtk:ruff-check
			add_check format python "$(tool_cmd ruff) format --respect-gitignore ."
		fi
		if tool_exists mypy; then
			add_check type mypy "$(tool_cmd mypy) ." rtk:mypy
		elif tool_exists pyright; then
			add_check type pyright "$(tool_cmd pyright) ." native:pyright-json "$(tool_cmd pyright) --outputjson ."
		fi
		if tool_exists pytest && has_any \( -path './tests/*' -o -name 'test_*.py' -o -name '*_test.py' \); then
			add_check test pytest "$(tool_cmd pytest) -q" rtk:pytest
		fi
	fi

	# Node/package.json checks
	if [[ "$NODE_PKG" -eq 1 ]]; then
		if npm_has_script lint; then add_check lint node "npm run lint"; fi
		if npm_has_script format; then add_check format node "npm run format"; fi
		if npm_has_script typecheck; then add_check type node "npm run typecheck"; fi
		if npm_has_script test; then add_check test node "npm test"; fi
	fi

	# Static web checks
	if [[ "$STATIC_WEB" -eq 1 ]]; then
		if local_bin_exists biome; then
			add_check lint web "$(selected_files_command '' "npx biome check --files-ignore-unknown=true --max-diagnostics=none")"
			add_check format web "$(selected_files_command '' "npx biome check --write --files-ignore-unknown=true --max-diagnostics=none")"
		elif tool_exists biome; then
			add_check lint web "$(selected_files_command '' "$(tool_cmd biome) check --files-ignore-unknown=true --max-diagnostics=none")"
			add_check format web "$(selected_files_command '' "$(tool_cmd biome) check --write --files-ignore-unknown=true --max-diagnostics=none")"
		fi
		if has_any -name '*.html'; then
			if local_bin_exists htmlhint; then
				add_check lint html "$(selected_files_command '.html .htm' "npx htmlhint")"
			elif tool_exists htmlhint; then
				add_check lint html "$(selected_files_command '.html .htm' "$(tool_cmd htmlhint)")"
			fi
		fi
	fi

	# PHP checks
	if [[ "$PHP_LANG" -eq 1 ]]; then
		if tool_exists php && has_any -name '*.php'; then
			add_check lint php-syntax "$(selected_files_command .php "-n1 $(tool_cmd php) -l")"
		fi
		if tool_exists phpcs; then
			add_check lint phpcs "$(selected_files_command .php "$(tool_cmd phpcs) --standard=PSR12 --extensions=php")" native:phpcs-json "$(selected_files_command .php "$(tool_cmd phpcs) --standard=PSR12 --extensions=php --report=json")"
		fi
		if tool_exists phpcbf; then
			add_check format phpcbf "$(selected_files_command .php "$(tool_cmd phpcbf) --standard=PSR12 --extensions=php")"
		fi
		if tool_exists phpstan; then
			add_check type phpstan "find . \\( -name '.agents' -o -name '.claude' -o -name '.codex' -o -name 'vendor' -o -name 'node_modules' -o -name 'dist' -o -name 'build' -o -name 'coverage' -o -name '.graphify' -o -name 'graphify-out' -o -name '.git' -o -name '.cache' -o -name '.venv' -o -name 'obsidian' \\) -prune -o -name '*.php' -print0 | xargs -0 $(tool_cmd phpstan) analyse --memory-limit=1G --no-progress --" native:phpstan-json "find . \\( -name '.agents' -o -name '.claude' -o -name '.codex' -o -name 'vendor' -o -name 'node_modules' -o -name 'dist' -o -name 'build' -o -name 'coverage' -o -name '.graphify' -o -name 'graphify-out' -o -name '.git' -o -name '.cache' -o -name '.venv' -o -name 'obsidian' \\) -prune -o -name '*.php' -print0 | xargs -0 $(tool_cmd phpstan) analyse --memory-limit=1G --no-progress --error-format=json --"
		fi
	fi

	# Shell checks
	if [[ "$SHELL_LANG" -eq 1 ]]; then
		if tool_exists shellcheck; then add_check lint shell "$(selected_files_command .sh "$(tool_cmd shellcheck)")" native:shellcheck-json1 "$(selected_files_command .sh "$(tool_cmd shellcheck) -f json1")"; fi
		if tool_exists shfmt; then add_check format shell "$(selected_files_command .sh "$(tool_cmd shfmt) -w")"; fi
	fi

	# Markdown checks
	if [[ "$MARKDOWN" -eq 1 ]]; then
		if local_bin_exists markdownlint-cli2; then
			add_check lint markdown "$(selected_files_command '.md .markdown' "npx markdownlint-cli2")"
			add_check format markdown "$(selected_files_command '.md .markdown' "npx markdownlint-cli2 --fix")"
		elif tool_exists markdownlint-cli2; then
			add_check lint markdown "$(selected_files_command '.md .markdown' "$(tool_cmd markdownlint-cli2)")"
			add_check format markdown "$(selected_files_command '.md .markdown' "$(tool_cmd markdownlint-cli2) --fix")"
		fi
	fi

	# Go checks
	if [[ "$GO_LANG" -eq 1 ]] && cmd_exists go; then
		add_check format go "$(selected_files_command .go 'gofmt -w')"
		add_check lint go "go vet ./..."
		add_check test go "go test ./..." rtk:go-test "go test -json ./..."
	fi
	if [[ "$GO_LANG" -eq 1 ]] && cmd_exists staticcheck; then
		add_check lint go-staticcheck "staticcheck ./..."
	fi

	# Rust checks
	if [[ "$RUST_LANG" -eq 1 ]] && cmd_exists cargo; then
		add_check format rust "cargo fmt --check"
		add_check lint rust "cargo clippy -- -D warnings"
		add_check type rust "cargo check"
		add_check test rust "cargo test" rtk:cargo-test
	fi

	# .NET checks
	if [[ "$DOTNET" -eq 1 ]] && cmd_exists dotnet; then
		add_check format dotnet "dotnet format --verify-no-changes"
		add_check type dotnet "dotnet build"
		add_check test dotnet "dotnet test"
	fi

	# Security check
}

print_available_checks() {
	echo
	log "Currently available checks I can wire now:"
	local any=0
	if [[ ${#LINT_CMDS[@]} -gt 0 ]]; then
		any=1
		echo "  Lint:"
		printf '    - %s\n' "${LINT_CMDS[@]}"
	fi
	if [[ ${#FORMAT_CMDS[@]} -gt 0 ]]; then
		any=1
		echo "  Format:"
		printf '    - %s\n' "${FORMAT_CMDS[@]}"
	fi
	if [[ ${#TYPE_CMDS[@]} -gt 0 ]]; then
		any=1
		echo "  Typecheck:"
		printf '    - %s\n' "${TYPE_CMDS[@]}"
	fi
	if [[ ${#TEST_CMDS[@]} -gt 0 ]]; then
		any=1
		echo "  Test:"
		printf '    - %s\n' "${TEST_CMDS[@]}"
	fi
	if [[ ${#SECURITY_CMDS[@]} -gt 0 ]]; then
		any=1
		echo "  Security:"
		printf '    - %s\n' "${SECURITY_CMDS[@]}"
	fi
	if [[ "$any" -eq 0 ]]; then echo "  None. Install a recommended tool manually first."; fi
	return 0
}

ensure_cache_gitignored() {
	local marker="# VibeHelper local AI/dev tooling"
	local block
	block=$'# VibeHelper local AI/dev tooling\n*.bak\n*.bak.*\n.cache/\n.agents/\n.claude/\n.codex/\n.unlazy/\n.mcp.json\nAGENTS.md\nCLAUDE.md\nagent/\nagents/\ncodebase-wiki/\n.graphify/\ngraphify-out/\nnode_modules/\nnotes/\nobsidian/\nvendor/\nvibe_scripts/\nskills-lock.json\nbiome.json\n'
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would ensure .gitignore has VibeHelper local-file entries."
		return 0
	fi
	touch .gitignore
	if ! grep -Fq "$marker" .gitignore; then
		printf '\n%s' "$block" >>.gitignore
		log "Updated .gitignore."
	fi
	if ! grep -Fxq '.unlazy/' .gitignore; then
		printf '\n.unlazy/\n' >>.gitignore
	fi
}

emit_ai_cmd() {
	local label="$1"
	local cmd="$2"
	local adapter="$3"
	printf "\t@python3 vibe_scripts/codex/ai-quality-wrapper.py --label %q --adapter %q --max-lines 30 --shell -- %q\n" "$label" "$adapter" "$cmd"
}

helper_targets_expected() {
	[[ "${CODEXHELPER_UNIFIED_INSTALL:-0}" == "1" ]]
}

helper_targets_available() {
	[[ -f vibe_scripts/codex/agent-check-edited.py ||
		-f vibe_scripts/codex/update-codebase-wiki.py ||
		-f vibe_scripts/codex/sync-skills.py ||
		-f vibe_scripts/codex/agent-verify.sh ]] ||
		helper_targets_expected
}

write_makefile() {
	if [[ -f vibe_scripts/codex/Makefile ]] && grep -Eq '^security(-ai)?:|[Ss][Ee][Mm][Gg][Rr][Ee][Pp]' vibe_scripts/codex/Makefile; then
		warn "needs action: preserving existing custom or ambiguous security wiring in vibe_scripts/codex/Makefile; review it manually."
		return 0
	fi
	local mf="vibe_scripts/codex/Makefile"
	local tmp helper_expected=0
	tmp="$(mktemp)"
	helper_targets_expected && helper_expected=1

	{
		echo "# Generated by part2.sh"
		echo "# Only targets backed by currently available tools/scripts are included."
		echo
		echo ".PHONY: verify"
		# This line is written for make, not expanded by this script.
		# shellcheck disable=SC2016
		echo 'export PATH := $(HOME)/.local/bin:$(PATH)'
		echo

		if [[ ${#LINT_CMDS[@]} -gt 0 ]]; then
			echo ".PHONY: lint"
			echo "lint:"
			local cmd
			for cmd in "${LINT_CMDS[@]}"; do echo "	$cmd"; done
			echo
		fi
		echo ".PHONY: lint-ai lint-all-ai"
		echo "lint-ai:"
		printf '\t@python3 vibe_scripts/codex/agent-check-edited.py --mode lint\n'
		echo "lint-all-ai:"
		printf '\t@python3 vibe_scripts/codex/agent-check-edited.py --mode lint --all\n'
		local idx
		echo

		if [[ ${#FORMAT_CMDS[@]} -gt 0 ]]; then
			echo ".PHONY: format fix"
			echo "format:"
			local cmd
			for cmd in "${FORMAT_CMDS[@]}"; do echo "	$cmd"; done
			echo
			echo "fix: format"
			echo
		fi

		if [[ ${#TYPE_CMDS[@]} -gt 0 ]]; then
			echo ".PHONY: typecheck"
			echo "typecheck:"
			local cmd
			for cmd in "${TYPE_CMDS[@]}"; do echo "	$cmd"; done
			echo
			echo ".PHONY: typecheck-ai"
			echo "typecheck-ai:"
			local idx
			for idx in "${!TYPE_AI_CMDS[@]}"; do emit_ai_cmd "type-${TYPE_TARGETS[$idx]}" "${TYPE_AI_CMDS[$idx]}" "${TYPE_ADAPTERS[$idx]}"; done
			echo
		fi

		if [[ ${#TEST_CMDS[@]} -gt 0 ]]; then
			echo ".PHONY: test"
			echo "test:"
			local cmd
			for cmd in "${TEST_CMDS[@]}"; do echo "	$cmd"; done
			echo
			echo ".PHONY: test-ai"
			echo "test-ai:"
			local idx
			for idx in "${!TEST_AI_CMDS[@]}"; do emit_ai_cmd "test-${TEST_TARGETS[$idx]}" "${TEST_AI_CMDS[$idx]}" "${TEST_ADAPTERS[$idx]}"; done
			echo
		fi

		if [[ ${#SECURITY_CMDS[@]} -gt 0 ]]; then
			echo ".PHONY: security"
			echo "security:"
			local cmd
			for cmd in "${SECURITY_CMDS[@]}"; do echo "	$cmd"; done
			echo
			echo ".PHONY: security-ai"
			echo "security-ai:"
			local idx
			for idx in "${!SECURITY_AI_CMDS[@]}"; do emit_ai_cmd "security-${SECURITY_TARGETS[$idx]}" "${SECURITY_AI_CMDS[$idx]}" "${SECURITY_ADAPTERS[$idx]}"; done
			echo
		fi

		echo "verify:"
		local deps=0
		if [[ ${#LINT_CMDS[@]} -gt 0 ]]; then
			echo "	\$(MAKE) -f vibe_scripts/codex/Makefile lint"
			deps=1
		fi
		if [[ ${#TYPE_CMDS[@]} -gt 0 ]]; then
			echo "	\$(MAKE) -f vibe_scripts/codex/Makefile typecheck"
			deps=1
		fi
		if [[ ${#TEST_CMDS[@]} -gt 0 ]]; then
			echo "	\$(MAKE) -f vibe_scripts/codex/Makefile test"
			deps=1
		fi
		if [[ ${#SECURITY_CMDS[@]} -gt 0 ]]; then
			echo "	\$(MAKE) -f vibe_scripts/codex/Makefile security"
			deps=1
		fi
		if [[ "$deps" -eq 0 ]]; then
			echo "	@echo 'No quality checks configured yet. Install a recommended linter/tool manually and rerun this script.'"
			echo "	@exit 1"
		fi
		echo
		echo ".PHONY: verify-ai"
		echo "verify-ai:"
		local ai_deps=0
		if [[ ${#LINT_CMDS[@]} -gt 0 ]]; then
			echo "	@\$(MAKE) -f vibe_scripts/codex/Makefile lint-all-ai"
			ai_deps=1
		fi
		if [[ ${#TYPE_CMDS[@]} -gt 0 ]]; then
			echo "	@\$(MAKE) -f vibe_scripts/codex/Makefile typecheck-ai"
			ai_deps=1
		fi
		if [[ ${#TEST_CMDS[@]} -gt 0 ]]; then
			echo "	@\$(MAKE) -f vibe_scripts/codex/Makefile test-ai"
			ai_deps=1
		fi
		if [[ ${#SECURITY_CMDS[@]} -gt 0 ]]; then
			echo "	@\$(MAKE) -f vibe_scripts/codex/Makefile security-ai"
			ai_deps=1
		fi
		if [[ "$ai_deps" -eq 0 ]]; then
			echo "	@echo 'No AI-safe quality checks configured yet. Install a recommended linter/tool manually and rerun this script.'"
			echo "	@exit 1"
		fi
		echo
		if [[ -f vibe_scripts/codex/agent-check-edited.py || -f vibe_scripts/codex/ai-quality-wrapper.py || "$helper_expected" -eq 1 ]]; then
			echo ".PHONY: edited-ai"
			echo "edited-ai:"
			echo "	@python3 vibe_scripts/codex/agent-check-edited.py"
		fi
		if [[ -f .codex/hooks/post_tool_use.py ]]; then
			echo
			echo ".PHONY: savings-ai"
			echo "savings-ai:"
			echo "	@python3 .codex/hooks/post_tool_use.py --report"
		fi
		if [[ -f vibe_scripts/codex/update-codebase-wiki.py || -d codebase-wiki || "$helper_expected" -eq 1 ]]; then
			echo
			echo ".PHONY: wiki-ai"
			echo "wiki-ai:"
			echo "	@python3 vibe_scripts/codex/update-codebase-wiki.py"
		fi
		if [[ -f vibe_scripts/codex/sync-skills.py || "$helper_expected" -eq 1 ]]; then
			echo
			echo ".PHONY: skills-check skills-update"
			echo "skills-check:"
			echo "	@python3 vibe_scripts/codex/sync-skills.py --check"
			echo
			echo "skills-update:"
			echo "	@python3 vibe_scripts/codex/sync-skills.py --update"
		fi
		if [[ -f vibe_scripts/codex/agent-verify.sh || "$helper_expected" -eq 1 ]]; then
			echo
			echo ".PHONY: agent-verify"
			echo "agent-verify:"
			echo "	@./vibe_scripts/codex/agent-verify.sh"
		fi
	} >"$tmp"

	if [[ "$DRY_RUN" -eq 1 ]]; then
		printf '\n'
		cat "$tmp"
	fi
	write_managed_temp "$mf" "$tmp"
}

write_biome_config() {
	if [[ "$STATIC_WEB" -ne 1 ]]; then
		return 0
	fi
	if ! local_bin_exists biome && ! cmd_exists biome; then
		return 0
	fi
	if has_biome_config; then
		log "Biome config already exists; leaving it unchanged."
		return 0
	fi

	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would create biome.json with common generated/vendor directories excluded."
		return 0
	fi

	cat >biome.json <<'BIOMEJSON'
{
  "root": true,
  "formatter": {
    "indentStyle": "space",
    "indentWidth": 2
  },
  "files": {
    "includes": [
      "**/*.js",
      "**/*.jsx",
      "**/*.mjs",
      "**/*.cjs",
      "**/*.ts",
      "**/*.tsx",
      "**/*.css",
      "biome.json",
      "package.json",
      "!!**/.git",
      "!!**/.agents",
      "!!**/.claude",
      "!!**/.codex",
      "!!**/node_modules",
      "!!**/vendor",
      "!!**/dist",
      "!!**/build",
      "!!**/coverage",
      "!!**/.graphify",
      "!!**/graphify-out",
      "!!**/.cache",
      "!!**/.venv",
      "!!**/obsidian",
      "!!**/composer.lock",
      "!!**/package-lock.json"
    ]
  }
}
BIOMEJSON
	log "Wrote biome.json."
}

warn_fixers_scope() {
	warn "Automatic fixers may rewrite files across the entire codebase."
	warn "Commands that will run:"
	local cmd
	for cmd in "${FORMAT_CMDS[@]}"; do
		warn "  $cmd"
	done
}

should_run_fixers() {
	if [[ ${#FORMAT_CMDS[@]} -eq 0 ]]; then
		return 1
	fi
	case "$FIX_MODE" in
	yes)
		warn_fixers_scope
		return 0
		;;
	no) return 1 ;;
	esac
	if [[ "$YES" -eq 1 ]]; then
		return 1
	fi
	warn_fixers_scope
	ask_yn "Run these automatic fixers now?" "n"
}

fixer_verify_cmd() {
	local cmd="$1"
	case "$cmd" in
	*biome\\\ check\\\ --write\\\ *)
		printf '%s\n' "${cmd/biome\\ check\\ --write/biome\\ check}"
		;;
	*markdownlint-cli2\\\ --fix*)
		printf '%s\n' "${cmd/\\ --fix/}"
		;;
	*npx\ biome\ check\ --write\ *)
		printf '%s\n' "${cmd/biome check --write/biome check}"
		;;
	*biome\ check\ --write\ *)
		printf '%s\n' "${cmd/biome check --write/biome check}"
		;;
	*markdownlint-cli2\ --fix\ *)
		printf '%s\n' "${cmd/ --fix/}"
		;;
	ruff\ format\ *)
		printf '%s\n' "${cmd/ruff format/ruff format --check}"
		;;
	*)
		return 1
		;;
	esac
}

run_fixers_now() {
	if [[ ${#FORMAT_CMDS[@]} -eq 0 ]]; then
		log "No automatic fixers are available for this repo."
		return 0
	fi

	if ! should_run_fixers; then
		log "Automatic fixers skipped."
		return 0
	fi

	echo
	log "Running safe automatic fixers."
	local cmd verify_cmd failed=0
	for cmd in "${FORMAT_CMDS[@]}"; do
		printf '+ %s\n' "$cmd"
		if [[ "$DRY_RUN" -eq 1 ]]; then
			continue
		fi
		if ! bash -lc "$cmd"; then
			verify_cmd="$(fixer_verify_cmd "$cmd" 2>/dev/null || true)"
			if [[ -n "$verify_cmd" ]]; then
				printf '+ %s\n' "$verify_cmd"
				if bash -lc "$verify_cmd"; then
					log "Fixer completed after applying changes: $cmd"
					continue
				fi
			fi
			failed=1
			warn "Fixer still reports issues after applying changes: $cmd"
		fi
	done

	if [[ "$failed" -eq 1 ]]; then
		warn "Automatic fixers ran, but at least one tool still reported issues that need manual review."
	else
		log "Automatic fixers completed."
	fi
}

print_detection
print_recommendations
install_missing_quality_tools
detect_available_checks
print_available_checks

missing_core_linter=0
if [[ ${#LINT_CMDS[@]} -eq 0 ]]; then
	missing_core_linter=1
fi

if [[ "$missing_core_linter" -eq 1 ]]; then
	echo
	warn "No real linter is currently available to wire."
	warn "Makefile wiring will include only the real checks and helper targets available now."
fi

if [[ "$WIRE_MODE" == "no" ]]; then
	log "Not writing Makefile because --no-wire was used."
	exit "$FINAL_EXIT_CODE"
fi

start_managed_lifecycle || exit 1
install_runtime_assets || exit 1
configure_design_hook || exit 1
configure_fallow || exit 1

TOTAL_CHECKS=$((${#LINT_CMDS[@]} + ${#TYPE_CMDS[@]} + ${#TEST_CMDS[@]} + ${#SECURITY_CMDS[@]}))
if [[ "$TOTAL_CHECKS" -eq 0 ]]; then
	if ! helper_targets_available; then
		warn "No runnable quality checks or helper targets are available. Not writing a Makefile."
		warn "Run part1.sh first or install a recommended tool manually, then rerun this script."
		exit "$FINAL_EXIT_CODE"
	fi
	warn "No runnable quality checks are available; writing helper operational targets only."
fi

if [[ "$WIRE_MODE" == "ask" ]]; then
	if ! ask_yn "Write/update Makefile using only currently available checks?" "y"; then
		log "Not writing Makefile."
		exit "$FINAL_EXIT_CODE"
	fi
fi

ensure_cache_gitignored
write_biome_config
write_makefile
run_fixers_now

if [[ "$DRY_RUN" -eq 0 ]]; then
	echo
	log "Final checks:"
	log "  make -f vibe_scripts/codex/Makefile edited-ai   # format/lint/typecheck edited files with capped AI output"
	log "  make -f vibe_scripts/codex/Makefile verify-ai   # broader AI-safe quality/security checks"
	if [[ -f .codex/hooks/post_tool_use.py ]]; then
		log "  make -f vibe_scripts/codex/Makefile savings-ai  # show the latest Codex session's context-reduction estimates"
	fi
	if [[ -f vibe_scripts/codex/update-codebase-wiki.py || -d codebase-wiki ]]; then
		log "  make -f vibe_scripts/codex/Makefile wiki-ai     # refresh generated codebase-wiki sections"
	fi
	if [[ -f vibe_scripts/codex/sync-skills.py ]]; then
		log "  make -f vibe_scripts/codex/Makefile skills-check  # audit managed skills/plugins"
		log "  make -f vibe_scripts/codex/Makefile skills-update # refresh policy-approved managed skills/plugins"
	fi
fi

if [[ "${CODEXHELPER_UNIFIED_INSTALL:-0}" != "1" ]]; then
	finish_managed_lifecycle "$FINAL_EXIT_CODE"
fi
exit "$FINAL_EXIT_CODE"
