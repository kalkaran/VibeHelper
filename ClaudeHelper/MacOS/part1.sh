#!/usr/bin/env bash
# Bootstrap a small Claude Code workflow into the current repository.
#
# Version: 2026-07-27-v5

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_NAME="$(basename "$0")"
SCRIPT_VERSION="2026-07-27-v5"
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
	--no-system-packages)
		# WSL-only apt bootstrap flag; no-op on this platform.
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
PLATFORM_LABEL="${CLAUDE_HELPER_PLATFORM_LABEL:-MacOS}"
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

validate_generated_files() {
	[[ "$DRY_RUN" -eq 1 ]] && return 0
	local pycache
	if ! python3 -m json.tool "$ROOT/.claude/settings.local.json" >/dev/null 2>&1; then
		warn "Generated .claude/settings.local.json failed JSON validation."
	fi
	pycache="$(mktemp -d "${TMPDIR:-/tmp}/claudehelper-pycache.XXXXXX")"
	if ! PYTHONPYCACHEPREFIX="$pycache" python3 -m py_compile "$ROOT"/.claude/hooks/*.py 2>/dev/null; then
		warn "A generated ClaudeHelper hook failed to compile."
	fi
	rm -rf "$pycache"
	if ! bash -n "$ROOT/vibe_scripts/agent-verify.sh" 2>/dev/null; then
		warn "vibe_scripts/agent-verify.sh failed Bash syntax validation."
	fi
}

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

insert_marker() {
	local marker="$1"
	local first_line line
	if [[ -z "$marker" ]]; then
		cat
		return 0
	fi
	IFS= read -r first_line || return 0
	if [[ "$first_line" == '#!'* ]]; then
		printf '%s\n%s\n' "$first_line" "$marker"
		cat
	elif [[ "$first_line" == '---' ]]; then
		printf '%s\n' "$first_line"
		while IFS= read -r line; do
			printf '%s\n' "$line"
			[[ "$line" == '---' ]] && break
		done
		printf '%s\n' "$marker"
		cat
	else
		printf '%s\n%s\n' "$marker" "$first_line"
		cat
	fi
}

write_file() {
	local rel="$1"
	local content="$2"
	local mode="${3:-0644}"
	local dest="$ROOT/$rel"
	local existing_version marker

	if [[ -e "$dest" && "$FORCE" -ne 1 ]]; then
		existing_version="$(managed_file_version "$dest" 2>/dev/null || true)"
		if [[ "$existing_version" == "$SCRIPT_VERSION" ]] ||
			{ [[ "$rel" == *.json ]] && printf '%s\n' "$content" | cmp -s - "$dest"; }; then
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
	mkdir -p "$(dirname "$dest")"
	marker="$(marker_for_path "$dest")"
	printf '%s\n' "$content" | insert_marker "$marker" >"$dest"
	chmod "$mode" "$dest"
	log "Wrote $rel"
}

# .claude/settings.local.json has no comment syntax to carry a version marker, so
# write_file() only ever replaces it on an exact byte match (see managed_file_version).
# Any hand edit or older-script layout then makes every future write_file call a
# permanent no-op without --force, including runs that only need to add a new
# required hook. Merge the required hook entries in instead, so re-running the
# installer always ends with them present regardless of what's already on disk.
write_hooks_settings() {
	local content="$1"
	local rel=".claude/settings.local.json"
	local dest="$ROOT/$rel"

	if [[ "$DRY_RUN" -eq 1 ]]; then
		if [[ -e "$dest" ]]; then
			log "Would merge required hooks into $rel"
		else
			log "Would write $rel"
		fi
		return 0
	fi

	if [[ ! -e "$dest" ]]; then
		mkdir -p "$(dirname "$dest")"
		printf '%s\n' "$content" >"$dest"
		chmod 0644 "$dest"
		log "Wrote $rel"
		return 0
	fi

	local merged
	if ! merged="$(
		python3 - "$dest" "$content" <<'PY'
import json
import sys

dest_path, template_text = sys.argv[1], sys.argv[2]
template = json.loads(template_text)

with open(dest_path) as f:
    existing = json.load(f)

# Migrate the legacy shape some earlier installs wrote: hook event arrays sitting
# at the document root instead of nested under "hooks".
EVENT_KEYS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "Notification",
    "Stop", "PreCompact", "PostCompact", "UserPromptSubmit", "SessionStart",
}
hooks = existing.setdefault("hooks", {})
if not isinstance(hooks, dict):
    hooks = {}
    existing["hooks"] = hooks
for key in list(existing.keys()):
    if key in EVENT_KEYS:
        hooks.setdefault(key, existing.pop(key))


def commands_in(entry):
    return {h.get("command") for h in entry.get("hooks", []) if isinstance(h, dict)}


for event, entries in template.get("hooks", {}).items():
    existing_entries = hooks.setdefault(event, [])
    existing_commands = set()
    for entry in existing_entries:
        existing_commands |= commands_in(entry)
    for entry in entries:
        new_commands = commands_in(entry)
        if new_commands and not new_commands & existing_commands:
            existing_entries.append(entry)
            existing_commands |= new_commands

print(json.dumps(existing, indent=2))
PY
	)"; then
		warn "Failed to merge required hooks into $rel; leaving it unchanged."
		return 1
	fi

	backup_file "$dest"
	printf '%s\n' "$merged" >"$dest"
	chmod 0644 "$dest"
	log "Merged required hooks into $rel"
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

install_shared_vibe_script() {
	local name="$1"
	local src content
	for src in "$TEMPLATE_ROOT/vibe_scripts/$name" "$HELPER_ROOT/../templates/vibe_scripts/$name"; do
		if [[ -f "$src" ]]; then
			content="$(cat "$src")"
			write_file "vibe_scripts/$name" "$content" 0755
			return 0
		fi
	done
	warn "Shared vibe script template not found: $name"
}

append_gitignore_block() {
	local path="$ROOT/.gitignore"
	local marker="# VibeHelper local AI/dev tooling"
	local block
	local existed=0
	block=$'# VibeHelper local AI/dev tooling\n*.bak\n*.bak.*\n.cache/\n.agents/\n.claude/\n.codex/\n.mcp.json\nAGENTS.md\nCLAUDE.md\nMakefile\nagent/\nagents/\ncodebase-wiki/\ngraphify-out/\nnode_modules/\nnotes/\nobsidian/\nvendor/\nvibe_scripts/\nskills-lock.json\nbiome.json\n'
	if [[ "$DRY_RUN" -eq 1 ]]; then
		log "Would ensure .gitignore has VibeHelper local-file entries"
		return 0
	fi
	[[ -e "$path" ]] && existed=1
	touch "$path"
	if grep -Fq "$marker" "$path"; then
		log ".gitignore already contains VibeHelper block"
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
		warn "npx not found; skipping Humanizer install."
		return 0
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
	printf '%s\n' "$GRAPHIFY_MARKER_START"
	printf '<!-- ClaudeHelper-Version: %s -->\n' "$SCRIPT_VERSION"
	cat <<'BLOCK'
# graphify
- **graphify** (`~/.claude/skills/graphify/SKILL.md`) turns any input (code, docs, papers, images) into a clustered knowledge graph with HTML + JSON + an audit report.
- Invoke it via the Skill tool (`skill: "graphify"`) **proactively — without waiting for a command** — whenever it would help, including:
  - **Understanding a codebase**: onboarding to, or answering architecture / dependency / "how does this fit together" questions about, a new or large repo.
  - **Synthesizing many documents**: several docs, papers, or notes that need cross-linking into one map.
  - **Explicit mapping asks**: any request to "map", "graph", "diagram", or "show relationships" — not just the literal `/graphify`.
  - **Before a codebase-wiki update**: run graphify first so `graphify-out/GRAPH_REPORT.md` exists for `update-codebase-wiki.py` (`make wiki-ai`) to consume.
- `/graphify` remains the explicit manual trigger. For a clearly expensive run, give a one-line heads-up and proceed rather than asking permission.
BLOCK
	printf '%s\n' "$GRAPHIFY_MARKER_END"
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
		if command -v brew >/dev/null 2>&1; then
			log "Would install uv via Homebrew (needed for graphify and code-review-graph)."
		else
			log "Would install uv via the official install script."
		fi
		return 0
	fi
	if command -v brew >/dev/null 2>&1; then
		log "Installing uv via Homebrew (needed for graphify and code-review-graph)."
		if brew install uv; then
			return 0
		fi
		warn "uv install via Homebrew failed; trying the official install script."
	fi
	if command -v curl >/dev/null 2>&1; then
		log "Installing uv via the official install script."
		if curl -LsSf https://astral.sh/uv/install.sh | sh; then
			log "uv install script completed."
			return 0
		fi
		warn "uv install script failed; install uv or pipx manually so graphify and code-review-graph can install."
		return 0
	fi
	warn "Neither brew nor curl is available to install uv; install uv or pipx manually so graphify and code-review-graph can install."
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
	if [[ -f ".claude/skills/impeccable/SKILL.md" ]]; then
		log "Impeccable is already installed for Claude Code."
		return 0
	fi
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

install_generated_repo_files() {
	local content

	content="$(
		cat <<'SETTINGS_EOF'
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "root=\"${CLAUDE_PROJECT_DIR:-}\"; [ -n \"$root\" ] || root=\"$(git rev-parse --show-toplevel 2>/dev/null || pwd)\"; hook=\"$root/.claude/hooks/pre_tool_use_policy.py\"; [ -f \"$hook\" ] || exit 0; exec python3 \"$hook\"",
            "timeout": 10
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "root=\"${CLAUDE_PROJECT_DIR:-}\"; [ -n \"$root\" ] || root=\"$(git rev-parse --show-toplevel 2>/dev/null || pwd)\"; hook=\"$root/.claude/hooks/rtk_pre_tool_use.py\"; [ -f \"$hook\" ] || exit 0; exec python3 \"$hook\"",
            "timeout": 10
          }
        ]
      },
      {
        "matcher": "Grep|Glob|Read",
        "hooks": [
          {
            "type": "command",
            "command": "root=\"${CLAUDE_PROJECT_DIR:-}\"; [ -n \"$root\" ] || root=\"$(git rev-parse --show-toplevel 2>/dev/null || pwd)\"; hook=\"$root/.claude/hooks/graph_first_policy.py\"; [ -f \"$hook\" ] || exit 0; exec python3 \"$hook\"",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "root=\"${CLAUDE_PROJECT_DIR:-}\"; [ -n \"$root\" ] || root=\"$(git rev-parse --show-toplevel 2>/dev/null || pwd)\"; hook=\"$root/.claude/hooks/post_edit_check.py\"; [ -f \"$hook\" ] || exit 0; exec python3 \"$hook\"",
            "timeout": 120
          }
        ]
      },
      {
        "matcher": "mcp__code-review-graph__.*",
        "hooks": [
          {
            "type": "command",
            "command": "root=\"${CLAUDE_PROJECT_DIR:-}\"; [ -n \"$root\" ] || root=\"$(git rev-parse --show-toplevel 2>/dev/null || pwd)\"; hook=\"$root/.claude/hooks/graph_first_policy.py\"; [ -f \"$hook\" ] || exit 0; exec python3 \"$hook\"",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "root=\"${CLAUDE_PROJECT_DIR:-}\"; [ -n \"$root\" ] || root=\"$(git rev-parse --show-toplevel 2>/dev/null || pwd)\"; hook=\"$root/.claude/hooks/stop_edited_check.py\"; [ -f \"$hook\" ] || exit 0; exec python3 \"$hook\"",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
SETTINGS_EOF
	)"
	write_hooks_settings "$content"

	content="$(
		cat <<'PRETOOL_EOF'
#!/usr/bin/env python3
"""Conservative Claude Code PreToolUse hook for Bash commands."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GIT_WRITE_COMMANDS = {
    "add",
    "am",
    "apply",
    "bisect",
    "branch",
    "checkout",
    "cherry-pick",
    "clean",
    "commit",
    "merge",
    "mv",
    "pull",
    "push",
    "rebase",
    "reset",
    "restore",
    "revert",
    "rm",
    "stash",
    "switch",
    "tag",
    "worktree",
}

ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
SUDO_OPTIONS_WITH_VALUES = {
    "-C",
    "-D",
    "-g",
    "-h",
    "-p",
    "-r",
    "-R",
    "-t",
    "-T",
    "-u",
    "--chdir",
    "--close-from",
    "--group",
    "--host",
    "--other-user",
    "--prompt",
    "--role",
    "--type",
    "--user",
}
ENV_OPTIONS_WITH_VALUES = {"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}
GIT_OPTIONS_WITH_VALUES = {
    "-C",
    "-c",
    "--config-env",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}


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
        root = os.environ.get("CLAUDE_PROJECT_DIR")
        base = Path(root) if root else Path.cwd()
        log_path = base / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[claude-helper] failed to log hook error: {exc}", file=sys.stderr)


def read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        log_hook_error("stdin-read-failed", error=exc)
        return {}
    if not raw.strip():
        log_hook_error("stdin-empty")
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log_hook_error("json-decode-failed", raw=raw, error=exc)
        return {}
    return data if isinstance(data, dict) else {}


def nested_get(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def find_command(data: dict[str, Any]) -> str:
    candidates = [
        data.get("command"),
        data.get("cmd"),
        nested_get(data, "tool_input", "command"),
        nested_get(data, "tool_input", "cmd"),
        nested_get(data, "input", "command"),
        nested_get(data, "input", "cmd"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def split_segments(command: str) -> list[list[str]]:
    segments: list[list[str]] = []
    for raw_segment in re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", command):
        raw_segment = raw_segment.strip()
        if not raw_segment:
            continue
        try:
            parts = shlex.split(raw_segment)
        except ValueError:
            parts = raw_segment.split()
        if parts:
            segments.append(parts)
    return segments


def command_name(value: str) -> str:
    return os.path.basename(value.rstrip("/"))


def unwrap_command(parts: list[str]) -> list[str]:
    remaining = list(parts)
    while remaining and ASSIGNMENT.match(remaining[0]):
        remaining.pop(0)
    if not remaining:
        return []

    head = command_name(remaining[0])
    if head == "env":
        remaining = remaining[1:]
        while remaining:
            if ASSIGNMENT.match(remaining[0]):
                remaining.pop(0)
                continue
            if not remaining[0].startswith("-"):
                break
            option = remaining.pop(0)
            option_name = option.split("=", 1)[0]
            if (
                option_name in ENV_OPTIONS_WITH_VALUES
                and "=" not in option
                and remaining
            ):
                remaining.pop(0)
        return unwrap_command(remaining)

    if head == "sudo":
        remaining = remaining[1:]
        while remaining and remaining[0].startswith("-"):
            option = remaining.pop(0)
            option_name = option.split("=", 1)[0]
            if (
                option_name in SUDO_OPTIONS_WITH_VALUES
                and "=" not in option
                and remaining
            ):
                remaining.pop(0)
        return unwrap_command(remaining)

    if head in {"command", "nohup", "time"}:
        remaining = remaining[1:]
        while remaining and remaining[0].startswith("-"):
            remaining.pop(0)
        return unwrap_command(remaining)

    return remaining


def git_subcommand(parts: list[str]) -> str:
    parts = unwrap_command(parts)
    if not parts or command_name(parts[0]) != "git":
        return ""

    index = 1
    while index < len(parts) and parts[index].startswith("-"):
        option = parts[index]
        option_name = option.split("=", 1)[0]
        index += 1
        if option_name in GIT_OPTIONS_WITH_VALUES and "=" not in option:
            index += 1
    return parts[index] if index < len(parts) else ""


def is_git_write(parts: list[str]) -> bool:
    return git_subcommand(parts) in GIT_WRITE_COMMANDS


def is_destructive(parts: list[str]) -> bool:
    parts = unwrap_command(parts)
    if not parts:
        return False
    head = command_name(parts[0])
    if head == "rm" and any(
        flag == "--recursive"
        or (
            flag.startswith("-")
            and not flag.startswith("--")
            and "r" in flag[1:].lower()
        )
        for flag in parts[1:]
    ):
        return True
    if head in {"chmod", "chown"} and any(
        flag == "--recursive"
        or (flag.startswith("-") and not flag.startswith("--") and "R" in flag[1:])
        for flag in parts[1:]
    ):
        return True
    if head == "dd":
        return True
    if head == "mkfs" or head.startswith("mkfs."):
        return True
    return False


def nested_shell_command(parts: list[str]) -> str:
    parts = unwrap_command(parts)
    if not parts:
        return ""
    head = command_name(parts[0])
    if head in {"bash", "dash", "sh", "zsh"} and "-c" in parts[1:]:
        index = parts.index("-c", 1)
        return parts[index + 1] if index + 1 < len(parts) else ""
    if head == "eval" and len(parts) > 1:
        return " ".join(parts[1:])
    return ""


def forbidden_kind(command: str) -> str:
    for parts in split_segments(command):
        if is_git_write(parts):
            return "git"
        if is_destructive(parts):
            return "destructive"
        nested = nested_shell_command(parts)
        if nested:
            nested_kind = forbidden_kind(nested)
            if nested_kind:
                return nested_kind
    return ""


def main() -> int:
    payload = read_payload()
    command = find_command(payload)
    if not command:
        return 0

    kind = forbidden_kind(command)
    if kind == "git":
        print(
            "[claude-helper] blocked Git-writing command. Ask the user before staging, committing, pushing, switching branches, or rewriting history.",
            file=sys.stderr,
        )
        return 2
    if kind == "destructive":
        print(
            "[claude-helper] blocked destructive shell command. Ask the user before running destructive filesystem operations.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PRETOOL_EOF
	)"
	write_file ".claude/hooks/pre_tool_use_policy.py" "$content" 0755

	content="$(
		cat <<'RTK_EOF'
#!/usr/bin/env python3
"""Claude Code PreToolUse hook that routes eligible Bash commands through RTK.

The hook is deliberately conservative. It rewrites simple read-only/noisy shell
commands when `rtk` is available, and otherwise lets the original command run.
It relies on the PreToolUse `updatedInput` capability to transparently replace
the Bash command before it executes, so Claude does not have to invoke `rtk`
by hand.
"""

from __future__ import annotations

import json
import os
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
INTERACTIVE_COMMANDS = {
    "less",
    "man",
    "more",
    "nano",
    "ssh",
    "tail",
    "top",
    "vim",
    "vi",
    "watch",
}
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
        root = os.environ.get("CLAUDE_PROJECT_DIR")
        base = Path(root) if root else Path.cwd()
        log_path = base / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[claude-helper] failed to log hook error: {exc}", file=sys.stderr)


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
        return parts[1] in READ_ONLY_GIT_SUBCOMMANDS and not has_unsafe_git_option(
            parts
        )
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
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Routing eligible Bash command through RTK to reduce Claude Code token usage.",
                    "updatedInput": {"command": wrapped},
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
RTK_EOF
	)"
	write_file ".claude/hooks/rtk_pre_tool_use.py" "$content" 0755

	content="$(
		cat <<'GRAPHFIRST_EOF'
#!/usr/bin/env python3
"""Enforce: use code-review-graph MCP tools before Grep/Glob/Read.

One script serves two hook wirings, distinguished by tool_name:
- PostToolUse on mcp__code-review-graph__* marks the session as "graph used".
- PreToolUse on Grep/Glob/Read blocks (exit 2) until the session is marked.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACKED_TOOLS = {"Grep", "Glob", "Read"}


def log_hook_error(reason: str, *, raw: str = "", error: Exception | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": "graph_first_policy.py",
        "reason": reason,
    }
    if error is not None:
        entry["error"] = str(error)
    if raw:
        entry["stdin_preview"] = raw[:8000]
    try:
        root = os.environ.get("CLAUDE_PROJECT_DIR")
        base = Path(root) if root else Path.cwd()
        log_path = base / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[claude-helper] failed to log hook error: {exc}", file=sys.stderr)


def read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        log_hook_error("stdin-read-failed", error=exc)
        return {}
    if not raw.strip():
        log_hook_error("stdin-empty")
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log_hook_error("json-decode-failed", raw=raw, error=exc)
        return {}
    return data if isinstance(data, dict) else {}


def marker_path(session_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return os.path.join(tempfile.gettempdir(), f"claude-graph-first-{safe}")


def main() -> int:
    payload = read_payload()
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name", "")
    if not isinstance(session_id, str) or not session_id:
        return 0  # fail open: nothing to key the marker on

    path = marker_path(session_id)

    if isinstance(tool_name, str) and tool_name.startswith("mcp__code-review-graph__"):
        try:
            open(path, "w").close()
        except OSError:
            pass
        return 0

    if tool_name in TRACKED_TOOLS and not os.path.exists(path):
        print(
            "[claude-helper] Use a code-review-graph MCP tool (query_graph_tool, "
            "semantic_search_nodes_tool, get_impact_radius_tool, etc.) before "
            f"{tool_name} to explore this codebase, per CLAUDE.md. Call one now, "
            "then retry.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
GRAPHFIRST_EOF
	)"
	write_file ".claude/hooks/graph_first_policy.py" "$content" 0755

	content="$(
		cat <<'POSTEDIT_EOF'
#!/usr/bin/env python3
"""Run edited-file checks after Claude Code edit tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def repo_root(payload: dict[str, Any]) -> Path:
    candidates = [
        payload.get("cwd"),
        os.environ.get("CLAUDE_PROJECT_DIR"),
        str(Path.cwd()),
    ]
    start = next(
        (
            Path(value)
            for value in candidates
            if isinstance(value, str) and value.strip()
        ),
        Path.cwd(),
    )
    completed = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return (
        Path(completed.stdout.strip()) if completed.stdout.strip() else start.resolve()
    )


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
        root = os.environ.get("CLAUDE_PROJECT_DIR")
        base = Path(root) if root else Path.cwd()
        log_path = base / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[claude-helper] failed to log hook error: {exc}", file=sys.stderr)


def read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        log_hook_error("stdin-read-failed", error=exc)
        return {}
    if not raw.strip():
        log_hook_error("stdin-empty")
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log_hook_error("json-decode-failed", raw=raw, error=exc)
        return {}
    return data if isinstance(data, dict) else {}


def collect_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"file_path", "path", "notebook_path"} and isinstance(item, str):
                paths.append(item)
            elif key in {"files", "paths"} and isinstance(item, list):
                paths.extend(str(entry) for entry in item if isinstance(entry, str))
            else:
                paths.extend(collect_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(collect_paths(item))
    return paths


def existing_project_files(root: Path, paths: list[str]) -> list[str]:
    result: list[str] = []
    resolved_root = root.resolve()
    for path in paths:
        candidate = (
            (root / path).resolve()
            if not Path(path).is_absolute()
            else Path(path).resolve()
        )
        try:
            rel = candidate.relative_to(resolved_root)
        except ValueError:
            continue
        if candidate.is_file():
            result.append(str(rel))
    return sorted(set(result))


def main() -> int:
    payload = read_payload()
    root = repo_root(payload)
    checker = root / "vibe_scripts" / "agent-check-edited.py"
    if not checker.is_file():
        print(
            "[post-edit-check] vibe_scripts/agent-check-edited.py not found; skipping",
            file=sys.stderr,
        )
        return 0

    files = existing_project_files(
        root, collect_paths(payload.get("tool_input", payload))
    )
    if not files:
        return 0

    cache_file = root / ".cache" / "claude-edited-files.txt"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        set(cache_file.read_text(encoding="utf-8").splitlines())
        if cache_file.exists()
        else set()
    )
    cache_file.write_text(
        "\n".join(sorted(existing | set(files))) + "\n", encoding="utf-8"
    )

    completed = subprocess.run(
        [sys.executable, str(checker), *files],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout.rstrip()
    if completed.returncode != 0:
        if output:
            print(output, file=sys.stderr)
        print(
            "[post-edit-check] edited-file checks failed; fix the reported errors before continuing.",
            file=sys.stderr,
        )
        return 2
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
POSTEDIT_EOF
	)"
	write_file ".claude/hooks/post_edit_check.py" "$content" 0755

	content="$(
		cat <<'STOPEDIT_EOF'
#!/usr/bin/env python3
"""Run the edited-file quality gate before Claude Code stops."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_hook_error(reason: str, *, raw: str = "", error: Exception | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "hook": "stop_edited_check.py",
        "reason": reason,
    }
    if error is not None:
        entry["error"] = str(error)
    if raw:
        entry["stdin_preview"] = raw[:8000]
    try:
        root = os.environ.get("CLAUDE_PROJECT_DIR")
        base = Path(root) if root else Path.cwd()
        log_path = base / ".cache" / "hook-errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception as exc:
        print(f"[claude-helper] failed to log hook error: {exc}", file=sys.stderr)


def read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception as exc:
        log_hook_error("stdin-read-failed", error=exc)
        return {}
    if not raw.strip():
        log_hook_error("stdin-empty")
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log_hook_error("json-decode-failed", raw=raw, error=exc)
        return {}
    return data if isinstance(data, dict) else {}


def repo_root(payload: dict[str, Any]) -> Path:
    candidates = [
        payload.get("cwd"),
        os.environ.get("CLAUDE_PROJECT_DIR"),
        str(Path.cwd()),
    ]
    start = next(
        (
            Path(value)
            for value in candidates
            if isinstance(value, str) and value.strip()
        ),
        Path.cwd(),
    )
    completed = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return (
        Path(completed.stdout.strip()) if completed.stdout.strip() else start.resolve()
    )


def existing_project_files(root: Path, paths: list[str]) -> list[str]:
    resolved_root = root.resolve()
    result: list[str] = []
    for path in paths:
        candidate = (root / path).resolve()
        try:
            rel = candidate.relative_to(resolved_root)
        except ValueError:
            continue
        if candidate.is_file():
            result.append(str(rel))
    return sorted(set(result))


def block_stop(output: str) -> int:
    detail = output.strip()
    if len(detail) > 4000:
        detail = detail[-4000:]
    reason = "Edited-file checks failed. Fix the errors before finishing the turn."
    if detail:
        reason += "\n\n" + detail
    print(json.dumps({"decision": "block", "reason": reason}), file=sys.stderr)
    return 2


def main() -> int:
    payload = read_payload()
    if payload.get("stop_hook_active") is True:
        return 0

    root = repo_root(payload)
    checker = root / "vibe_scripts" / "agent-check-edited.py"
    cache_file = root / ".cache" / "claude-edited-files.txt"

    if not checker.is_file() or not cache_file.is_file():
        return 0

    files = [
        line.strip()
        for line in cache_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    files = existing_project_files(root, files)
    if not files:
        cache_file.unlink(missing_ok=True)
        return 0

    completed = subprocess.run(
        [sys.executable, str(checker), *files],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode == 0:
        cache_file.unlink(missing_ok=True)
        if completed.stdout.strip():
            print(completed.stdout.rstrip())
        return 0
    return block_stop(completed.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
STOPEDIT_EOF
	)"
	write_file ".claude/hooks/stop_edited_check.py" "$content" 0755

	content="$(
		cat <<'AGENTVERIFY_EOF'
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
AGENTVERIFY_EOF
	)"
	write_file "vibe_scripts/agent-verify.sh" "$content" 0755
}

install_agent_scaffold_files() {
	local content

	content="$(
		cat <<'CLAUDEMD_EOF'
# Claude project instructions

Use this repository with minimal, verifiable changes.

## Project Structure & Module Organization

- Start with `CLAUDE.md`, then read `/agent/` workflow files and relevant `/codebase-wiki/` pages.
- Treat `vibe_scripts/` as generated helper tooling for AI-safe verification.
- Treat `.claude/` as Claude Code project configuration, hooks, and slash commands.
- Treat `codebase-wiki/` as reviewed durable project memory.
- Treat `notes/` as session logging when the project asks for it.

## Build, Test, and Development Commands

- Use existing project commands first.
- Use `make edited-ai` after edits when available.
- Use `make verify-ai` for broad, risky, security-sensitive, or cross-workflow changes.
- Use `make wiki-ai` only when stable project knowledge should be drafted into `codebase-wiki/`.
- Use `make skills-check` after helper upgrades or when managed skills/plugins may be stale; use `make skills-update` only when the user asks to refresh them or an installer explicitly allows it.

## Testing Guidelines

- Run the narrowest check that covers the change.
- Add or update tests for behavior changes.
- For UI changes, verify responsive layout and user-facing states when possible.
- If a check cannot run, report why and what was run instead.

## Coding Style & Naming Conventions

- Follow existing project style.
- Prefer names that match nearby code.
- Keep changes closely scoped to the requested behavior.
- Add comments only when they clarify non-obvious logic.

## Repo Memory Workflow

- Promote only stable, reusable facts into `codebase-wiki/`.
- Do not copy raw tool output, temporary task notes, speculation, credentials, or user-private data into repo memory.
- Review generated wiki updates before relying on them.

## Security & Configuration Tips

- Do not weaken validation, authorization, escaping, CSRF protections, rate limits, or data-loss safeguards.
- Do not commit secrets or machine-local configuration.
- Ask before Git-writing commands such as stage, commit, push, reset, checkout, switch, or rebase.

## Agent Workflow

Before editing:
- Read relevant `/codebase-wiki/` pages for durable repo memory.
- Read `/agent/index.md` for workflow details on non-trivial tasks.
- Use Context7 for library/API/framework docs, setup, configuration, or unfamiliar APIs.
- Use Humanizer before finalizing user-facing prose, docs, README copy, PR descriptions, release notes, or other natural-language text when the skill is available.
- When an Impeccable critique would benefit from subagents, ask: "Use subagents for the Impeccable critique? Reply yes to approve." Treat a plain "yes" as approval only when it directly answers that question; otherwise continue single-agent.
- Prefer existing local patterns and helper APIs over new abstractions.
- Read only the minimal files needed.

Implementation rules:
- Prefer small diffs.
- Do not introduce new production dependencies unless clearly justified.
- Do not rewrite architecture unless explicitly asked.
- Do not remove validation, error handling, security checks, accessibility, data-loss protection, or meaningful tests to make code shorter.
- Write or update tests for behavior changes.

Before final answer:
- Run the relevant verification command.
- Summarize files changed.
- Summarize tests/checks run.
- Explain remaining risks.

Session logging:
- Append each user query to `notes/queries.md` with the current date and keep prior entries intact.
- Append each user query and assistant reply to `notes/conversation-log.md` with the current date and keep prior entries intact.
CLAUDEMD_EOF
	)"
	write_file "CLAUDE.md" "$content" 0644

	content="$(
		cat <<'AGENTIDX_EOF'
# Agent workflow index

This folder contains durable instructions for AI coding agents. Keep `CLAUDE.md` short and put details here.

Read order for non-trivial tasks:
1. `CLAUDE.md`
2. `/agent/commands.md`
3. `/agent/context-strategy.md`
4. `/agent/coding-rules.md`
5. `/agent/verify.md`
6. Relevant pages in `/codebase-wiki/`

Default workflow:
1. Understand the task and expected behavior.
2. Read relevant `/codebase-wiki/` pages for durable repo memory.
3. Use Context7 for framework, library, SDK, API, CLI, setup, or configuration details.
4. Reuse existing code, prefer standard library/native capabilities, avoid new dependencies, and make the smallest safe change.
5. Add or update tests when behavior changes.
6. Run `make edited-ai` after edits. Use `make verify-ai` for broader checks when risk warrants it.
7. Run `make wiki-ai` when substantial discovery reveals stable project knowledge, then review the generated wiki sections.
8. Run `make skills-check` after helper upgrades or when managed skills/plugins may be stale; run `make skills-update` only on explicit refresh requests.
AGENTIDX_EOF
	)"
	write_file "agent/index.md" "$content" 0644

	content="$(
		cat <<'AGENTCMD_EOF'
# Repository commands for agents

Prefer existing project commands. These standard targets may exist after bootstrap:

```bash
make edited-ai
make wiki-ai
make lint-ai
make typecheck-ai
make test-ai
make security-ai
make verify-ai
make skills-check
make skills-update
```

Claude Code slash commands are installed under `.claude/commands/`:

```text
/edited-ai
/verify-ai
/wiki-ai
```

Context7:

```bash
npx ctx7 setup
```

Use Context7 when work depends on external library, framework, SDK, API, CLI, setup, or configuration details.

Managed skills/plugins:

```bash
make skills-check
make skills-update
python3 vibe_scripts/sync-skills.py --check
python3 vibe_scripts/sync-skills.py --update --dry-run
```

Use `skills-check` after helper upgrades or when skill/plugin behavior seems stale. Use `skills-update` only when the user asks to refresh managed skills/plugins or an installer explicitly allows it.
AGENTCMD_EOF
	)"
	write_file "agent/commands.md" "$content" 0644

	content="$(
		cat <<'AGENTVFY_EOF'
# Verification

Default checks:

```bash
make edited-ai
```

Broader checks:

```bash
make lint-ai
make typecheck-ai
make test-ai
make security-ai
make verify-ai
```

Use `make verify-ai` for large, risky, security-sensitive, or cross-project changes.

If a generated target does not exist, use the closest existing project command and record what ran in the final answer.
AGENTVFY_EOF
	)"
	write_file "agent/verify.md" "$content" 0644

	content="$(
		cat <<'AGENTRULES_EOF'
# Coding rules

Engineering discipline:
1. Does this need to exist? If not, skip it.
2. Is it already in this codebase? Reuse it.
3. Does the standard library do it? Use that.
4. Does the platform/browser/framework provide it natively? Use that.
5. Is an existing dependency already installed that does it? Use that.
6. Can it safely stay simple? Keep it simple.
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
AGENTRULES_EOF
	)"
	write_file "agent/coding-rules.md" "$content" 0644

	content="$(
		cat <<'AGENTCTX_EOF'
# Context strategy

Read narrowly before editing:
- Start from the changed workflow or failing command.
- Prefer `rg` and `rg --files` for discovery.
- Read adjacent tests and existing helpers before creating new code.
- Do not paste large generated logs into the conversation; use `.cache/ai-quality/` logs when full output is needed.

Durable memory:
- Treat `codebase-wiki/` as reviewed project memory.
- Add to it only when a task reveals stable architecture, testing, security, integration, or operational facts.
- Do not store temporary task notes, raw tool output, speculation, credentials, or user-private data.
AGENTCTX_EOF
	)"
	write_file "agent/context-strategy.md" "$content" 0644

	content="$(
		cat <<'WIKIIDX_EOF'
# Codebase wiki

This is durable, human-readable memory for AI agents and developers.

Pages:
- [Architecture](architecture.md)
- [Testing](testing.md)
- [Conventions](conventions.md)
- [Known issues](known-issues.md)
- [Risky areas](risky-areas.md)

Maintenance rule:
- Use `make wiki-ai` to draft concise updates from repo guidance and discovery output.
- Promote only stable, reusable facts into this wiki.
- Do not add temporary task notes, raw linter output, or speculation.
- Review generated sections before relying on them for future work.
WIKIIDX_EOF
	)"
	write_file "codebase-wiki/index.md" "$content" 0644

	content="$(
		cat <<'WIKIARCH_EOF'
# Architecture

Run `make wiki-ai` after meaningful discovery to draft stable architecture notes.
WIKIARCH_EOF
	)"
	write_file "codebase-wiki/architecture.md" "$content" 0644

	content="$(
		cat <<'WIKITEST_EOF'
# Testing

Run `make wiki-ai` after meaningful discovery to draft stable testing notes.
WIKITEST_EOF
	)"
	write_file "codebase-wiki/testing.md" "$content" 0644

	content="$(
		cat <<'WIKICONV_EOF'
# Conventions

Run `make wiki-ai` after meaningful discovery to draft stable conventions.
WIKICONV_EOF
	)"
	write_file "codebase-wiki/conventions.md" "$content" 0644

	content="$(
		cat <<'WIKIISSUES_EOF'
# Known issues

Add confirmed durable issues here. Do not store speculative task notes.
WIKIISSUES_EOF
	)"
	write_file "codebase-wiki/known-issues.md" "$content" 0644

	content="$(
		cat <<'WIKIRISKY_EOF'
# Risky areas

Add confirmed risky areas here after review.
WIKIRISKY_EOF
	)"
	write_file "codebase-wiki/risky-areas.md" "$content" 0644

	content="$(
		cat <<'NOTESQ_EOF'
# Queries

Append user requests here when the project asks for session logging.
NOTESQ_EOF
	)"
	write_file "notes/queries.md" "$content" 0644

	content="$(
		cat <<'NOTESLOG_EOF'
# Conversation log

Append user requests and assistant replies here when the project asks for session logging.
NOTESLOG_EOF
	)"
	write_file "notes/conversation-log.md" "$content" 0644

	content="$(
		cat <<'CMDEDITED_EOF'
---
description: Run the edited-file quality gate
argument-hint: [files...]
allowed-tools: Bash(make:*), Bash(./vibe_scripts/agent-check-edited.py:*)
---

Run the edited-file quality gate for the current repository.

If arguments were provided, pass them as file paths to `./vibe_scripts/agent-check-edited.py`. Otherwise run `make edited-ai`.

Arguments: $ARGUMENTS
CMDEDITED_EOF
	)"
	write_file ".claude/commands/edited-ai.md" "$content" 0644

	content="$(
		cat <<'CMDVERIFY_EOF'
---
description: Run broader AI-safe verification
allowed-tools: Bash(make:*)
---

Run `make verify-ai` and summarize the result. If the target is unavailable, inspect `Makefile` and run the closest available AI-safe verification targets.
CMDVERIFY_EOF
	)"
	write_file ".claude/commands/verify-ai.md" "$content" 0644

	content="$(
		cat <<'CMDWIKI_EOF'
---
description: Draft codebase-wiki updates
allowed-tools: Bash(make:*), Bash(./vibe_scripts/update-codebase-wiki.py:*)
---

Run `make wiki-ai` to draft durable `codebase-wiki/` updates. Review the generated changes before treating them as project memory.
CMDWIKI_EOF
	)"
	write_file ".claude/commands/wiki-ai.md" "$content" 0644
}

while IFS= read -r -d '' file; do
	install_template "$file"
done < <(find "$TEMPLATE_ROOT" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' -print0)

install_shared_vibe_script "ai-quality-wrapper.py"
install_shared_vibe_script "agent-check-edited.py"
install_shared_vibe_script "update-codebase-wiki.py"
install_generated_repo_files
install_agent_scaffold_files
validate_generated_files

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
  3. Run: make skills-check
NEXT
else
	cat <<NEXT

Next steps:
  1. Run: bash $HELPER_ROOT/$PLATFORM_LABEL/part2.sh --dry-run --wire
  2. Run: bash $HELPER_ROOT/$PLATFORM_LABEL/part2.sh --wire
  3. Restart Claude Code in this repo (reloads hooks; RTK routing and graphify guidance apply).
  4. Run: make edited-ai
  5. Run: make skills-check
NEXT
fi
