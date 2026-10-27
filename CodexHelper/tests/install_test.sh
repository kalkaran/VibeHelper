#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd)"
TEST_BIN="$(mktemp -d)"
trap 'rm -rf "$TEST_BIN"' EXIT
SYSTEM_NODE="$(command -v node)"
export SYSTEM_NODE
printf '#!/usr/bin/env bash\nexit 0\n' >"$TEST_BIN/npx"
chmod +x "$TEST_BIN/npx"
PATH="$TEST_BIN:$PATH"
export PATH

REVIEWER_RULE="Before any coding task makes its first repository edit, including edits to tests, configuration, hooks, wrappers, or Make targets, spawn one separate read-only sentinel agent."

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
	local count
	count="$(grep -Ec "^${target}:" "$makefile" || true)"
	if [[ "$count" -ne 1 ]]; then
		echo "expected exactly one Makefile target named $target, found $count" >&2
		return 1
	fi
}

assert_no_target() {
	local makefile="$1"
	local target="$2"
	if grep -Eq "^${target}:" "$makefile"; then
		echo "unexpected Makefile target named $target" >&2
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

run_default_wiring_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	git -C "$tmp" config user.email "test@example.invalid"
	git -C "$tmp" config user.name "VibeHelper Test"
	touch "$tmp/.gitkeep"
	git -C "$tmp" add .gitkeep
	git -C "$tmp" commit -q -m "init"

	if ! bash "$ROOT/CodexHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--yes \
		--no-install \
		--no-fix \
		--no-humanizer --no-omniroute \
		--no-codex-hooks \
		--no-rtk-hook \
		--no-crg-build \
		--no-apply-codex-config >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if [[ ! -f "$tmp/vibe_scripts/codex/Makefile" ]]; then
		echo "default Codex install did not create Makefile" >&2
		return 1
	fi
	assert_target "$tmp/vibe_scripts/codex/Makefile" edited-ai
	assert_target "$tmp/vibe_scripts/codex/Makefile" wiki-ai
	assert_target "$tmp/vibe_scripts/codex/Makefile" skills-check
	assert_target "$tmp/vibe_scripts/codex/Makefile" skills-update
	assert_target "$tmp/vibe_scripts/codex/Makefile" agent-verify
	assert_no_target "$tmp/vibe_scripts/codex/Makefile" format-ai
	if [[ "$(grep -Fc 'Checking local prerequisites' "$tmp/install.log")" -ne 1 ]]; then
		echo "default Codex install did not report prerequisites exactly once" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi
	assert_gitignore_entries "$tmp/.gitignore"
	assert_reviewer_rule "$tmp/AGENTS.md"
	assert_reviewer_rule "$ROOT/CodexHelper/MacOS/part1.sh"
	assert_reviewer_rule "$ROOT/CodexHelper/WSL/part1.sh"
}

run_context7_oauth_planning_test() {
	local platform tmp
	for platform in MacOS WSL; do
		tmp="$(mktemp -d)"
		mkdir -p "$tmp/bin" "$tmp/home" "$tmp/repo"
		git -C "$tmp/repo" init -q
		printf '#!/usr/bin/env bash\nexit 0\n' >"$tmp/bin/codex"
		chmod +x "$tmp/bin/codex"

		if ! (
			cd "$tmp/repo"
			HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" \
				--dry-run \
				--yes \
				--context7 \
				--no-humanizer --no-omniroute \
				--no-codex-hooks \
				--no-rtk-hook \
				--no-crg-build \
				--no-apply-codex-config
		) >"$tmp/install.log" 2>&1; then
			cat "$tmp/install.log" >&2
			return 1
		fi
		if ! grep -Eq '\$ codex mcp login context7' "$tmp/install.log"; then
			echo "Codex $platform Context7 setup did not start OAuth login" >&2
			cat "$tmp/install.log" >&2
			return 1
		fi
		if ! grep -Eq '\$ npx ctx7 setup --codex --mcp --oauth -y' "$tmp/install.log"; then
			echo "Codex $platform Context7 setup did not select hosted OAuth" >&2
			cat "$tmp/install.log" >&2
			return 1
		fi
		rm -rf "$tmp"
	done
}

run_chrome_mcp_test() {
	local platform tmp
	for platform in MacOS WSL; do
		tmp="$(mktemp -d)"
		mkdir -p "$tmp/bin" "$tmp/home" "$tmp/repo/.agents/skills/unlazy" "$tmp/repo/.code-review-graph"
		git -C "$tmp/repo" init -q
		printf '# Unlazy\n' >"$tmp/repo/.agents/skills/unlazy/SKILL.md"
		printf 'graph\n' >"$tmp/repo/.code-review-graph/graph.db"
		cat >"$tmp/bin/codex" <<'EOF_CODEX'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CODEX_CALLS"
if [[ "${1:-} ${2:-} ${3:-}" == "mcp get chrome-devtools" ]]; then
	case "${CHROME_STATE:-missing}" in
	current)
		printf '%s\n' '{"name":"chrome-devtools","enabled":true,"transport":{"type":"stdio","command":"npx","args":["chrome-devtools-mcp@latest"]}}'
		;;
	custom)
		printf '%s\n' '{"name":"chrome-devtools","enabled":true,"transport":{"type":"stdio","command":"/opt/homebrew/bin/npx","args":["-y","chrome-devtools-mcp@latest","--browser-url=http://127.0.0.1:9222"]}}'
		;;
	stale)
		printf '%s\n' '{"name":"chrome-devtools","enabled":true,"transport":{"type":"stdio","command":"npx","args":["chrome-devtools-mcp@0.1.0","--headless=true"]}}'
		;;
	*)
		exit 1
		;;
	esac
	exit 0
fi
if [[ "${1:-} ${2:-} ${3:-}" == "mcp get context7" ]]; then
	exit 1
fi
if [[ "${1:-} ${2:-} ${3:-}" == "plugin list --json" ]]; then
	printf '[{"pluginId":"ponytail@ponytail"}]\n'
	exit 0
fi
if [[ "${1:-} ${2:-} ${3:-}" == "plugin marketplace list" ]]; then
	printf 'ponytail\n'
	exit 0
fi
exit 0
EOF_CODEX
		cat >"$tmp/bin/node" <<'EOF_NODE'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
	printf 'v%s\n' "${NODE_VERSION:-22.12.0}"
	exit 0
fi
exec "$SYSTEM_NODE" "$@"
EOF_NODE
		cat >"$tmp/bin/code-review-graph" <<'EOF_CRG'
#!/usr/bin/env bash
[[ "${1:-}" == "--version" ]] && printf 'code-review-graph 1.0.0\n'
exit 0
EOF_CRG
		chmod +x "$tmp/bin/codex" "$tmp/bin/code-review-graph" "$tmp/bin/node"

		: >"$tmp/calls"
		if ! (
			cd "$tmp/repo"
			CODEX_CALLS="$tmp/calls" CHROME_STATE=current HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --force --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$tmp/current.log" 2>&1; then
			cat "$tmp/current.log" >&2
			return 1
		fi
		if ! grep -Fq "Chrome DevTools MCP is already registered with @latest" "$tmp/current.log" ||
			grep -Fq "mcp add chrome-devtools" "$tmp/calls"; then
			echo "Codex $platform reconfigured a current Chrome DevTools MCP registration" >&2
			cat "$tmp/current.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! (
			cd "$tmp/repo"
			CODEX_CALLS="$tmp/calls" CHROME_STATE=custom HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --force --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$tmp/custom.log" 2>&1; then
			cat "$tmp/custom.log" >&2
			return 1
		fi
		if grep -Fq "mcp add chrome-devtools" "$tmp/calls"; then
			echo "Codex $platform erased current custom Chrome DevTools MCP launch arguments" >&2
			cat "$tmp/custom.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! (
			cd "$tmp/repo"
			CODEX_CALLS="$tmp/calls" CHROME_STATE=missing HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$tmp/missing.log" 2>&1; then
			cat "$tmp/missing.log" >&2
			return 1
		fi
		if ! grep -Fq '$ codex mcp add chrome-devtools -- npx chrome-devtools-mcp@latest' "$tmp/missing.log"; then
			echo "Codex $platform did not register a missing Chrome DevTools MCP" >&2
			cat "$tmp/missing.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! (
			cd "$tmp/repo"
			CODEX_CALLS="$tmp/calls" CHROME_STATE=stale HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --force --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$tmp/stale.log" 2>&1; then
			cat "$tmp/stale.log" >&2
			return 1
		fi
		if ! grep -Fq '$ codex mcp add chrome-devtools -- npx chrome-devtools-mcp@latest' "$tmp/stale.log"; then
			echo "Codex $platform did not update a stale Chrome DevTools MCP registration" >&2
			cat "$tmp/stale.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! (
			cd "$tmp/repo"
			CODEX_CALLS="$tmp/calls" CHROME_STATE=stale HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --force --yes --no-chrome-mcp --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$tmp/disabled.log" 2>&1; then
			cat "$tmp/disabled.log" >&2
			return 1
		fi
		if grep -Fq "mcp add chrome-devtools" "$tmp/disabled.log"; then
			echo "Codex $platform ignored --no-chrome-mcp" >&2
			cat "$tmp/disabled.log" >&2
			return 1
		fi

		: >"$tmp/calls"
		if ! (
			cd "$tmp/repo"
			CODEX_CALLS="$tmp/calls" CHROME_STATE=missing NODE_VERSION=18.20.0 HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --force --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$tmp/old-node.log" 2>&1; then
			cat "$tmp/old-node.log" >&2
			return 1
		fi
		if ! grep -Fq "requires a package-supported Node.js version" "$tmp/old-node.log" ||
			grep -Fq "mcp add chrome-devtools" "$tmp/old-node.log"; then
			echo "Codex $platform registered Chrome DevTools MCP with unsupported Node.js" >&2
			cat "$tmp/old-node.log" >&2
			return 1
		fi
		rm -rf "$tmp"
	done
}

run_force_repairs_legacy_makefile_test() {
	local platform tmp make_output
	for platform in MacOS WSL; do
		tmp="$(mktemp -d)"
		mkdir -p "$tmp/vibe_scripts/codex"
		git -C "$tmp" init -q
		cat >"$tmp/vibe_scripts/codex/Makefile" <<'EOF_MAKEFILE'
# CodexHelper-Version: legacy
.PHONY: edited-ai wiki-ai
edited-ai:
	@:

edited-ai:
	@:

wiki-ai:
	@:
EOF_MAKEFILE
		printf '#!/usr/bin/env python3\n' >"$tmp/vibe_scripts/codex/sync-skills.py"

		if ! CODEXHELPER_UNIFIED_INSTALL=1 PATH="$TEST_BIN:/usr/bin:/bin" bash "$ROOT/CodexHelper/$platform/part2.sh" \
			--repo "$tmp" \
			--force \
			--yes \
			--no-install \
			--wire \
			--no-fix >"$tmp/install.log" 2>&1; then
			cat "$tmp/install.log" >&2
			return 1
		fi
		assert_target "$tmp/vibe_scripts/codex/Makefile" edited-ai
		assert_target "$tmp/vibe_scripts/codex/Makefile" wiki-ai
		assert_target "$tmp/vibe_scripts/codex/Makefile" skills-check
		assert_target "$tmp/vibe_scripts/codex/Makefile" skills-update
		if ! make_output="$(make -C "$tmp" -f vibe_scripts/codex/Makefile -n skills-check 2>&1)"; then
			printf '%s\n' "$make_output" >&2
			return 1
		fi
		if grep -Eq "overriding recipe|ignoring old recipe" <<<"$make_output"; then
			echo "Codex $platform forced Makefile regeneration left duplicate recipes" >&2
			printf '%s\n' "$make_output" >&2
			return 1
		fi
		rm -rf "$tmp"
	done
}

run_no_wire_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	git -C "$tmp" config user.email "test@example.invalid"
	git -C "$tmp" config user.name "VibeHelper Test"
	touch "$tmp/.gitkeep"
	git -C "$tmp" add .gitkeep
	git -C "$tmp" commit -q -m "init"

	if ! bash "$ROOT/CodexHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--yes \
		--no-install \
		--no-fix \
		--no-humanizer --no-omniroute \
		--no-codex-hooks \
		--no-rtk-hook \
		--no-crg-build \
		--no-apply-codex-config \
		--no-wire >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if [[ -f "$tmp/vibe_scripts/codex/Makefile" ]]; then
		echo "--no-wire Codex install unexpectedly created Makefile" >&2
		return 1
	fi
}

run_cleanup_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	git -C "$tmp" config user.email "test@example.invalid"
	git -C "$tmp" config user.name "VibeHelper Test"
	touch "$tmp/.gitkeep"
	git -C "$tmp" add .gitkeep
	git -C "$tmp" commit -q -m "init"

	if ! bash "$ROOT/CodexHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--yes \
		--no-install \
		--no-fix \
		--no-humanizer --no-omniroute \
		--no-codex-hooks \
		--no-rtk-hook \
		--no-crg-build \
		--no-apply-codex-config >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if ! bash "$ROOT/CodexHelper/install.sh" --repo "$tmp" --cleanup --yes >"$tmp/cleanup.log" 2>&1; then
		cat "$tmp/cleanup.log" >&2
		return 1
	fi

	local path
	for path in AGENTS.md agent/codex vibe_scripts/codex .codex; do
		if [[ -e "$tmp/$path" || -L "$tmp/$path" ]]; then
			echo "Codex cleanup left $path behind" >&2
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

run_hook_error_logging_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	git -C "$tmp" config user.email "test@example.invalid"
	git -C "$tmp" config user.name "VibeHelper Test"
	touch "$tmp/.gitkeep"
	git -C "$tmp" add .gitkeep
	git -C "$tmp" commit -q -m "init"

	if ! bash "$ROOT/CodexHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--yes \
		--no-install \
		--no-fix \
		--no-humanizer --no-omniroute \
		--no-rtk-hook \
		--no-crg-build \
		--no-apply-codex-config \
		--no-wire >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	printf '{bad json' | (
		cd "$tmp"
		python3 .codex/hooks/pre_tool_use_policy.py
	) >/dev/null

	if ! grep -Fq '"reason": "json-decode-failed"' "$tmp/.cache/hook-errors.log"; then
		echo "Codex hook did not log malformed JSON failure" >&2
		cat "$tmp/.cache/hook-errors.log" >&2
		return 1
	fi
	if ! grep -Fq '"colno": 2' "$tmp/.cache/hook-errors.log" || ! grep -Fq '"error":' "$tmp/.cache/hook-errors.log"; then
		echo "Codex hook log did not include error details and location" >&2
		cat "$tmp/.cache/hook-errors.log" >&2
		return 1
	fi
}

run_lint_ignores_agent_roots_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	git -C "$tmp" config user.email "test@example.invalid"
	git -C "$tmp" config user.name "VibeHelper Test"
	mkdir -p "$tmp/bin" "$tmp/.claude/skills/impeccable/scripts"
	printf '#!/usr/bin/env bash\ntrue\n' >"$tmp/app.sh"
	printf '#!/usr/bin/env bash\nfalse\n' >"$tmp/.claude/skills/impeccable/scripts/installed.sh"
	chmod +x "$tmp/app.sh"
	git -C "$tmp" add app.sh
	git -C "$tmp" commit -q -m "init"
	cat >"$tmp/bin/shellcheck" <<'EOF_SHELLCHECK'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
	printf 'ShellCheck mock\n'
fi
EOF_SHELLCHECK
	chmod +x "$tmp/bin/shellcheck"

	if ! PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--yes \
		--no-install \
		--no-fix \
		--no-humanizer --no-omniroute \
		--no-codex-hooks \
		--no-rtk-hook \
		--no-crg-build \
		--no-apply-codex-config >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	python3 "$ROOT/tests/assert_quality_selection.py" "$tmp" codex shell

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
		>"$tmp/.cache/codex-edited-files.txt"
	cat >"$tmp/package.json" <<'EOF_PACKAGE'
{"scripts":{"typecheck":"echo whole-project-typecheck-ran >&2; exit 99"}}
EOF_PACKAGE
	if edited_output="$(cd "$tmp" && python3 vibe_scripts/codex/agent-check-edited.py --json 2>&1)"; then
		echo "Codex edited-file checker falsely passed without the required Biome tool" >&2
		printf '%s\n' "$edited_output" >&2
		return 1
	fi
	if ! grep -Fq '"src/app.ts"' <<<"$edited_output" || ! grep -Fq '"status": "not_checked"' <<<"$edited_output"; then
		echo "Codex edited-file checker skipped recorded application code" >&2
		printf '%s\n' "$edited_output" >&2
		return 1
	fi
	for excluded in "skills/example/skill.ts" "tools/ai-marketing-skills/eval/run-eval.ts" "untouched.ts" "whole-project-typecheck-ran"; do
		if grep -Fq "$excluded" <<<"$edited_output"; then
			echo "Codex edited-file checker included excluded or untouched code: $excluded" >&2
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
	git -C "$tmp" config user.email "test@example.invalid"
	git -C "$tmp" config user.name "VibeHelper Test"
	mkdir -p "$tmp/bin"
	printf '# Test\n' >"$tmp/README.md"
	git -C "$tmp" add README.md
	git -C "$tmp" commit -q -m "init"
	cat >"$tmp/bin/markdownlint-cli2" <<'EOF_MARKDOWNLINT'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
	printf 'markdownlint-cli2 mock\n'
fi
EOF_MARKDOWNLINT
	chmod +x "$tmp/bin/markdownlint-cli2"

	if ! PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--yes \
		--no-install \
		--no-fix \
		--no-humanizer --no-omniroute \
		--no-codex-hooks \
		--no-rtk-hook \
		--no-crg-build \
		--no-apply-codex-config >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if ! grep -Fq "markdownlint-cli2" "$tmp/vibe_scripts/codex/Makefile"; then
		echo "generated Codex Makefile did not wire markdownlint-cli2" >&2
		cat "$tmp/vibe_scripts/codex/Makefile" >&2
		return 1
	fi
	python3 "$ROOT/tests/assert_quality_selection.py" "$tmp" codex markdown
	if ! grep -Fq "MARKDOWN_EXTS" "$tmp/vibe_scripts/codex/agent-check-edited.py"; then
		echo "generated Codex edited-file checker does not include Markdown files" >&2
		cat "$tmp/vibe_scripts/codex/agent-check-edited.py" >&2
		return 1
	fi
}

run_impeccable_codex_install_ignores_claude_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	git -C "$tmp" init -q
	git -C "$tmp" config user.email "test@example.invalid"
	git -C "$tmp" config user.name "VibeHelper Test"
	mkdir -p "$tmp/.claude/skills/impeccable" "$tmp/bin"
	printf '# Claude Impeccable\n' >"$tmp/.claude/skills/impeccable/SKILL.md"
	touch "$tmp/.gitkeep"
	git -C "$tmp" add .gitkeep
	git -C "$tmp" commit -q -m "init"
	cat >"$tmp/bin/npx" <<'EOF_NPX'
#!/usr/bin/env bash
printf '%s\n' "$*" >>npx.log
if [[ "$1 $2 $3 $4" == "impeccable skills install -y" ]]; then
	mkdir -p .agents/skills/impeccable
	printf '# Codex Impeccable\n' >.agents/skills/impeccable/SKILL.md
	mkdir -p .agents/skills/impeccable/scripts
	printf "export function payload(text, eventName, harness) {\\n  if (harness === 'codex' && eventName === 'Stop') return JSON.stringify({ decision: 'block', reason: text });\\n}\\n" >.agents/skills/impeccable/scripts/hook-lib.mjs
fi
EOF_NPX
	chmod +x "$tmp/bin/npx"

	if ! PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/install.sh" \
		--repo "$tmp" \
		--repo-only \
		--yes \
		--impeccable \
		--no-install \
		--no-fix \
		--no-humanizer --no-omniroute \
		--no-codex-hooks \
		--no-rtk-hook \
		--no-crg-build \
		--no-apply-codex-config >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	if [[ ! -f "$tmp/.agents/skills/impeccable/SKILL.md" ]]; then
		echo "Codex Impeccable install was skipped because Claude Impeccable existed" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi
	if ! grep -q -- '--providers=codex' "$tmp/npx.log"; then
		echo "Impeccable install did not target Codex" >&2
		cat "$tmp/npx.log" >&2
		return 1
	fi
	if grep -Fq "JSON.stringify({ decision: 'block', reason: text })" "$tmp/.agents/skills/impeccable/scripts/hook-lib.mjs"; then
		echo "Codex Impeccable Stop hook kept Claude-style block JSON" >&2
		cat "$tmp/.agents/skills/impeccable/scripts/hook-lib.mjs" >&2
		return 1
	fi
	if ! grep -Fq "JSON.stringify({ continue: false, stopReason: text })" "$tmp/.agents/skills/impeccable/scripts/hook-lib.mjs"; then
		echo "Codex Impeccable Stop hook was not patched to Codex JSON" >&2
		cat "$tmp/.agents/skills/impeccable/scripts/hook-lib.mjs" >&2
		return 1
	fi
}

run_impeccable_codex_hook_root_test() {
	local platform tmp command
	for platform in MacOS WSL; do
		tmp="$(mktemp -d)"
		mkdir -p \
			"$tmp/home" \
			"$tmp/repo/.agents/skills/impeccable/scripts" \
			"$tmp/repo/.codex" \
			"$tmp/repo/nested/deeper"
		git -C "$tmp/repo" init -q
		printf '# Impeccable\n' >"$tmp/repo/.agents/skills/impeccable/SKILL.md"
		cat >"$tmp/repo/.agents/skills/impeccable/scripts/hook-lib.mjs" <<'EOF_HOOK_LIB'
export function payload(text, eventName, harness) {
  if (harness === 'codex' && eventName === 'Stop') return JSON.stringify({ decision: 'block', reason: text });
}
EOF_HOOK_LIB
		cat >"$tmp/repo/.agents/skills/impeccable/scripts/hook.mjs" <<'EOF_HOOK'
process.stderr.write('[fake-impeccable] ran\n');
EOF_HOOK
		cat >"$tmp/repo/.codex/hooks.json" <<'EOF_HOOKS'
{
  "hooks": {
    "PostToolUse": [{"hooks": [{"type": "command", "command": "node \".agents/skills/impeccable/scripts/hook.mjs\""}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "node \".agents/skills/impeccable/scripts/hook.mjs\""}]}]
  }
}
EOF_HOOKS

		if ! (
			cd "$tmp/repo"
			HOME="$tmp/home" bash "$ROOT/CodexHelper/$platform/part1.sh" \
				--repo-only \
				--yes \
				--no-humanizer --no-omniroute \
				--no-codex-hooks \
				--no-rtk-hook \
				--no-crg-build \
				--no-apply-codex-config
		) >"$tmp/install.log" 2>&1; then
			cat "$tmp/install.log" >&2
			return 1
		fi

		if ! grep -Fq "JSON.stringify({ continue: false, stopReason: text })" "$tmp/repo/.agents/skills/impeccable/scripts/hook-lib.mjs"; then
			echo "Codex $platform Impeccable Stop payload was not patched" >&2
			return 1
		fi
		# shellcheck disable=SC2016
		if ! node -e '
const fs = require("fs");
const data = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const commands = Object.values(data.hooks).flatMap(groups => groups.flatMap(group => group.hooks)).map(hook => hook.command);
process.exit(commands.length === 2 && commands.every(command => command.includes("git rev-parse --show-toplevel") && command.includes("exec node \"$hook\"")) ? 0 : 1);
' "$tmp/repo/.codex/hooks.json"; then
			echo "Codex $platform Impeccable hooks were not made repository-root relative" >&2
			cat "$tmp/repo/.codex/hooks.json" >&2
			return 1
		fi

		command="$(node -e 'const h=require(process.argv[1]); process.stdout.write(h.hooks.Stop[0].hooks[0].command)' "$tmp/repo/.codex/hooks.json")"
		if ! (
			cd "$tmp/repo/nested/deeper"
			printf '%s\n' '{"hook_event_name":"Stop"}' | /bin/sh -c "$command"
		) >"$tmp/hook.stdout" 2>"$tmp/hook.stderr"; then
			cat "$tmp/hook.stderr" >&2
			return 1
		fi
		if ! grep -Fq '[fake-impeccable] ran' "$tmp/hook.stderr"; then
			echo "Codex $platform Impeccable hook did not run from the nested directory" >&2
			cat "$tmp/hook.stderr" >&2
			return 1
		fi
		rm -rf "$tmp"
	done
}

run_ponytail_codex_hook_patch_test() {
	local tmp repo manifest
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	repo="$tmp/repo"
	manifest="$tmp/home/.codex/plugins/cache/ponytail/ponytail/4.9.0/hooks/claude-codex-hooks.json"
	mkdir -p "$repo" "$tmp/bin" "$(dirname "$manifest")"
	git -C "$repo" init -q
	git -C "$repo" config user.email "test@example.invalid"
	git -C "$repo" config user.name "VibeHelper Test"
	touch "$repo/.gitkeep"
	git -C "$repo" add .gitkeep
	git -C "$repo" commit -q -m "init"
	cat >"$manifest" <<'EOF_MANIFEST'
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/hooks/ponytail-activate.js\""
          }
        ]
      }
    ],
    "SubagentStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/hooks/ponytail-subagent.js\""
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/hooks/ponytail-mode-tracker.js\""
          }
        ]
      }
    ]
  }
}
EOF_MANIFEST
	cat >"$tmp/bin/codex" <<'EOF_CODEX'
#!/usr/bin/env bash
if [[ "$1 $2 $3" == "plugin list --json" ]]; then
	printf '[{"pluginId":"ponytail@ponytail"}]\n'
	exit 0
fi
if [[ "$1 $2 $3" == "plugin marketplace list" ]]; then
	printf 'ponytail DietrichGebert/ponytail\n'
	exit 0
fi
exit 0
EOF_CODEX
	cat >"$tmp/bin/code-review-graph" <<'EOF_CRG'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then printf 'code-review-graph 1.0.0\n'; fi
exit 0
EOF_CRG
	chmod +x "$tmp/bin/codex" "$tmp/bin/code-review-graph"

	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/install.sh" \
		--repo "$repo" \
		--yes \
		--no-install \
		--no-fix \
		--no-humanizer --no-omniroute \
		--no-rtk-hook \
		--no-crg-build \
		--no-apply-codex-config \
		--no-wire >"$tmp/install.log" 2>&1; then
		cat "$tmp/install.log" >&2
		return 1
	fi

	# shellcheck disable=SC2016
	if ! node -e 'const fs=require("fs"); const p=process.argv[1]; const data=JSON.parse(fs.readFileSync(p,"utf8")); const cmd=data.hooks.UserPromptSubmit[0].hooks[0].command; process.exit(cmd.includes("root=\"${CLAUDE_PLUGIN_ROOT:-}\"") && cmd.includes("for candidate in \"$HOME\"/.codex/plugins/cache/ponytail/ponytail/*") ? 0 : 1)' "$manifest"; then
		echo "Ponytail hook manifest did not get a Codex-safe root fallback" >&2
		cat "$manifest" >&2
		return 1
	fi
	if ! grep -Fq '[ponytail] UserPromptSubmit hook failed:' "$manifest"; then
		echo "Ponytail hook manifest did not get diagnostic hook failure output" >&2
		cat "$manifest" >&2
		return 1
	fi
	if ! grep -Fq "code-review-graph install --platform codex --no-hooks --no-instructions" "$tmp/install.log"; then
		echo "new Codex install did not configure code-review-graph without replacing AGENTS.md" >&2
		cat "$tmp/install.log" >&2
		return 1
	fi

	local cmd
	printf 'process.exit(7)\n' >"$(dirname "$manifest")/ponytail-mode-tracker.js"
	cmd="$(node -e 'const fs=require("fs"); const p=process.argv[1]; const data=JSON.parse(fs.readFileSync(p,"utf8")); console.log(data.hooks.UserPromptSubmit[0].hooks[0].command)' "$manifest")"
	if HOME="$tmp/home" env -u CLAUDE_PLUGIN_ROOT PLUGIN_DATA="$tmp/plugin-data" sh -c "$cmd" 2>"$tmp/hook.err"; then
		echo "Patched Ponytail hook command unexpectedly succeeded" >&2
		return 1
	fi
	if ! grep -Fq "[ponytail] UserPromptSubmit hook failed:" "$tmp/hook.err"; then
		echo "Patched Ponytail hook command did not print the hook event diagnostic" >&2
		cat "$tmp/hook.err" >&2
		return 1
	fi
}

run_force_refresh_planning_test() {
	local tmp
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	mkdir -p "$tmp/bin" "$tmp/home" "$tmp/repo"
	git -C "$tmp/repo" init -q
	printf '<!-- CodexHelper-Version: previous -->\n' >"$tmp/repo/AGENTS.md"

	cat >"$tmp/bin/brew" <<'EOF_BREW'
#!/usr/bin/env bash
if [[ "${1:-}" == "list" && "${2:-}" == "--formula" ]]; then exit 0; fi
exit 0
EOF_BREW
	cat >"$tmp/bin/uv" <<EOF_UV
#!/usr/bin/env bash
if [[ "\${1:-} \${2:-}" == "tool list" ]]; then printf 'code-review-graph v1.0.0\ngraphifyy v1.0.0\n'; fi
if [[ "\${1:-} \${2:-} \${3:-}" == "tool dir --bin" ]]; then printf '%s\n' '$tmp/bin'; fi
exit 0
EOF_UV
	cat >"$tmp/bin/pipx" <<'EOF_PIPX'
#!/usr/bin/env bash
if [[ "${1:-} ${2:-}" == "list --short" ]]; then printf 'uv 1.0.0\n'; fi
exit 0
EOF_PIPX
	cat >"$tmp/bin/rtk" <<'EOF_RTK'
#!/usr/bin/env bash
printf 'CLI proxy for token-optimized LLM context\n'
EOF_RTK
	cat >"$tmp/bin/codex" <<'EOF_CODEX'
#!/usr/bin/env bash
if [[ "${1:-} ${2:-} ${3:-}" == "plugin list --json" ]]; then printf '[{"pluginId":"ponytail@ponytail"}]\n'; fi
if [[ "${1:-} ${2:-} ${3:-}" == "plugin marketplace list" ]]; then printf 'ponytail\n'; fi
exit 0
EOF_CODEX
	cat >"$tmp/bin/code-review-graph" <<'EOF_CRG'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then printf 'code-review-graph 1.0.0\n'; fi
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

	if ! (cd "$tmp/repo" && HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/MacOS/part1.sh" --dry-run --force --yes --no-humanizer --no-omniroute --no-crg-build --no-apply-codex-config >"$tmp/mac.log" 2>&1); then
		cat "$tmp/mac.log" >&2
		return 1
	fi
	for expected in "brew upgrade rtk" "brew upgrade uv" "brew upgrade pipx" "uv tool upgrade code-review-graph" "uv tool upgrade graphifyy" "graphify install --platform codex" "codex plugin marketplace upgrade ponytail" "skills add Leonxlnx/unlazy --agent codex"; do
		if ! grep -Fq "$expected" "$tmp/mac.log"; then
			echo "Codex macOS --force did not plan: $expected" >&2
			cat "$tmp/mac.log" >&2
			return 1
		fi
	done
	if grep -Fq "code-review-graph install --platform codex" "$tmp/mac.log"; then
		echo "Codex --force reconfigured unchanged code-review-graph" >&2
		cat "$tmp/mac.log" >&2
		return 1
	fi
	if ! grep -Fq "only if its version changes" "$tmp/mac.log"; then
		echo "Codex --force did not make code-review-graph setup conditional" >&2
		cat "$tmp/mac.log" >&2
		return 1
	fi
	if ! grep -Fq "Context7 setup not run in --yes mode" "$tmp/mac.log"; then
		echo "Codex --force did not report missing Context7 setup" >&2
		cat "$tmp/mac.log" >&2
		return 1
	fi

	if ! (cd "$tmp/repo" && HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/MacOS/part1.sh" --dry-run --fresh-install --yes --no-crg-build --no-apply-codex-config >"$tmp/mac-fresh.log" 2>&1); then
		cat "$tmp/mac-fresh.log" >&2
		return 1
	fi
	for expected in "skills add blader/humanizer --agent codex" "skills add Leonxlnx/unlazy --agent codex" "graphify install --platform codex" "code-review-graph install --platform codex --no-hooks --no-instructions" "ctx7 setup --codex --mcp --oauth -y" "codex mcp login context7" "impeccable skills install -y --providers=codex --scope=project --force"; do
		if ! grep -Fq -- "$expected" "$tmp/mac-fresh.log"; then
			echo "Codex macOS --fresh-install did not plan: $expected" >&2
			cat "$tmp/mac-fresh.log" >&2
			return 1
		fi
	done
	for forbidden in "--agent claude" "--providers=claude" "--platform claude" "ctx7 setup --claude"; do
		if grep -Fq -- "$forbidden" "$tmp/mac-fresh.log"; then
			echo "Codex macOS --fresh-install incorrectly targeted Claude: $forbidden" >&2
			cat "$tmp/mac-fresh.log" >&2
			return 1
		fi
	done
	if ! HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/install.sh" --repo "$tmp/repo" --dry-run --fresh-install --yes --no-install --no-fix --no-crg-build --no-apply-codex-config >"$tmp/unified-fresh.log" 2>&1; then
		cat "$tmp/unified-fresh.log" >&2
		return 1
	fi
	for expected in "ctx7 setup --codex --mcp --oauth -y" "codex mcp login context7" "impeccable skills install -y --providers=codex --scope=project --force"; do
		if ! grep -Fq -- "$expected" "$tmp/unified-fresh.log"; then
			echo "Codex unified --fresh-install did not plan: $expected" >&2
			cat "$tmp/unified-fresh.log" >&2
			return 1
		fi
	done
	for forbidden in "--agent claude" "--providers=claude" "--platform claude" "ctx7 setup --claude"; do
		if grep -Fq -- "$forbidden" "$tmp/unified-fresh.log"; then
			echo "Codex unified --fresh-install incorrectly targeted Claude: $forbidden" >&2
			cat "$tmp/unified-fresh.log" >&2
			return 1
		fi
	done

	cat >"$tmp/bin/rtk" <<'EOF_WRONG_RTK'
#!/usr/bin/env bash
printf 'unrelated RTK utility\n'
EOF_WRONG_RTK
	chmod +x "$tmp/bin/rtk"
	(cd "$tmp/repo" && HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/MacOS/part1.sh" --dry-run --force --yes --no-humanizer --no-omniroute --no-crg-build --no-apply-codex-config >"$tmp/wrong-rtk.log" 2>&1) || true
	if ! grep -Fq "RTK name collision" "$tmp/wrong-rtk.log" || grep -Fq "brew upgrade rtk" "$tmp/wrong-rtk.log"; then
		echo "Codex --force did not reject an unrelated rtk executable" >&2
		cat "$tmp/wrong-rtk.log" >&2
		return 1
	fi
	cat >"$tmp/bin/rtk" <<'EOF_RTK'
#!/usr/bin/env bash
printf 'CLI proxy for token-optimized LLM context\n'
EOF_RTK
	chmod +x "$tmp/bin/rtk"

	if ! (cd "$tmp/repo" && HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/WSL/part1.sh" --dry-run --force --yes --no-humanizer --no-omniroute --no-crg-build --no-apply-codex-config >"$tmp/wsl.log" 2>&1); then
		cat "$tmp/wsl.log" >&2
		return 1
	fi
	for expected in "raw.githubusercontent.com/rtk-ai/rtk/master/install.sh" "pipx upgrade uv" "uv tool upgrade code-review-graph" "uv tool upgrade graphifyy" "graphify install --platform codex" "codex plugin marketplace upgrade ponytail" "skills add Leonxlnx/unlazy --agent codex"; do
		if ! grep -Fq "$expected" "$tmp/wsl.log"; then
			echo "Codex WSL --force did not plan: $expected" >&2
			cat "$tmp/wsl.log" >&2
			return 1
		fi
	done
	if grep -Fq "code-review-graph install --platform codex" "$tmp/wsl.log" || ! grep -Fq "only if its version changes" "$tmp/wsl.log"; then
		echo "Codex WSL --force did not keep code-review-graph setup conditional" >&2
		cat "$tmp/wsl.log" >&2
		return 1
	fi

	if ! (cd "$tmp/repo" && HOME="$tmp/home" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/WSL/part1.sh" --dry-run --fresh-install --yes --no-crg-build --no-apply-codex-config >"$tmp/wsl-fresh.log" 2>&1); then
		cat "$tmp/wsl-fresh.log" >&2
		return 1
	fi
	for expected in "skills add blader/humanizer --agent codex" "skills add Leonxlnx/unlazy --agent codex" "graphify install --platform codex" "ctx7 setup --codex --mcp --oauth -y" "codex mcp login context7" "impeccable skills install -y --providers=codex --scope=project --force"; do
		if ! grep -Fq -- "$expected" "$tmp/wsl-fresh.log"; then
			echo "Codex WSL --fresh-install did not plan: $expected" >&2
			cat "$tmp/wsl-fresh.log" >&2
			return 1
		fi
	done
	for forbidden in "--agent claude" "--providers=claude" "--platform claude" "ctx7 setup --claude"; do
		if grep -Fq -- "$forbidden" "$tmp/wsl-fresh.log"; then
			echo "Codex WSL --fresh-install incorrectly targeted Claude: $forbidden" >&2
			cat "$tmp/wsl-fresh.log" >&2
			return 1
		fi
	done
}

run_rerun_idempotency_test() {
	local platform tmp mode state log
	local -a mode_args
	for platform in MacOS WSL; do
		tmp="$(mktemp -d)"
		mkdir -p "$tmp/bin" "$tmp/home" "$tmp/repo/.agents/skills/unlazy" "$tmp/repo/.code-review-graph"
		git -C "$tmp/repo" init -q
		printf '<!-- CodexHelper-Version: previous -->\n' >"$tmp/repo/AGENTS.md"
		printf '# Unlazy\n' >"$tmp/repo/.agents/skills/unlazy/SKILL.md"
		printf 'graph\n' >"$tmp/repo/.code-review-graph/graph.db"
		cat >"$tmp/bin/codex" <<'EOF_CODEX'
#!/usr/bin/env bash
if [[ "${1:-} ${2:-} ${3:-}" == "mcp get context7" ]]; then
	case "${CONTEXT7_STATE:-missing}" in
	exact)
		printf 'enabled: true\nurl: https://mcp.context7.com/mcp/oauth\n'
		exit 0
		;;
	wrong)
		printf 'enabled: true\nurl: https://wrong.example/mcp\n'
		exit 0
		;;
	*)
		exit 1
		;;
	esac
fi
if [[ "${1:-} ${2:-} ${3:-}" == "plugin list --json" ]]; then
	printf '[{"pluginId":"ponytail@ponytail"}]\n'
	exit 0
fi
if [[ "${1:-} ${2:-} ${3:-}" == "plugin marketplace list" ]]; then
	printf 'ponytail\n'
	exit 0
fi
exit 0
EOF_CODEX
		cat >"$tmp/bin/code-review-graph" <<'EOF_CRG'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then printf 'code-review-graph 1.0.0\n'; fi
exit 0
EOF_CRG
		chmod +x "$tmp/bin/codex" "$tmp/bin/code-review-graph"

		for mode in normal force; do
			mode_args=(--yes)
			[[ "$mode" == "force" ]] && mode_args+=(--force)
			log="$tmp/context7-$mode.log"
			if ! (
				cd "$tmp/repo"
				HOME="$tmp/home" CONTEXT7_STATE=exact PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config "${mode_args[@]}"
			) >"$log" 2>&1; then
				cat "$log" >&2
				return 1
			fi
			if ! grep -Fq "Context7 MCP is already registered" "$log" ||
				grep -Eq '\$ npx ctx7 setup --codex' "$log" ||
				grep -Fq "Configure Context7 MCP" "$log"; then
				echo "Codex $platform $mode rerun did not skip exact Context7 state" >&2
				cat "$log" >&2
				return 1
			fi
		done

		log="$tmp/context7-fresh.log"
		if ! (
			cd "$tmp/repo"
			HOME="$tmp/home" CONTEXT7_STATE=exact PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --fresh-install --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$log" 2>&1 ||
			grep -Eq '\$ npx ctx7 setup --codex' "$log" ||
			! grep -Eq '\$ codex mcp login context7' "$log"; then
			echo "Codex $platform fresh install did not preserve registration and rerun OAuth login" >&2
			cat "$log" >&2
			return 1
		fi

		log="$tmp/context7-explicit.log"
		if ! (
			cd "$tmp/repo"
			HOME="$tmp/home" CONTEXT7_STATE=exact PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --yes --context7 --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$log" 2>&1 ||
			grep -Eq '\$ npx ctx7 setup --codex' "$log" ||
			! grep -Eq '\$ codex mcp login context7' "$log"; then
			echo "Codex $platform explicit Context7 request did not rerun OAuth login" >&2
			cat "$log" >&2
			return 1
		fi

		for state in missing wrong; do
			log="$tmp/context7-$state-yes.log"
			if ! (
				cd "$tmp/repo"
				HOME="$tmp/home" CONTEXT7_STATE="$state" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
			) >"$log" 2>&1; then
				cat "$log" >&2
				return 1
			fi
			if ! grep -Fq "Context7 setup not run in --yes mode" "$log" ||
				grep -Eq '\$ npx ctx7 setup --codex' "$log"; then
				echo "Codex $platform $state Context7 state was not handled safely in --yes mode" >&2
				cat "$log" >&2
				return 1
			fi

			log="$tmp/context7-$state-interactive.log"
			# Accept the default ADHD setup, then the Context7 prompt.
			if ! printf 'y\ny\n' | (
				cd "$tmp/repo"
				HOME="$tmp/home" CONTEXT7_STATE="$state" PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
			) >"$log" 2>&1; then
				cat "$log" >&2
				return 1
			fi
			if ! grep -Eq '\$ codex plugin add i-have-adhd@i-have-adhd' "$log" ||
				! grep -Eq '\$ npx ctx7 setup --codex --mcp --oauth -y' "$log" ||
				! grep -Eq '\$ codex mcp login context7' "$log"; then
				echo "Codex $platform $state Context7 state did not accept interactive setup" >&2
				cat "$log" >&2
				return 1
			fi
		done

		for mode in normal force; do
			mode_args=(--yes)
			[[ "$mode" == "force" ]] && mode_args+=(--force)
			log="$tmp/graph-existing-$mode.log"
			if ! (
				cd "$tmp/repo"
				HOME="$tmp/home" CONTEXT7_STATE=exact PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-apply-codex-config "${mode_args[@]}"
			) >"$log" 2>&1; then
				cat "$log" >&2
				return 1
			fi
			if ! grep -Fq "repo index already exists; skipping rebuild" "$log" ||
				grep -Eq '\$ .*code-review-graph build' "$log"; then
				echo "Codex $platform $mode rerun rebuilt an existing graph index" >&2
				cat "$log" >&2
				return 1
			fi
		done

		log="$tmp/graph-fresh.log"
		if ! (
			cd "$tmp/repo"
			HOME="$tmp/home" CONTEXT7_STATE=exact PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --fresh-install --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-apply-codex-config
		) >"$log" 2>&1 || ! grep -Eq '\$ .*code-review-graph build' "$log"; then
			echo "Codex $platform fresh install did not allow a graph rebuild" >&2
			cat "$log" >&2
			return 1
		fi

		: >"$tmp/repo/.code-review-graph/graph.db"
		log="$tmp/graph-missing.log"
		# Accept the default ADHD setup, then the graph-build prompt.
		if ! printf 'y\ny\n' | (
			cd "$tmp/repo"
			HOME="$tmp/home" CONTEXT7_STATE=exact PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-apply-codex-config
		) >"$log" 2>&1; then
			cat "$log" >&2
			return 1
		fi
		if ! grep -Eq '\$ codex plugin add i-have-adhd@i-have-adhd' "$log" ||
			! grep -Eq '\$ .*code-review-graph build' "$log"; then
			echo "Codex $platform missing graph index did not accept an interactive build" >&2
			cat "$log" >&2
			return 1
		fi

		log="$tmp/graph-disabled.log"
		if ! (
			cd "$tmp/repo"
			HOME="$tmp/home" CONTEXT7_STATE=exact PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/$platform/part1.sh" --dry-run --fresh-install --yes --no-humanizer --no-omniroute --no-codex-hooks --no-rtk-hook --no-crg-build --no-apply-codex-config
		) >"$log" 2>&1; then
			cat "$log" >&2
			return 1
		fi
		if grep -Fq "Build code-review-graph index for this repo now?" "$log" ||
			grep -Eq '\$ .*code-review-graph build' "$log"; then
			echo "Codex $platform --no-crg-build did not suppress graph building" >&2
			cat "$log" >&2
			return 1
		fi
		rm -rf "$tmp"
	done
}

run_skill_force_refresh_unit_test() {
	ROOT_PATH="$ROOT" python3 - <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

path = Path(os.environ["ROOT_PATH"]) / "CodexHelper/templates/vibe_scripts/sync-skills.py"
spec = importlib.util.spec_from_file_location("sync_skills", path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

finding = module.Finding("skill", "humanizer", "codex", "ok", "installed", ["refresh"])
assert module.should_update(finding, force=True, allow_manual=False)
assert not module.should_update(finding, force=False, allow_manual=True)
unknown = module.Finding("skill", "humanizer", "codex", "unknown", "missing CLI", ["refresh"])
assert not module.should_update(unknown, force=True, allow_manual=True)
for provider in ("codex", "claude-code"):
    command = module.DEFAULT_LOCK["skills"]["impeccable"]["providers"][provider]["updateCommand"]
    assert command[:4] == ["npx", "impeccable", "skills", "install"]
    assert f"--providers={provider}" in command
    assert command[-1] == "--force"
PY
}

run_quality_force_refresh_planning_test() {
	local tmp tool
	tmp="$(mktemp -d)"
	trap 'rm -rf "$tmp"' RETURN
	mkdir -p "$tmp/bin" "$tmp/repo/node_modules/.bin"
	git -C "$tmp/repo" init -q
	printf 'print("test")\n' >"$tmp/repo/app.py"
	printf '#!/usr/bin/env bash\ntrue\n' >"$tmp/repo/app.sh"
	printf '<?php echo "test";\n' >"$tmp/repo/app.php"
	printf '# Test\n' >"$tmp/repo/README.md"
	printf '{"name":"test","private":true}\n' >"$tmp/repo/package.json"

	cat >"$tmp/bin/brew" <<'EOF_BREW'
#!/usr/bin/env bash
if [[ "${1:-}" == "list" && "${2:-}" == "--formula" ]]; then exit 0; fi
exit 0
EOF_BREW
	cat >"$tmp/bin/uv" <<'EOF_UV'
#!/usr/bin/env bash
if [[ "${1:-} ${2:-}" == "tool list" ]]; then printf 'ruff v1.0.0\n'; fi
exit 0
EOF_UV
	cat >"$tmp/bin/pipx" <<'EOF_PIPX'
#!/usr/bin/env bash
if [[ "${1:-} ${2:-}" == "list --short" ]]; then printf 'semgrep 1.0.0\n'; fi
exit 0
EOF_PIPX
	cat >"$tmp/bin/composer" <<'EOF_COMPOSER'
#!/usr/bin/env bash
exit 0
EOF_COMPOSER
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
	for tool in ruff semgrep shellcheck shfmt phpcs phpstan npm; do
		cat >"$tmp/bin/$tool" <<'EOF_TOOL'
#!/usr/bin/env bash
exit 0
EOF_TOOL
	done
	for tool in biome htmlhint markdownlint-cli2; do
		cat >"$tmp/repo/node_modules/.bin/$tool" <<'EOF_TOOL'
#!/usr/bin/env bash
exit 0
EOF_TOOL
	done
	chmod +x "$tmp/bin"/* "$tmp/repo/node_modules/.bin"/* "$tmp/repo/app.sh"

	if ! PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/MacOS/part2.sh" --repo "$tmp/repo" --dry-run --force --yes --no-wire --no-fix >"$tmp/mac-quality.log" 2>&1; then
		cat "$tmp/mac-quality.log" >&2
		return 1
	fi
	for expected in "uv tool upgrade ruff" "@biomejs/biome@latest" "composer update squizlabs/php_codesniffer phpstan/phpstan --with-dependencies" "brew upgrade shellcheck shfmt"; do
		if ! grep -Fq "$expected" "$tmp/mac-quality.log"; then
			echo "Codex macOS quality --force did not plan: $expected" >&2
			cat "$tmp/mac-quality.log" >&2
			return 1
		fi
	done

	if ! PATH="$tmp/bin:$PATH" bash "$ROOT/CodexHelper/WSL/part2.sh" --repo "$tmp/repo" --dry-run --force --yes --no-wire --no-fix >"$tmp/wsl-quality.log" 2>&1; then
		cat "$tmp/wsl-quality.log" >&2
		return 1
	fi
	for expected in "uv tool upgrade ruff" "apt-get install -y shellcheck shfmt composer"; do
		if ! grep -Fq "$expected" "$tmp/wsl-quality.log"; then
			echo "Codex WSL quality --force did not plan: $expected" >&2
			cat "$tmp/wsl-quality.log" >&2
			return 1
		fi
	done
}

printf 'Running %s\n' run_default_wiring_test
run_default_wiring_test
printf 'Running %s\n' run_context7_oauth_planning_test
run_context7_oauth_planning_test
printf 'Running %s\n' run_chrome_mcp_test
run_chrome_mcp_test
printf 'Running %s\n' run_force_repairs_legacy_makefile_test
run_force_repairs_legacy_makefile_test
printf 'Running %s\n' run_no_wire_test
run_no_wire_test
printf 'Running %s\n' run_cleanup_test
run_cleanup_test
printf 'Running %s\n' run_hook_error_logging_test
run_hook_error_logging_test
printf 'Running %s\n' run_lint_ignores_agent_roots_test
run_lint_ignores_agent_roots_test
printf 'Running %s\n' run_markdown_lint_wiring_test
run_markdown_lint_wiring_test
printf 'Running %s\n' run_impeccable_codex_install_ignores_claude_test
run_impeccable_codex_install_ignores_claude_test
printf 'Running %s\n' run_impeccable_codex_hook_root_test
run_impeccable_codex_hook_root_test
printf 'Running %s\n' run_ponytail_codex_hook_patch_test
run_ponytail_codex_hook_patch_test
printf 'Running %s\n' run_force_refresh_planning_test
run_force_refresh_planning_test
printf 'Running %s\n' run_rerun_idempotency_test
run_rerun_idempotency_test
printf 'Running %s\n' run_skill_force_refresh_unit_test
run_skill_force_refresh_unit_test
printf 'Running %s\n' run_quality_force_refresh_planning_test
run_quality_force_refresh_planning_test
python3 "$ROOT/CodexHelper/tests/quality_controls_test.py"
echo "CodexHelper install smoke tests passed."
