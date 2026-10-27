#!/usr/bin/env bash
# Wire AI-safe quality targets for a repository already bootstrapped by WSL/part1.sh.
#
# Version: 2026-10-12-v7

set -Eeuo pipefail
INSTALLER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IFS=$'\n\t'

SCRIPT_NAME="$(basename "$0")"
DRY_RUN=0
FORCE=0
REFRESH_MANAGED=0
FALLOW_INSTALL="${CLAUDEHELPER_PACKAGE_INSTALL:-1}"
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
  --wire          Write/update the managed vibe_scripts/claude/Makefile block.
  --refresh-managed Refresh managed files; never install or upgrade packages.
  --force         Replace an existing ClaudeHelper managed vibe_scripts/claude/Makefile block.
  --fresh-install Replace the managed vibe_scripts/claude/Makefile block and enable wiring.
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
		FORCE=1
		WIRE=1
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

if [[ "$REFRESH_MANAGED" == "1" ]]; then
	FORCE=1
	WIRE=1
	log "explicitly skipped: all package changes (--refresh-managed)."
fi

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
		\( -name .agents -o -name .claude -o -name .codex -o -name node_modules -o -name vendor -o -name dist -o -name build -o -name coverage -o -name .graphify -o -name graphify-out -o -name .git -o -name .cache -o -name .venv \) -prune \
		-o \( -name '*.md' -o -name '*.markdown' \) -print -quit 2>/dev/null)"
	[[ -n "$match" ]]
}

selected_files_command() {
	printf 'bash -o pipefail -c %q' "python3 vibe_scripts/claude/agent-check-edited.py --list-files $1 | xargs -0 -r $2"
}

configure_fallow() {
	local source="$INSTALLER_DIR/../../templates/vibe_scripts/fallow.py"
	[[ -f "$source" ]] || source="$INSTALLER_DIR/../templates/vibe_scripts/fallow.py"
	local flags=("install" "--provider" "claude")
	[[ "$DRY_RUN" != "1" ]] || flags+=("--dry-run")
	if [[ "$REFRESH_MANAGED" == "1" ]]; then FALLOW_INSTALL=0; fi
	python3 "$source" "${flags[@]}" --allow-install "$FALLOW_INSTALL"
}

configure_design_hook() {
	[[ "$DRY_RUN" != "1" ]] || return 0
	local source="$INSTALLER_DIR/../../templates/vibe_scripts/quality-hook.py"
	[[ -f "$source" ]] || source="$INSTALLER_DIR/../templates/vibe_scripts/quality-hook.py"
	python3 "$source" --provider claude --install-design-scope
}

install_runtime_assets() {
	local name source destination found
	for name in ai-quality-wrapper.py agent-check-edited.py agent-verify.sh quality-hook.py test-verification.py fallow.py; do
		found=0
		for source in "$INSTALLER_DIR/../../templates/vibe_scripts/$name" "$INSTALLER_DIR/../templates/vibe_scripts/$name"; do
			[[ -f "$source" ]] || continue
			found=1
			destination="vibe_scripts/claude/$name"
			if [[ "$DRY_RUN" == "1" ]]; then
				log "Would install shared runtime: $destination"
			elif [[ ! -f "$destination" || "$FORCE" == "1" ]]; then
				if [[ -L "$destination" || -L "vibe_scripts/claude" || -L vibe_scripts ]]; then
					err "Refusing symlinked runtime destination: $destination"
					return 1
				fi
				mkdir -p vibe_scripts/claude
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
	local result=$?
	trap - EXIT
	if [[ "${#MANAGED_FLAGS[@]}" -gt 0 ]]; then
		python3 "$MANAGED_ASSET" finish --provider claude --run-id "$$" "${MANAGED_FLAGS[@]}" || result=1
	else
		python3 "$MANAGED_ASSET" finish --provider claude --run-id "$$" || result=1
	fi
	exit "$result"
}

start_managed_lifecycle() {
	[[ "${CLAUDEHELPER_UNIFIED_INSTALL:-0}" != "1" ]] || return 0
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
		python3 "$MANAGED_ASSET" begin --provider claude --run-id "$$" "${MANAGED_FLAGS[@]}" || return 1
	else
		python3 "$MANAGED_ASSET" begin --provider claude --run-id "$$" || return 1
	fi
	trap 'finish_managed_lifecycle' EXIT
}

ROOT="$(repo_root)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
log "Working in repo/root: $ROOT"
[[ "$DRY_RUN" -eq 1 ]] && warn "Dry run: no files will be written."

if [[ "$WIRE" -ne 1 && "$DRY_RUN" -ne 1 ]]; then
	warn "No changes requested. Pass --wire to write vibe_scripts/claude/Makefile targets."
	exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
	err "python3 is required to inspect project scripts and run ClaudeHelper targets."
	exit 1
fi

start_managed_lifecycle || exit 1
install_runtime_assets || exit 1
configure_design_hook || exit 1
configure_fallow || exit 1

make_target() {
	local target="$1"
	local body="$2"
	printf '.PHONY: %s\n%s:\n%s\n\n' "$target" "$target" "$body"
}

helper_targets_expected() {
	[[ "${CLAUDEHELPER_UNIFIED_INSTALL:-0}" == "1" ]]
}

targets=()
verify_deps=()
helper_expected=0
helper_targets_expected && helper_expected=1

if [[ -f vibe_scripts/claude/agent-check-edited.py || "$helper_expected" -eq 1 ]]; then
	targets+=("$(make_target edited-ai $'\t@python3 vibe_scripts/claude/agent-check-edited.py')")
fi

if [[ -f vibe_scripts/claude/update-codebase-wiki.py || -d codebase-wiki || "$helper_expected" -eq 1 ]]; then
	targets+=("$(make_target wiki-ai $'\t@python3 vibe_scripts/claude/update-codebase-wiki.py')")
fi

if [[ -f vibe_scripts/claude/sync-skills.py || "$helper_expected" -eq 1 ]]; then
	targets+=("$(make_target skills-check $'\t@python3 vibe_scripts/claude/sync-skills.py --check')")
	targets+=("$(make_target skills-update $'\t@python3 vibe_scripts/claude/sync-skills.py --update')")
fi

if [[ -f vibe_scripts/claude/agent-verify.sh || "$helper_expected" -eq 1 ]]; then
	targets+=("$(make_target agent-verify $'\t@./vibe_scripts/claude/agent-verify.sh')")
fi

targets+=("$(make_target lint-ai $'\t@python3 vibe_scripts/claude/agent-check-edited.py --mode lint')")
targets+=("$(make_target lint-all-ai $'\t@python3 vibe_scripts/claude/agent-check-edited.py --mode lint --all')")
verify_deps+=("lint-all-ai")

if has_npm_script typecheck; then
	targets+=("$(make_target typecheck-ai $'\t@python3 vibe_scripts/claude/ai-quality-wrapper.py --label typecheck-npm --max-lines 40 -- npm run typecheck')")
	verify_deps+=("typecheck-ai")
fi

if has_npm_script test; then
	targets+=("$(make_target test-ai $'\t@python3 vibe_scripts/claude/ai-quality-wrapper.py --label test-npm --max-lines 40 -- npm test')")
	verify_deps+=("test-ai")
fi

if [[ "${#verify_deps[@]}" -gt 0 ]]; then
	body=""
	for dep in "${verify_deps[@]}"; do
		body+=$'\t@$(MAKE) -f vibe_scripts/claude/Makefile '"$dep"$'\n'
	done
	targets+=("$(make_target verify-ai "$body")")
fi

if [[ "${#targets[@]}" -eq 0 ]]; then
	warn "No AI-safe targets could be generated. Run part1.sh first or install project quality tools."
	exit 0
fi

block_start="# >>> ClaudeHelper managed targets"
block_end="# <<< ClaudeHelper managed targets"
block="$block_start"$'\n'"# ClaudeHelper-Version: 2026-10-12-v7"$'\n\n'
for target in "${targets[@]}"; do
	block+="$target"$'\n\n'
done
block+="$block_end"$'\n'

if [[ "$DRY_RUN" -eq 1 ]]; then
	printf '\n%s\n' "$block"
	exit 0
fi

if [[ -f vibe_scripts/claude/Makefile ]] && grep -Eq '^security(-ai)?:|[Ss][Ee][Mm][Gg][Rr][Ee][Pp]' vibe_scripts/claude/Makefile; then
	warn "needs action: preserving existing custom or ambiguous security wiring in vibe_scripts/claude/Makefile; review it manually."
	exit 0
fi

existing_block_count=0
if [[ -f vibe_scripts/claude/Makefile ]]; then
	existing_block_count="$(grep -cF "$block_start" vibe_scripts/claude/Makefile || true)"
fi
if [[ "$existing_block_count" -gt 1 ]]; then
	err "vibe_scripts/claude/Makefile contains multiple ClaudeHelper blocks; resolve them manually before wiring."
	exit 1
fi

if [[ "$existing_block_count" -eq 1 ]]; then
	current_block="$(awk -v start="$block_start" -v end="$block_end" '
		$0 == start { capture = 1 }
		capture { print }
		$0 == end { capture = 0 }
	' vibe_scripts/claude/Makefile)"
	if [[ "$current_block" == "${block%$'\n'}" ]]; then
		log "vibe_scripts/claude/Makefile ClaudeHelper targets are already current."
		exit 0
	fi
fi

if [[ -f vibe_scripts/claude/Makefile && "$FORCE" -ne 1 && "$existing_block_count" -gt 0 ]]; then
	warn "vibe_scripts/claude/Makefile has an older ClaudeHelper block. Use --force to back it up and replace it."
	exit 0
fi

tmp="$(mktemp)"
if [[ -f vibe_scripts/claude/Makefile ]]; then
	awk -v start="$block_start" -v end="$block_end" '
		$0 == start { skip = 1; next }
		$0 == end { skip = 0; next }
		!skip { print }
	' vibe_scripts/claude/Makefile | awk '
		{ lines[NR] = $0 }
		NF { last = NR }
		END { for (line = 1; line <= last; line++) print lines[line] }
	' >"$tmp"
else
	: >"$tmp"
fi
[[ -s "$tmp" ]] && printf '\n' >>"$tmp"
printf '%s' "$block" >>"$tmp"
if [[ -f vibe_scripts/claude/Makefile ]]; then
	cp vibe_scripts/claude/Makefile "vibe_scripts/claude/Makefile.bak.$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p vibe_scripts/claude
mv "$tmp" vibe_scripts/claude/Makefile
log "Updated vibe_scripts/claude/Makefile with ClaudeHelper targets."
