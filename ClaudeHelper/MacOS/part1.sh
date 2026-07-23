#!/usr/bin/env bash
# Bootstrap a small Claude Code workflow into the current repository.
#
# Version: 2026-07-21-v2

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_NAME="$(basename "$0")"
SCRIPT_VERSION="2026-07-21-v2"
DRY_RUN=0
FORCE=0
RUN_HUMANIZER=1
RUN_RTK=1
RUN_GRAPHIFY=1
RUN_PONYTAIL=1
RUN_CRG=1
RUN_CRG_BUILD=0
RUN_CONTEXT7=1
RUN_IMPECCABLE=0
WITH_LLM_COUNCIL=0
SKIP_GLOBAL=0
REPO_ROOT=""

GRAPHIFY_MARKER_START="<!-- >>> ClaudeHelper graphify guidance -->"
GRAPHIFY_MARKER_END="<!-- <<< ClaudeHelper graphify guidance -->"

log() { printf '\033[1;34m[claude-bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

usage() {
	cat <<USAGE
Usage: $SCRIPT_NAME [options]

Options:
  --dry-run       Print what would happen, but do not write files.
  --force         Overwrite managed files after backing them up.
  --no-humanizer  Do not install Humanizer writing skill for Claude Code.
  --no-rtk        Do not install RTK or activate its command-routing hook.
  --no-graphify   Do not install the graphify skill or its proactive guidance.
  --no-ponytail   Do not install the Ponytail plugin.
  --no-crg        Do not install/register code-review-graph.
  --crg-build     Build the code-review-graph index for this repo after install.
  --no-context7   Do not run Context7 setup (npx ctx7 setup).
  --impeccable    Install the Impeccable design skill/hooks for Claude Code.
  --with-llm-council
                  Clone karpathy/llm-council into ~/.local/share/llm-council.
  --repo-only     Only write repo files; skip all global tool installs.
  --skip-global   Alias for --repo-only.
  --yes           Accepted for compatibility (installs are already non-interactive).
  --repo PATH     Configure a specific repository/path.
  -h, --help      Show this help.

Recommended first run:
  bash $SCRIPT_NAME --dry-run
  bash $SCRIPT_NAME
USAGE
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
	--no-humanizer)
		RUN_HUMANIZER=0
		shift
		;;
	--no-rtk)
		RUN_RTK=0
		shift
		;;
	--no-graphify)
		RUN_GRAPHIFY=0
		shift
		;;
	--no-ponytail)
		RUN_PONYTAIL=0
		shift
		;;
	--no-crg)
		RUN_CRG=0
		shift
		;;
	--crg-build)
		RUN_CRG_BUILD=1
		shift
		;;
	--no-context7)
		RUN_CONTEXT7=0
		shift
		;;
	--impeccable)
		RUN_IMPECCABLE=1
		shift
		;;
	--with-llm-council)
		WITH_LLM_COUNCIL=1
		shift
		;;
	--repo-only | --skip-global)
		SKIP_GLOBAL=1
		shift
		;;
	--yes | -y)
		# Accepted for compatibility; installs are already non-interactive.
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

script_dir() {
	local source="${BASH_SOURCE[0]}"
	while [[ -L "$source" ]]; do
		local dir
		dir="$(cd -P "$(dirname "$source")" >/dev/null 2>&1 && pwd)"
		source="$(readlink "$source")"
		[[ "$source" == /* ]] || source="$dir/$source"
	done
	cd -P "$(dirname "$source")" >/dev/null 2>&1 && pwd
}

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

HELPER_ROOT="$(cd "$(script_dir)/.." >/dev/null 2>&1 && pwd)"
TEMPLATE_ROOT="$HELPER_ROOT/templates"
ROOT="$(repo_root)"
cd "$ROOT"

if [[ ! -d "$TEMPLATE_ROOT" ]]; then
	err "Template directory not found: $TEMPLATE_ROOT"
	exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
	err "python3 is required to validate and run ClaudeHelper hooks."
	exit 1
fi

validate_templates() {
	local pycache
	pycache="$(mktemp -d "${TMPDIR:-/tmp}/claudehelper-pycache.XXXXXX")"
	if ! python3 -m json.tool "$TEMPLATE_ROOT/.claude/settings.local.json" >/dev/null; then
		rm -rf "$pycache"
		err "Invalid Claude Code settings template."
		exit 1
	fi
	if ! PYTHONPYCACHEPREFIX="$pycache" python3 -m py_compile \
		"$TEMPLATE_ROOT"/.claude/hooks/*.py "$TEMPLATE_ROOT"/vibe_scripts/*.py; then
		rm -rf "$pycache"
		err "A ClaudeHelper Python template failed to compile."
		exit 1
	fi
	rm -rf "$pycache"
	if ! bash -n "$TEMPLATE_ROOT/vibe_scripts/agent-verify.sh"; then
		err "The agent verification script failed Bash syntax validation."
		exit 1
	fi
}

validate_templates

log "Working in repo/root: $ROOT"
[[ "$DRY_RUN" -eq 1 ]] && warn "Dry run: no files will be written."

backup_file() {
	local path="$1"
	if [[ -e "$path" ]]; then
		local backup
		backup="$path.bak.$(date +%Y%m%d-%H%M%S)"
		if [[ "$DRY_RUN" -eq 1 ]]; then
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
	sed -n '1,5s/.*ClaudeHelper-Version:[[:space:]]*\([^[:space:]>]*\).*/\1/p' "$path" | head -n 1
}

marker_for_path() {
	local path="$1"
	case "$path" in
	*.md) printf '<!-- ClaudeHelper-Version: %s -->' "$SCRIPT_VERSION" ;;
	*.json) printf '' ;;
	*) printf '# ClaudeHelper-Version: %s' "$SCRIPT_VERSION" ;;
	esac
}

copy_with_marker() {
	local src="$1"
	local dest="$2"
	local marker first_line
	marker="$(marker_for_path "$dest")"
	mkdir -p "$(dirname "$dest")"
	if [[ -z "$marker" ]]; then
		cp "$src" "$dest"
		return 0
	fi

	IFS= read -r first_line <"$src" || first_line=""
	if [[ "$first_line" == '#!'* ]]; then
		{
			printf '%s\n' "$first_line"
			printf '%s\n' "$marker"
			tail -n +2 "$src"
		} >"$dest"
	else
		{
			printf '%s\n' "$marker"
			cat "$src"
		} >"$dest"
	fi
}

install_template() {
	local src="$1"
	local rel="${src#"$TEMPLATE_ROOT"/}"
	local dest="$ROOT/$rel"
	local existing_version

	if [[ -e "$dest" && "$FORCE" -ne 1 ]]; then
		existing_version="$(managed_file_version "$dest" 2>/dev/null || true)"
		if [[ "$existing_version" == "$SCRIPT_VERSION" ]] ||
			{ [[ "$rel" == *.json ]] && cmp -s "$src" "$dest"; }; then
			log "$rel already current; leaving it unchanged."
		else
			warn "$rel already exists. Use --force to back it up and replace it."
		fi
		return 0
	fi

	if [[ "$DRY_RUN" -eq 1 ]]; then
		[[ -e "$dest" ]] && log "Would back up $rel"
		log "Would write $rel"
		return 0
	fi

	[[ -e "$dest" ]] && backup_file "$dest"
	copy_with_marker "$src" "$dest"
	if [[ -x "$src" ]]; then
		chmod 755 "$dest"
	else
		chmod 644 "$dest"
	fi
	log "Wrote $rel"
}

append_gitignore_block() {
	local path="$ROOT/.gitignore"
	local marker="# ClaudeHelper"
	local block
	local existed=0
	block=$'# ClaudeHelper\n.cache/\n.claude/settings.local.json\n'
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would ensure .gitignore has ClaudeHelper local-file entries"
		return 0
	fi
	[[ -e "$path" ]] && existed=1
	touch "$path"
	if grep -Fq "$marker" "$path"; then
		log ".gitignore already contains ClaudeHelper block"
		return 0
	fi
	[[ "$existed" -eq 1 ]] && backup_file "$path"
	printf '\n%s' "$block" >>"$path"
	log "Updated .gitignore"
}

humanizer_installed() {
	[[ -f "$ROOT/.claude/skills/humanizer/SKILL.md" ]] ||
		[[ -f "$HOME/.claude/skills/humanizer/SKILL.md" ]]
}

install_humanizer() {
	[[ "$RUN_HUMANIZER" -eq 1 ]] || return 0

	if humanizer_installed; then
		log "Humanizer is already installed for Claude Code."
		return 0
	fi
	if ! command -v npx >/dev/null 2>&1; then
		err "Cannot install Humanizer because npx was not found."
		exit 1
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would run: npx --yes skills add blader/humanizer --agent claude-code --yes"
		return 0
	fi

	log "Installing Humanizer under .claude/skills/."
	if ! npx --yes skills add blader/humanizer --agent claude-code --yes; then
		err "Humanizer installation failed."
		exit 1
	fi
}

maybe_install_rtk() {
	[[ "$RUN_RTK" -eq 1 ]] || return 0
	if command -v rtk >/dev/null 2>&1; then
		log "RTK already installed: $(command -v rtk)."
		return 0
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		if command -v brew >/dev/null 2>&1; then
			log "Would install RTK via: brew install rtk"
		else
			log "Would install RTK via the official install script."
		fi
		return 0
	fi
	# RTK install failures are non-fatal: the routing hook passes commands through
	# unchanged until RTK is on PATH.
	if command -v brew >/dev/null 2>&1; then
		log "Installing RTK via Homebrew."
		if brew install rtk; then
			log "RTK installed."
			return 0
		fi
		warn "RTK install via Homebrew failed. The routing hook will no-op until RTK is installed."
		return 0
	fi
	if command -v curl >/dev/null 2>&1; then
		log "Installing RTK via the official install script."
		if curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/master/install.sh | sh; then
			log "RTK install script completed."
			return 0
		fi
		warn "RTK install script failed. The routing hook will no-op until RTK is installed."
		return 0
	fi
	warn "Neither brew nor curl is available to install RTK; the routing hook no-ops until you install it."
	return 0
}

install_graphify_skill() {
	[[ "$RUN_GRAPHIFY" -eq 1 ]] || return 0
	if [[ -f "$HOME/.claude/skills/graphify/SKILL.md" ]]; then
		log "graphify skill already installed."
		return 0
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would install graphify: 'uv tool install graphifyy' (or pipx/pip), then 'graphify install'."
		return 0
	fi

	# graphifyy ships the `graphify` CLI; `graphify install` registers the skill for
	# Claude Code (~/.claude/skills/graphify). See github.com/Graphify-Labs/graphify.
	if ! command -v graphify >/dev/null 2>&1; then
		if command -v uv >/dev/null 2>&1; then
			uv tool install graphifyy || true
		elif command -v pipx >/dev/null 2>&1; then
			pipx install graphifyy || true
		else
			python3 -m pip install --user graphifyy || true
		fi
	fi
	if command -v graphify >/dev/null 2>&1; then
		graphify install || warn "'graphify install' failed; run it manually."
		log "graphify installed."
	else
		warn "Could not install graphifyy. Install manually: 'uv tool install graphifyy && graphify install'."
	fi
}

maybe_install_ponytail() {
	[[ "$RUN_PONYTAIL" -eq 1 ]] || return 0
	if ! command -v claude >/dev/null 2>&1; then
		warn "claude CLI not found; skipping Ponytail. Install later: 'claude plugin marketplace add DietrichGebert/ponytail && claude plugin install ponytail@ponytail'."
		return 0
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would install Ponytail: 'claude plugin marketplace add DietrichGebert/ponytail' then 'claude plugin install ponytail@ponytail'."
		return 0
	fi
	log "Adding Ponytail plugin marketplace."
	claude plugin marketplace add DietrichGebert/ponytail || warn "Ponytail marketplace add failed; add it manually via /plugin."
	log "Installing Ponytail plugin."
	claude plugin install ponytail@ponytail || warn "Ponytail install failed; finish via /plugin and trust hooks in /hooks."
}

graphify_guidance_block() {
	cat <<'BLOCK'
<!-- >>> ClaudeHelper graphify guidance -->
<!-- ClaudeHelper-Version: 2026-07-21-v2 -->
# graphify
- **graphify** (`~/.claude/skills/graphify/SKILL.md`) turns any input (code, docs, papers, images) into a clustered knowledge graph with HTML + JSON + an audit report.
- Invoke it via the Skill tool (`skill: "graphify"`) **proactively — without waiting for a command** — whenever it would help, including:
  - **Understanding a codebase**: onboarding to, or answering architecture / dependency / "how does this fit together" questions about, a new or large repo.
  - **Synthesizing many documents**: several docs, papers, or notes that need cross-linking into one map.
  - **Explicit mapping asks**: any request to "map", "graph", "diagram", or "show relationships" — not just the literal `/graphify`.
  - **Before a codebase-wiki update**: run graphify first so `graphify-out/GRAPH_REPORT.md` exists for `update-codebase-wiki.py` (`make wiki-ai`) to consume.
- `/graphify` remains the explicit manual trigger. For a clearly expensive run, give a one-line heads-up and proceed rather than asking permission.
<!-- <<< ClaudeHelper graphify guidance -->
BLOCK
}

install_graphify_guidance() {
	[[ "$RUN_GRAPHIFY" -eq 1 ]] || return 0
	local target="$HOME/.claude/CLAUDE.md"
	local block
	block="$(graphify_guidance_block)"

	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would ensure $target contains the ClaudeHelper graphify guidance block."
		return 0
	fi

	mkdir -p "$(dirname "$target")"
	if [[ -f "$target" ]] && grep -Fq "$GRAPHIFY_MARKER_START" "$target"; then
		local current
		current="$(awk -v s="$GRAPHIFY_MARKER_START" -v e="$GRAPHIFY_MARKER_END" \
			'$0==s{c=1} c{print} $0==e{c=0}' "$target")"
		if [[ "$current" == "$block" ]]; then
			log "graphify guidance in $target already current."
			return 0
		fi
	fi

	local tmp
	tmp="$(mktemp)"
	if [[ -f "$target" ]]; then
		awk -v s="$GRAPHIFY_MARKER_START" -v e="$GRAPHIFY_MARKER_END" \
			'$0==s{skip=1; next} $0==e{skip=0; next} !skip{print}' "$target" >"$tmp"
		backup_file "$target"
	else
		: >"$tmp"
	fi
	[[ -s "$tmp" ]] && printf '\n' >>"$tmp"
	printf '%s\n' "$block" >>"$tmp"
	mv "$tmp" "$target"
	log "Updated graphify guidance in $target."
}

ensure_prereqs() {
	if command -v uv >/dev/null 2>&1 || command -v pipx >/dev/null 2>&1; then
		return 0
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would install uv via Homebrew (needed for graphify and code-review-graph)."
		return 0
	fi
	if command -v brew >/dev/null 2>&1; then
		log "Installing uv via Homebrew (needed for graphify and code-review-graph)."
		brew install uv || warn "uv install failed; graphify/code-review-graph may not install."
	else
		warn "Homebrew not found; install uv or pipx manually so graphify and code-review-graph can install."
	fi
}

uv_or_pipx_install() {
	local package="$1"
	if command -v uv >/dev/null 2>&1; then
		uv tool install "$package"
	elif command -v pipx >/dev/null 2>&1; then
		pipx install "$package"
	else
		python3 -m pip install --user "$package"
	fi
}

install_crg() {
	[[ "$RUN_CRG" -eq 1 ]] || return 0
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would install code-review-graph and register it: code-review-graph install --platform claude-code -y"
		[[ "$RUN_CRG_BUILD" -eq 1 ]] && log "Would build the code-review-graph index for this repo."
		return 0
	fi
	if ! command -v code-review-graph >/dev/null 2>&1; then
		log "Installing code-review-graph."
		uv_or_pipx_install code-review-graph || warn "code-review-graph install failed."
	fi
	if command -v code-review-graph >/dev/null 2>&1; then
		log "Registering code-review-graph for Claude Code."
		code-review-graph install --platform claude-code -y || warn "code-review-graph Claude Code registration failed."
		if [[ "$RUN_CRG_BUILD" -eq 1 ]]; then
			log "Building code-review-graph index for this repo."
			code-review-graph build || warn "code-review-graph build failed."
		fi
	else
		warn "code-review-graph not on PATH; skipping registration."
	fi
}

setup_context7() {
	[[ "$RUN_CONTEXT7" -eq 1 ]] || return 0
	if ! command -v npx >/dev/null 2>&1; then
		warn "npx not found; skipping Context7 setup."
		return 0
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would run Context7 setup: npx ctx7 setup --claude --mcp -p -y"
		return 0
	fi
	log "Running Context7 setup for this project."
	npx ctx7 setup --claude --mcp -p -y || warn "Context7 setup failed; run 'npx ctx7 setup' manually to finish it interactively."
}

setup_impeccable() {
	[[ "$RUN_IMPECCABLE" -eq 1 ]] || return 0
	if ! command -v npx >/dev/null 2>&1; then
		warn "npx not found; skipping Impeccable setup."
		return 0
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would install Impeccable: npx impeccable skills install -y --providers=claude-code --scope=project"
		return 0
	fi
	log "Installing Impeccable design skill/hooks for Claude Code."
	npx impeccable skills install -y --providers=claude-code --scope=project ||
		warn "Impeccable install failed or was cancelled."
}

clone_llm_council() {
	[[ "$WITH_LLM_COUNCIL" -eq 1 ]] || return 0
	local dest="$HOME/.local/share/llm-council"
	if [[ -d "$dest/.git" ]]; then
		log "llm-council already cloned at $dest."
		return 0
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would clone karpathy/llm-council into $dest."
		return 0
	fi
	if ! command -v git >/dev/null 2>&1; then
		warn "git not found; skipping llm-council clone."
		return 0
	fi
	mkdir -p "$(dirname "$dest")"
	git clone https://github.com/karpathy/llm-council.git "$dest" || warn "llm-council clone failed."
}

while IFS= read -r -d '' file; do
	install_template "$file"
done < <(find "$TEMPLATE_ROOT" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print0)

# Repo files (always).
append_gitignore_block
install_graphify_guidance

# Global tools (skipped by --repo-only/--skip-global).
if [[ "$SKIP_GLOBAL" -eq 1 ]]; then
	log "Skipping global tool installs (--repo-only/--skip-global)."
else
	ensure_prereqs
	maybe_install_rtk
	maybe_install_ponytail
	install_crg
	install_graphify_skill
	install_humanizer
	setup_context7
	setup_impeccable
	clone_llm_council
fi

if [[ -n "${CLAUDE_HELPER_ORCHESTRATED:-}" ]]; then
	cat <<NEXT

Next steps:
  1. Restart Claude Code in this repo (reloads hooks; RTK routing and graphify guidance apply).
  2. Run: make edited-ai
NEXT
else
	cat <<NEXT

Next steps:
  1. Run: bash $HELPER_ROOT/MacOS/part2.sh --dry-run --wire
  2. Run: bash $HELPER_ROOT/MacOS/part2.sh --wire
  3. Restart Claude Code in this repo (reloads hooks; RTK routing and graphify guidance apply).
  4. Run: make edited-ai
NEXT
fi
