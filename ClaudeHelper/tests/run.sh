#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd)"

REVIEWER_RULE="After substantive coding, always spawn a separate reviewer agent to inspect the final implementation for breaking changes, scope creep, and alignment with the user request."

assert_reviewer_rule() {
	local file="$1"
	if ! grep -Fq "$REVIEWER_RULE" "$file"; then
		echo "missing independent reviewer-agent rule: $file" >&2
		return 1
	fi
}

assert_target() {
	local makefile="$1"
	local target="$2"
	if ! grep -Eq "^${target}:" "$makefile"; then
		echo "missing Makefile target: $target" >&2
		return 1
	fi
}

assert_gitignore_entries() {
	local gitignore="$1"
	local entry
	for entry in "# VibeHelper local AI/dev tooling" "*.bak" "*.bak.*" ".cache/" ".agents/" ".claude/" ".codex/" ".mcp.json" "AGENTS.md" "CLAUDE.md" "agent/" "codebase-wiki/" "graphify-out/" "node_modules/" "notes/" "obsidian/" "vendor/" "/vibe_scripts/" "/skills-lock.json" "biome.json"; do
		if ! grep -Fxq "$entry" "$gitignore"; then
			echo "missing .gitignore entry: $entry" >&2
			cat "$gitignore" >&2
			return 1
		fi
	done
}

run_template_packaging_test() {
	local required tmp
	for required in \
		ClaudeHelper/templates/skills-lock.json \
		ClaudeHelper/templates/vibe_scripts/sync-skills.py \
		templates/vibe_scripts/ai-quality-wrapper.py \
		templates/vibe_scripts/agent-check-edited.py \
		templates/vibe_scripts/agent-verify.sh \
		templates/vibe_scripts/quality-hook.py \
		templates/vibe_scripts/managed-install.py \
		templates/vibe_scripts/update-codebase-wiki.py; do
		if [[ ! -f "$ROOT/$required" ]]; then
			echo "missing ClaudeHelper installer template: $required" >&2
			return 1
		fi
		if git -C "$ROOT" check-ignore -q "$required"; then
			echo "ClaudeHelper installer template is excluded from release packaging: $required" >&2
			git -C "$ROOT" check-ignore -v "$required" >&2 || true
			return 1
		fi
	done

	tmp="$(mktemp -d)"
	mkdir -p "$tmp/package/VibeHelper/ClaudeHelper/WSL" \
		"$tmp/package/VibeHelper/ClaudeHelper/templates" \
		"$tmp/package/VibeHelper/templates" "$tmp/repo"
	cp "$ROOT/ClaudeHelper/WSL/part1.sh" \
		"$tmp/package/VibeHelper/ClaudeHelper/WSL/part1.sh"
	cp -R "$ROOT/ClaudeHelper/templates/." \
		"$tmp/package/VibeHelper/ClaudeHelper/templates/"
	cp -R "$ROOT/templates/." "$tmp/package/VibeHelper/templates/"
	git -C "$tmp/repo" init -q
	if ! bash "$tmp/package/VibeHelper/ClaudeHelper/WSL/part1.sh" \
		--repo "$tmp/repo" --repo-only \
		--no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail \
		--no-crg --no-context7 >"$tmp/package.log" 2>&1; then
		cat "$tmp/package.log" >&2
		return 1
	fi
	for required in skills-lock.json vibe_scripts/claude/sync-skills.py \
		vibe_scripts/claude/ai-quality-wrapper.py vibe_scripts/claude/agent-check-edited.py \
		vibe_scripts/claude/update-codebase-wiki.py; do
		if [[ ! -f "$tmp/repo/$required" ]]; then
			echo "packaged Claude WSL installer did not copy $required" >&2
			cat "$tmp/package.log" >&2
			return 1
		fi
	done
	rm -rf "$tmp"
}

run_chrome_mcp_test() {
	local platform tmp log tool
	for platform in MacOS WSL; do
		tmp="$(mktemp -d)"
		mkdir -p "$tmp/bin" "$tmp/home" "$tmp/repo"
		git -C "$tmp/repo" init -q
		cat >"$tmp/bin/claude" <<'EOF_CLAUDE'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CLAUDE_CALLS"
if [[ "${1:-} ${2:-} ${3:-}" == "plugin list --json" ]]; then
	if [[ "${CLAUDE_PLUGIN_STATE:-missing}" == "installed" ]]; then
		printf '[{"id":"chrome-devtools-mcp@chrome-devtools-plugins","enabled":true}]\n'
	else
		printf '[]\n'
	fi
	exit 0
fi
if [[ "${1:-} ${2:-} ${3:-}" == "mcp remove chrome-devtools" ]]; then
	printf '{}\n' >"$HOME/.claude.json"
	exit 0
fi
if [[ "${1:-} ${2:-} ${3:-}" == "mcp add chrome-devtools" && "${CLAUDE_FAIL_ADD:-0}" == "1" ]]; then
	exit 1
fi
exit 0
EOF_CLAUDE
		cat >"$tmp/bin/npx" <<'EOF_NPX'
#!/usr/bin/env bash
exit 0
EOF_NPX
		cat >"$tmp/bin/node" <<'EOF_NODE'
#!/usr/bin/env bash
if [[ "${1:-}" == "-p" ]]; then
	version="${NODE_VERSION:-22.12.0}"
	printf '%s\n' "${version%%.*}"
	exit 0
fi
printf 'v%s\n' "${NODE_VERSION:-22.12.0}"
EOF_NODE
		chmod +x "$tmp/bin/claude" "$tmp/bin/npx" "$tmp/bin/node"
		for tool in brew uv pipx; do
			printf '#!/bin/sh\nexit 0\n' >"$tmp/bin/$tool"
			chmod +x "$tmp/bin/$tool"
		done

		log="$tmp/current.log"
		cat >"$tmp/home/.claude.json" <<'EOF_CURRENT'
{"mcpServers":{"chrome-devtools":{"type":"stdio","command":"npx","args":["-y","chrome-devtools-mcp@latest","--headless=true"]}}}
EOF_CURRENT
		if ! CLAUDE_CALLS="$tmp/calls" CLAUDE_PLUGIN_STATE=installed HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/$platform/part1.sh" \
			--repo "$tmp/repo" --dry-run --force --no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 >"$log" 2>&1; then
			cat "$log" >&2
			return 1
		fi
		if ! grep -Fq "Chrome DevTools MCP is already registered with @latest" "$log" ||
			grep -Fq "mcp add chrome-devtools" "$tmp/calls" 2>/dev/null; then
			echo "Claude $platform reconfigured a current Chrome DevTools MCP registration" >&2
			cat "$log" >&2
			return 1
		fi

		: >"$tmp/calls"
		rm -f "$tmp/home/.claude.json"
		if ! CLAUDE_CALLS="$tmp/calls" HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/$platform/part1.sh" \
			--repo "$tmp/repo" --dry-run --no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 >"$tmp/missing.log" 2>&1; then
			cat "$tmp/missing.log" >&2
			return 1
		fi
		if ! grep -Fq "Would run: claude mcp add chrome-devtools --scope user -- npx chrome-devtools-mcp@latest" "$tmp/missing.log"; then
			echo "Claude $platform did not register a missing Chrome DevTools MCP" >&2
			cat "$tmp/missing.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! CLAUDE_CALLS="$tmp/calls" CLAUDE_PLUGIN_STATE=installed HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/$platform/part1.sh" \
			--repo "$tmp/repo" --dry-run --force --no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 >"$tmp/plugin.log" 2>&1; then
			cat "$tmp/plugin.log" >&2
			return 1
		fi
		if ! grep -Fq "already installed as an enabled Claude plugin" "$tmp/plugin.log" ||
			grep -Fq "mcp add chrome-devtools" "$tmp/plugin.log"; then
			echo "Claude $platform duplicated the official Chrome DevTools MCP plugin" >&2
			cat "$tmp/plugin.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! CLAUDE_CALLS="$tmp/calls" NODE_VERSION=18.20.0 HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/$platform/part1.sh" \
			--repo "$tmp/repo" --dry-run --force --no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 >"$tmp/old-node.log" 2>&1; then
			cat "$tmp/old-node.log" >&2
			return 1
		fi
		if ! grep -Fq "requires a package-supported Node.js version" "$tmp/old-node.log" ||
			grep -Fq "mcp add chrome-devtools" "$tmp/old-node.log"; then
			echo "Claude $platform registered Chrome DevTools MCP with unsupported Node.js" >&2
			cat "$tmp/old-node.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		cat >"$tmp/home/.claude.json" <<'EOF_STALE'
{"mcpServers":{"chrome-devtools":{"type":"stdio","command":"npx","args":["chrome-devtools-mcp@0.1.0","--headless=true"]}}}
EOF_STALE
		if ! CLAUDE_CALLS="$tmp/calls" HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/$platform/part1.sh" \
			--repo "$tmp/repo" --dry-run --no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 >"$tmp/stale.log" 2>&1; then
			cat "$tmp/stale.log" >&2
			return 1
		fi
		if grep -Fq "mcp remove chrome-devtools" "$tmp/calls" || ! grep -Fq "rerun with --force or --chrome-mcp" "$tmp/stale.log"; then
			echo "Claude $platform did not preserve stale Chrome MCP settings during a normal rerun" >&2
			cat "$tmp/stale.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! CLAUDE_CALLS="$tmp/calls" CLAUDE_PLUGIN_STATE=installed HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/$platform/part1.sh" \
			--repo "$tmp/repo" --dry-run --force --no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 >"$tmp/update.log" 2>&1; then
			cat "$tmp/update.log" >&2
			return 1
		fi
		if ! grep -Fq "Would run: claude mcp remove chrome-devtools --scope user" "$tmp/update.log" ||
			! grep -Fq "Would run: claude mcp add chrome-devtools --scope user -- npx chrome-devtools-mcp@latest" "$tmp/update.log"; then
			echo "Claude $platform did not update a stale Chrome DevTools MCP registration" >&2
			cat "$tmp/update.log" >&2
			return 1
		fi

		cp "$tmp/home/.claude.json" "$tmp/expected-claude.json"
		: >"$tmp/calls"
		if CLAUDE_CALLS="$tmp/calls" CLAUDE_FAIL_ADD=1 HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/$platform/part1.sh" \
			--repo "$tmp/repo" --force --no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 >"$tmp/rollback.log" 2>&1; then
			cat "$tmp/rollback.log" >&2
			return 1
		fi
		if ! cmp -s "$tmp/expected-claude.json" "$tmp/home/.claude.json" ||
			! grep -Fq "restored the previous user configuration" "$tmp/rollback.log"; then
			echo "Claude $platform did not restore Chrome DevTools MCP settings after a failed update" >&2
			cat "$tmp/rollback.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! CLAUDE_CALLS="$tmp/calls" HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/$platform/part1.sh" \
			--repo "$tmp/repo" --dry-run --force --no-chrome-mcp --no-system-packages --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 >"$tmp/disabled.log" 2>&1; then
			cat "$tmp/disabled.log" >&2
			return 1
		fi
		if grep -Fq "mcp add chrome-devtools" "$tmp/disabled.log" || grep -Fq "mcp remove chrome-devtools" "$tmp/disabled.log"; then
			echo "Claude $platform ignored --no-chrome-mcp" >&2
			cat "$tmp/disabled.log" >&2
			return 1
		fi
		rm -rf "$tmp"
	done
}

run_default_wiring_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q

	if ! bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if [[ ! -f "$tmp/vibe_scripts/claude/Makefile" ]]; then
		echo "default Claude install did not create Makefile" >&2
		return 1
	fi
	assert_target "$tmp/vibe_scripts/claude/Makefile" edited-ai
	assert_target "$tmp/vibe_scripts/claude/Makefile" wiki-ai
	assert_target "$tmp/vibe_scripts/claude/Makefile" skills-check
	assert_target "$tmp/vibe_scripts/claude/Makefile" skills-update
	assert_target "$tmp/vibe_scripts/claude/Makefile" agent-verify
	assert_gitignore_entries "$tmp/.gitignore"
	assert_reviewer_rule "$tmp/CLAUDE.md"
	assert_reviewer_rule "$ROOT/ClaudeHelper/MacOS/part1.sh"
	assert_reviewer_rule "$ROOT/ClaudeHelper/WSL/part1.sh"

	if ! bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--force \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 >"$tmp/update.log" 2>&1; then
		cat "$tmp/update.log" >&2
		return 1
	fi
	if compgen -G "$tmp/.claude/settings.local.json.bak*" >/dev/null; then
		echo "unchanged Claude hook settings were backed up and rewritten" >&2
		return 1
	fi
}

run_hook_error_logging_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q

	if ! bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 \
		--no-wire >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	printf '{bad json' | (
		cd "$tmp"
		CLAUDE_PROJECT_DIR="$tmp" python3 .claude/hooks/pre_tool_use_policy.py
	)

	if ! grep -Fq '"reason": "json-decode-failed"' "$tmp/.cache/hook-errors.log"; then
		echo "Claude hook did not log malformed JSON failure" >&2
		cat "$tmp/.cache/hook-errors.log" >&2
		return 1
	fi
	if ! grep -Fq '"colno": 2' "$tmp/.cache/hook-errors.log" || ! grep -Fq '"error":' "$tmp/.cache/hook-errors.log"; then
		echo "Claude hook log did not include error details and location" >&2
		cat "$tmp/.cache/hook-errors.log" >&2
		return 1
	fi
}

run_no_wire_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q

	if ! bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 \
		--no-wire >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if [[ -f "$tmp/vibe_scripts/claude/Makefile" ]]; then
		echo "--no-wire Claude install unexpectedly created Makefile" >&2
		return 1
	fi
}

run_cleanup_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q

	if ! bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if ! bash "$ROOT/ClaudeHelper/install.sh" --repo "$tmp" --cleanup --yes >"$tmp/cleanup.log" 2>&1; then
		cat "$tmp/cleanup.log" >&2
		return 1
	fi

	local path
	for path in CLAUDE.md agent/claude vibe_scripts/claude .claude; do
		if [[ -e "$tmp/$path" || -L "$tmp/$path" ]]; then
			echo "Claude cleanup left $path behind" >&2
			find "$tmp" -maxdepth 2 -print >&2
			return 1
		fi
	done
	for path in codebase-wiki notes skills-lock.json .gitignore; do
		if [[ ! -e "$tmp/$path" ]]; then
			echo "Cleanup removed shared project data: $path" >&2
			return 1
		fi
	done
}

run_lint_ignores_agent_roots_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	mkdir -p "$tmp/bin"
	cat >"$tmp/bin/shellcheck" <<'EOF_SHELLCHECK'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
	printf 'ShellCheck mock\n'
fi
EOF_SHELLCHECK
	chmod +x "$tmp/bin/shellcheck"

	if ! PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	python3 "$ROOT/tests/assert_quality_selection.py" "$tmp" claude shell

	mkdir -p "$tmp/.cache" "$tmp/src" "$tmp/skills/example" "$tmp/tools/ai-marketing-skills/eval"
	printf 'const app = true;\n' >"$tmp/src/app.ts"
	printf 'const untouched = true;\n' >"$tmp/untouched.ts"
	git -C "$tmp" add untouched.ts
	git -C "$tmp" -c user.email=test@example.invalid -c user.name=VibeHelper commit -q -m 'unchanged fixture'
	printf 'const skill = true;\n' >"$tmp/skills/example/skill.ts"
	printf 'const evalFile = true;\n' >"$tmp/tools/ai-marketing-skills/eval/run-eval.ts"
	printf '%s\n' \
		'src/app.ts' \
		'skills/example/skill.ts' \
		'tools/ai-marketing-skills/eval/run-eval.ts' \
		>"$tmp/.cache/claude-edited-files.txt"
	cat >"$tmp/package.json" <<'EOF_PACKAGE'
{"scripts":{"typecheck":"echo whole-project-typecheck-ran >&2; exit 99"}}
EOF_PACKAGE
	if edited_output="$(cd "$tmp" && python3 vibe_scripts/claude/agent-check-edited.py --json 2>&1)"; then
		echo "Claude edited-file checker falsely passed without the required Biome tool" >&2
		printf '%s\n' "$edited_output" >&2
		return 1
	fi
	if ! grep -Fq '"src/app.ts"' <<<"$edited_output" || ! grep -Fq '"status": "not_checked"' <<<"$edited_output"; then
		echo "Claude edited-file checker skipped recorded application code" >&2
		printf '%s\n' "$edited_output" >&2
		return 1
	fi
	for excluded in "skills/example/skill.ts" "tools/ai-marketing-skills/eval/run-eval.ts" "untouched.ts" "whole-project-typecheck-ran"; do
		if grep -Fq "$excluded" <<<"$edited_output"; then
			echo "Claude edited-file checker included excluded or untouched code: $excluded" >&2
			printf '%s\n' "$edited_output" >&2
			return 1
		fi
	done
}

run_markdown_lint_wiring_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	mkdir -p "$tmp/bin"
	printf '# Test\n' >"$tmp/README.md"
	cat >"$tmp/bin/markdownlint-cli2" <<'EOF_MARKDOWNLINT'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
	printf 'markdownlint-cli2 mock\n'
fi
EOF_MARKDOWNLINT
	chmod +x "$tmp/bin/markdownlint-cli2"

	if ! PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if ! grep -Fq "markdownlint-cli2" "$tmp/vibe_scripts/claude/Makefile"; then
		echo "generated Claude Makefile did not wire markdownlint-cli2" >&2
		cat "$tmp/vibe_scripts/claude/Makefile" >&2
		return 1
	fi
	python3 "$ROOT/tests/assert_quality_selection.py" "$tmp" claude markdown
	if ! grep -Fq "MARKDOWN_EXTS" "$tmp/vibe_scripts/claude/agent-check-edited.py"; then
		echo "generated Claude edited-file checker does not include Markdown files" >&2
		cat "$tmp/vibe_scripts/claude/agent-check-edited.py" >&2
		return 1
	fi
}

run_impeccable_claude_install_ignores_codex_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	mkdir -p "$tmp/.agents/skills/impeccable" "$tmp/bin"
	printf '# Codex Impeccable\n' >"$tmp/.agents/skills/impeccable/SKILL.md"
	cat >"$tmp/bin/npx" <<'EOF_NPX'
#!/usr/bin/env bash
printf '%s\n' "$*" >>npx.log
if [[ "$1 $2 $3 $4" == "impeccable skills install -y" ]]; then
	mkdir -p .claude/skills/impeccable
	printf '# Claude Impeccable\n' >.claude/skills/impeccable/SKILL.md
fi
EOF_NPX
	chmod +x "$tmp/bin/npx"

	if ! PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--impeccable \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 \
		--no-wire >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if [[ ! -f "$tmp/.claude/skills/impeccable/SKILL.md" ]]; then
		echo "Claude Impeccable install was skipped because Codex Impeccable existed" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi
	if ! grep -q -- '--providers=claude-code' "$tmp/npx.log"; then
		echo "Impeccable install did not target Claude Code" >&2
		cat "$tmp/npx.log" >&2
		return 1
	fi
}

run_wsl_delegation_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q

	if grep -Rq -- "../MacOS/part" "$ROOT/ClaudeHelper/WSL"; then
		echo "ClaudeHelper WSL scripts must not delegate to MacOS scripts" >&2
		grep -Rn -- "../MacOS/part" "$ROOT/ClaudeHelper/WSL" >&2
		return 1
	fi
	if ! grep -Fq 'DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a' "$ROOT/ClaudeHelper/WSL/part1.sh"; then
		echo "WSL apt bootstrap must run noninteractively" >&2
		return 1
	fi
	if grep -Fq "apt-get install -y \"\${missing[@]}\"" "$ROOT/ClaudeHelper/WSL/part1.sh"; then
		echo "WSL apt bootstrap must not install all missing packages as one atomic transaction" >&2
		return 1
	fi
	if grep -Eq '^maybe_install_system_packages\(\)|^sudo_prefix\(\)' "$ROOT/ClaudeHelper/WSL/part1.sh"; then
		echo "WSL part1 contains duplicate dead apt helper functions" >&2
		return 1
	fi
	if ! grep -Fq 'Node.js >=18 is required' "$ROOT/ClaudeHelper/WSL/part1.sh"; then
		echo "WSL npx setup must warn clearly for old Node.js versions" >&2
		return 1
	fi

	if ! bash "$ROOT/ClaudeHelper/WSL/part1.sh" \
		--repo "$tmp" \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi
	if ! grep -q -- "Skipping apt package bootstrap" "$tmp/install.log"; then
		echo "WSL --repo-only did not skip apt package bootstrap" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi
	if grep -q -- "Would install missing Ubuntu packages via apt-get\\|Installing missing Ubuntu packages via apt-get" "$tmp/install.log"; then
		echo "WSL --repo-only attempted apt package bootstrap" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi

	if ! grep -q -- "WSL/part2.sh" "$tmp/install.log"; then
		echo "WSL part1 next-steps message did not point back to WSL/part2.sh" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi
	if grep -q -- "MacOS/part2.sh" "$tmp/install.log"; then
		echo "WSL part1 next-steps message wrongly pointed at MacOS/part2.sh" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi

	if ! bash "$ROOT/ClaudeHelper/WSL/part2.sh" \
		--repo "$tmp" \
		--wire >>"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi
	assert_target "$tmp/vibe_scripts/claude/Makefile" edited-ai
}

run_lint_shell_empty_input_test() {
	if ! command -v shellcheck >/dev/null 2>&1; then
		echo "shellcheck not on PATH; skipping lint-shell empty-input test" >&2
		return 0
	fi

	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q

	if ! bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	python3 "$ROOT/tests/assert_quality_selection.py" "$tmp" claude empty-shell

	# No .sh files exist in this fresh repo. Without -r, GNU xargs on Linux runs
	# the linter with zero file args and fails; BSD xargs on macOS masks the bug.
	if ! (cd "$tmp" && make -f vibe_scripts/claude/Makefile lint-ai >"$tmp/lint.log" 2>&1); then
		echo "make -f vibe_scripts/claude/Makefile lint-ai failed against a repo with zero .sh files" >&2
		cat "$tmp/lint.log" >&2
		return 1
	fi
}

run_fresh_install_test() {
	local tmp script
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	printf '# Existing Claude instructions\n' >"$tmp/CLAUDE.md"
	mkdir -p "$tmp/vibe_scripts/claude"
	printf '#!/usr/bin/env python3\n' >"$tmp/vibe_scripts/claude/agent-check-edited.py"

	if ! bash "$ROOT/ClaudeHelper/install.sh" \
		--repo "$tmp" \
		--dry-run \
		--fresh-install \
		--repo-only \
		--no-humanizer --no-omniroute \
		--no-rtk \
		--no-graphify \
		--no-ponytail \
		--no-crg \
		--no-context7 >"$tmp/unified.log" 2>&1; then
		cat "$tmp/unified.log" >&2
		return 1
	fi
	if ! grep -Fq "Would back up CLAUDE.md" "$tmp/unified.log"; then
		echo "Claude --fresh-install did not enable managed-file refresh" >&2
		cat "$tmp/unified.log" >&2
		return 1
	fi

	for script in "$ROOT/ClaudeHelper/MacOS/part1.sh" "$ROOT/ClaudeHelper/WSL/part1.sh"; do
		if ! bash "$script" \
			--repo "$tmp" \
			--dry-run \
			--fresh-install \
			--repo-only \
			--no-humanizer --no-omniroute \
			--no-rtk \
			--no-graphify \
			--no-ponytail \
			--no-crg \
			--no-context7 >"$tmp/part1.log" 2>&1; then
			cat "$tmp/part1.log" >&2
			return 1
		fi
	done

	for script in "$ROOT/ClaudeHelper/MacOS/part2.sh" "$ROOT/ClaudeHelper/WSL/part2.sh"; do
		rm -f "$tmp/vibe_scripts/claude/Makefile"
		if ! bash "$script" --repo "$tmp" --fresh-install >"$tmp/part2.log" 2>&1; then
			cat "$tmp/part2.log" >&2
			return 1
		fi
		assert_target "$tmp/vibe_scripts/claude/Makefile" edited-ai
		if ! grep -Fq "ClaudeHelper-Version: 2026-10-12-v7" "$tmp/vibe_scripts/claude/Makefile"; then
			echo "Claude part2 --fresh-install did not write the current managed block" >&2
			cat "$tmp/vibe_scripts/claude/Makefile" >&2
			return 1
		fi
	done
}

run_force_refresh_planning_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	mkdir -p "$tmp/bin" "$tmp/home/.claude/skills/graphify" "$tmp/repo/.claude/skills/humanizer" "$tmp/repo/.claude/skills/impeccable"
	git -C "$tmp/repo" init -q
	printf '<!-- ClaudeHelper-Version: previous -->\n' >"$tmp/repo/CLAUDE.md"
	printf '# Graphify\n' >"$tmp/home/.claude/skills/graphify/SKILL.md"
	printf '# Humanizer\n' >"$tmp/repo/.claude/skills/humanizer/SKILL.md"
	printf '# Impeccable\n' >"$tmp/repo/.claude/skills/impeccable/SKILL.md"
	printf '1.0.0\n' >"$tmp/crg-version"

	cat >"$tmp/bin/brew" <<'EOF_BREW'
#!/usr/bin/env bash
if [[ "${1:-}" == "list" && "${2:-}" == "--formula" ]]; then exit 0; fi
exit 0
EOF_BREW
	cat >"$tmp/bin/uv" <<'EOF_UV'
#!/usr/bin/env bash
if [[ "${1:-} ${2:-}" == "tool list" ]]; then printf 'code-review-graph v1.0.0\ngraphifyy v1.0.0\n'; fi
if [[ "${1:-} ${2:-} ${3:-}" == "tool upgrade code-review-graph" && -n "${CRG_NEXT_VERSION:-}" ]]; then
	printf '%s\n' "$CRG_NEXT_VERSION" >"$CRG_VERSION_FILE"
fi
exit 0
EOF_UV
	cat >"$tmp/bin/pipx" <<'EOF_PIPX'
#!/usr/bin/env bash
exit 0
EOF_PIPX
	cat >"$tmp/bin/rtk" <<'EOF_RTK'
#!/usr/bin/env bash
printf 'CLI proxy for token-optimized LLM context\n'
EOF_RTK
	cat >"$tmp/bin/claude" <<'EOF_CLAUDE'
#!/usr/bin/env bash
if [[ "${1:-} ${2:-}" == "plugin list" ]]; then printf 'ponytail@ponytail\n'; fi
exit 0
EOF_CLAUDE
	cat >"$tmp/bin/code-review-graph" <<'EOF_CRG'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
	if [[ -n "${CRG_VERSION_FILE:-}" ]]; then
		printf 'code-review-graph %s\n' "$(<"$CRG_VERSION_FILE")"
	else
		printf 'code-review-graph 1.0.0\n'
	fi
else
	printf '%s\n' "$*" >>"${CRG_CALLS:?}"
fi
exit 0
EOF_CRG
	cat >"$tmp/bin/graphify" <<'EOF_GRAPHIFY'
#!/usr/bin/env bash
exit 0
EOF_GRAPHIFY
	cat >"$tmp/bin/npx" <<'EOF_NPX'
#!/usr/bin/env bash
exit 0
EOF_NPX
	cat >"$tmp/bin/node" <<'EOF_NODE'
#!/usr/bin/env bash
printf '20\n'
EOF_NODE
	cat >"$tmp/bin/dpkg" <<'EOF_DPKG'
#!/usr/bin/env bash
exit 0
EOF_DPKG
	cat >"$tmp/bin/apt-get" <<'EOF_APT'
#!/usr/bin/env bash
exit 0
EOF_APT
	cat >"$tmp/bin/sudo" <<'EOF_SUDO'
#!/usr/bin/env bash
"$@"
EOF_SUDO
	chmod +x "$tmp/bin"/*

	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/MacOS/part1.sh" --repo "$tmp/repo" --dry-run --force >"$tmp/mac.log" 2>&1; then
		cat "$tmp/mac.log" >&2
		return 1
	fi
	for expected in "brew upgrade rtk" "brew upgrade uv" "brew upgrade pipx" "uv tool upgrade graphifyy" "uv tool upgrade code-review-graph" "skills add blader/humanizer" "skills add Leonxlnx/unlazy --agent claude-code" "claude plugin marketplace update ponytail" "claude plugin update ponytail@ponytail" "impeccable skills install"; do
		if ! grep -Fq "$expected" "$tmp/mac.log"; then
			echo "Claude macOS --force did not plan: $expected" >&2
			cat "$tmp/mac.log" >&2
			return 1
		fi
	done
	if grep -Fq "Would register it: code-review-graph install" "$tmp/mac.log"; then
		echo "Claude --force reconfigured unchanged code-review-graph" >&2
		cat "$tmp/mac.log" >&2
		return 1
	fi
	if ! grep -Fq "only if its version changes" "$tmp/mac.log"; then
		echo "Claude --force did not make code-review-graph setup conditional" >&2
		cat "$tmp/mac.log" >&2
		return 1
	fi
	if ! grep -Fq "Skipping unchanged Context7 MCP setup during update" "$tmp/mac.log"; then
		echo "Claude --force repeated unchanged Context7 setup" >&2
		cat "$tmp/mac.log" >&2
		return 1
	fi
	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/MacOS/part1.sh" --repo "$tmp/repo" --dry-run --fresh-install --no-context7 >"$tmp/mac-fresh.log" 2>&1; then
		cat "$tmp/mac-fresh.log" >&2
		return 1
	fi
	if ! grep -Fq "impeccable skills install -y --providers=claude-code --scope=project --force" "$tmp/mac-fresh.log"; then
		echo "Claude macOS --fresh-install did not force-refresh Impeccable for Claude Code" >&2
		cat "$tmp/mac-fresh.log" >&2
		return 1
	fi
	if ! grep -Fq "code-review-graph install --platform claude-code --no-hooks --no-instructions -y" "$tmp/mac-fresh.log"; then
		echo "Claude --fresh-install did not configure code-review-graph without replacing CLAUDE.md" >&2
		cat "$tmp/mac-fresh.log" >&2
		return 1
	fi
	: >"$tmp/crg-calls"
	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" CRG_VERSION_FILE="$tmp/crg-version" CRG_CALLS="$tmp/crg-calls" bash "$ROOT/ClaudeHelper/MacOS/part1.sh" --repo "$tmp/repo" --force --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-context7 >"$tmp/mac-unchanged.log" 2>&1; then
		cat "$tmp/mac-unchanged.log" >&2
		return 1
	fi
	if grep -Fq "install --platform claude-code" "$tmp/crg-calls"; then
		echo "unchanged code-review-graph ran real Claude MCP setup" >&2
		cat "$tmp/crg-calls" >&2
		return 1
	fi
	: >"$tmp/crg-calls"
	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" CRG_VERSION_FILE="$tmp/crg-version" CRG_CALLS="$tmp/crg-calls" CRG_NEXT_VERSION="2.0.0" bash "$ROOT/ClaudeHelper/MacOS/part1.sh" --repo "$tmp/repo" --force --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-context7 >"$tmp/mac-changed.log" 2>&1; then
		cat "$tmp/mac-changed.log" >&2
		return 1
	fi
	if ! grep -Fq "install --platform claude-code --no-hooks --no-instructions -y" "$tmp/crg-calls"; then
		echo "updated code-review-graph did not run Claude MCP setup" >&2
		cat "$tmp/crg-calls" >&2
		return 1
	fi
	rm -rf "$tmp/repo/.claude/skills/impeccable"
	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/install.sh" --repo "$tmp/repo" --dry-run --fresh-install --no-context7 >"$tmp/unified-fresh.log" 2>&1; then
		cat "$tmp/unified-fresh.log" >&2
		return 1
	fi
	if ! grep -Fq "impeccable skills install -y --providers=claude-code --scope=project --force" "$tmp/unified-fresh.log"; then
		echo "Claude unified --fresh-install did not force-install Impeccable for Claude Code" >&2
		cat "$tmp/unified-fresh.log" >&2
		return 1
	fi

	cat >"$tmp/bin/brew" <<'EOF_BREW_UNMANAGED'
#!/usr/bin/env bash
if [[ "${1:-}" == "list" && "${2:-}" == "--formula" ]]; then exit 1; fi
exit 0
EOF_BREW_UNMANAGED
	chmod +x "$tmp/bin/brew"
	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/MacOS/part1.sh" --repo "$tmp/repo" --dry-run --force --no-context7 >"$tmp/mac-unmanaged.log" 2>&1; then
		cat "$tmp/mac-unmanaged.log" >&2
		return 1
	fi
	if ! grep -Fq "RTK is installed but is not owned by Homebrew; leaving it unchanged." "$tmp/mac-unmanaged.log"; then
		echo "Claude macOS --force did not preserve unmanaged RTK" >&2
		cat "$tmp/mac-unmanaged.log" >&2
		return 1
	fi
	if grep -Fq "official RTK installer" "$tmp/mac-unmanaged.log"; then
		echo "Claude macOS --force still planned the official RTK installer" >&2
		cat "$tmp/mac-unmanaged.log" >&2
		return 1
	fi

	rm -f "$tmp/bin/rtk"
	if ! HOME="$tmp/home" PATH="$tmp/bin:/usr/bin:/bin" bash "$ROOT/ClaudeHelper/MacOS/part1.sh" --repo "$tmp/repo" --dry-run --no-context7 >"$tmp/mac-missing.log" 2>&1; then
		cat "$tmp/mac-missing.log" >&2
		return 1
	fi
	if ! grep -Fq "brew install rtk" "$tmp/mac-missing.log"; then
		echo "Claude macOS did not plan RTK installation with Homebrew" >&2
		cat "$tmp/mac-missing.log" >&2
		return 1
	fi

	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/WSL/part1.sh" --repo "$tmp/repo" --dry-run --force >"$tmp/wsl.log" 2>&1; then
		cat "$tmp/wsl.log" >&2
		return 1
	fi
	for expected in "Would install or refresh Ubuntu packages via apt-get" "official RTK installer" "uv self update" "uv tool upgrade graphifyy" "uv tool upgrade code-review-graph" "skills add Leonxlnx/unlazy --agent claude-code" "claude plugin update ponytail@ponytail"; do
		if ! grep -Fq "$expected" "$tmp/wsl.log"; then
			echo "Claude WSL --force did not plan: $expected" >&2
			cat "$tmp/wsl.log" >&2
			return 1
		fi
	done
	if grep -Fq "Would register it: code-review-graph install" "$tmp/wsl.log" || ! grep -Fq "only if its version changes" "$tmp/wsl.log"; then
		echo "Claude WSL --force did not keep code-review-graph setup conditional" >&2
		cat "$tmp/wsl.log" >&2
		return 1
	fi
	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/ClaudeHelper/WSL/part1.sh" --repo "$tmp/repo" --dry-run --fresh-install --no-context7 >"$tmp/wsl-fresh.log" 2>&1; then
		cat "$tmp/wsl-fresh.log" >&2
		return 1
	fi
	if ! grep -Fq "impeccable skills install -y --providers=claude-code --scope=project --force" "$tmp/wsl-fresh.log"; then
		echo "Claude WSL --fresh-install did not force-install Impeccable for Claude Code" >&2
		cat "$tmp/wsl-fresh.log" >&2
		return 1
	fi
}

run_force_with_codex_coexistence_test() {
	local tmp user_repo
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	printf '<!-- CodexHelper-Version: test -->\n' >"$tmp/AGENTS.md"
	printf '<!-- ClaudeHelper-Version: old -->\n' >"$tmp/CLAUDE.md"
	mkdir -p "$tmp/agent"
	printf '<!-- CodexHelper-Version: test -->\n' >"$tmp/agent/index.md"
	if ! bash "$ROOT/ClaudeHelper/install.sh" --repo "$tmp" --force --repo-only --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 --no-wire >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi
	if ! grep -Fq -- "--force will refresh tools while preserving shared managed files" "$tmp/install.log"; then
		echo "Claude --force did not enter Codex-safe tool refresh mode" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi
	if ! grep -Fq "$REVIEWER_RULE" "$tmp/CLAUDE.md"; then
		echo "Claude --force did not update CLAUDE.md during Codex coexistence" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi
	if ! grep -Fq "CodexHelper-Version: test" "$tmp/agent/index.md"; then
		echo "Claude --force overwrote shared workflow files during Codex coexistence" >&2
		cat "$tmp/agent/index.md" >&2
		return 1
	fi

	user_repo="$tmp/user-owned"
	mkdir -p "$user_repo"
	git -C "$user_repo" init -q
	printf '<!-- CodexHelper-Version: test -->\n' >"$user_repo/AGENTS.md"
	printf 'user-owned Claude instructions\n' >"$user_repo/CLAUDE.md"
	if ! bash "$ROOT/ClaudeHelper/install.sh" --repo "$user_repo" --force --repo-only --no-humanizer --no-omniroute --no-rtk --no-graphify --no-ponytail --no-crg --no-context7 --no-wire >"$user_repo/install.log" 2>&1; then
		cat "$user_repo/install.log" >&2
		return 1
	fi
	if [[ "$(<"$user_repo/CLAUDE.md")" != "user-owned Claude instructions" ]]; then
		echo "Claude --force replaced a user-owned CLAUDE.md during Codex coexistence" >&2
		cat "$user_repo/CLAUDE.md" >&2
		return 1
	fi
}

printf 'Running %s\n' run_template_packaging_test
run_template_packaging_test
printf 'Running %s\n' run_chrome_mcp_test
run_chrome_mcp_test
printf 'Running %s\n' run_default_wiring_test
run_default_wiring_test
printf 'Running %s\n' run_hook_error_logging_test
run_hook_error_logging_test
printf 'Running %s\n' run_no_wire_test
run_no_wire_test
printf 'Running %s\n' run_cleanup_test
run_cleanup_test
printf 'Running %s\n' run_lint_ignores_agent_roots_test
run_lint_ignores_agent_roots_test
printf 'Running %s\n' run_markdown_lint_wiring_test
run_markdown_lint_wiring_test
printf 'Running %s\n' run_impeccable_claude_install_ignores_codex_test
run_impeccable_claude_install_ignores_codex_test
printf 'Running %s\n' run_wsl_delegation_test
run_wsl_delegation_test
printf 'Running %s\n' run_lint_shell_empty_input_test
run_lint_shell_empty_input_test
printf 'Running %s\n' run_fresh_install_test
run_fresh_install_test
printf 'Running %s\n' run_force_refresh_planning_test
run_force_refresh_planning_test
printf 'Running %s\n' run_force_with_codex_coexistence_test
run_force_with_codex_coexistence_test
echo "ClaudeHelper install smoke tests passed."
