# Claude Helper

Claude Helper is a personal-use bootstrap for adding a small Claude Code
workflow to another repository.

Use `MacOS/` on macOS. Use `WSL/` inside Ubuntu WSL.

It helps Claude Code:

- read project instructions from `CLAUDE.md`
- expose repeatable project slash commands
- check files it edits with capped output
- keep durable repo knowledge in `codebase-wiki/`

## What The Parts Do

- `install.sh` is the recommended entry point. It detects macOS or Ubuntu WSL
  and runs both parts in order.
- `part1.sh` validates and creates Claude Code project files, local hook
  scripts, repo memory files, helper scripts, and notes files. On WSL, it first
  bootstraps missing base packages (`curl git ca-certificates python3
  python3-pip python3-venv pipx nodejs npm`) via `apt-get` before running the
  shared logic; pass `--no-system-packages` to skip that.
- `part2.sh` detects available quality tools and writes Makefile targets for
  checks that actually exist.
- `templates/` contains the files copied into configured repositories.

## Recommended Install Flow

From the repository you want to configure, preview both phases first:

```sh
bash /path/to/ClaudeHelper/install.sh --dry-run
```

If the preview looks right, run it:

```sh
bash /path/to/ClaudeHelper/install.sh
```

The installer selects macOS or Ubuntu WSL, runs part 1, and then runs part 2
only if part 1 succeeds. Wiring `vibe_scripts/claude/Makefile` is enabled by
default, including alongside Codex. Pass `--no-wire` to skip wiring. Use
`--repo /path/to/project` when running it outside the target repository.

For an existing installation, `--force` is the in-place update path:

```sh
bash /path/to/ClaudeHelper/install.sh --force
```

When Codex project setup is detected, `--force` refreshes ClaudeHelper tools,
scripts, hooks, instructions and its own Makefile while preserving shared
project files. Without Codex setup, it refreshes both tools and managed files.
Package refreshes use the detected owner; unknown owners are reported and left
unchanged. Ordinary `--force` updates skip Context7 setup and keep the existing
code-review-graph MCP configuration unless the code-review-graph package version
changes. New and fresh installs configure the MCP integration.

To install missing tools and replace installed helper-managed skill/tool files
in one run, use `--fresh-install`. This includes a forced Impeccable reinstall
for Claude Code:

```sh
bash /path/to/ClaudeHelper/install.sh --fresh-install --yes
```

Generated `CLAUDE.md` files require a separate reviewer agent after substantive
coding to check for breaking changes, scope creep, and drift from the request.

Humanizer and Context7 are installed by default. To skip them, use
`--no-humanizer` / `--no-context7` :

```sh
bash /path/to/ClaudeHelper/install.sh --no-humanizer --no-context7
```

Context7 setup writes a live API key into the target repo's `.mcp.json` ; the
installer gitignores that file automatically.

To install the Claude files without writing the Makefile, use `--no-wire`:

```sh
bash /path/to/ClaudeHelper/install.sh --no-wire
```

Restart Claude Code after installation, then invoke `/humanizer` or ask Claude
to humanize prose.

## Manual Platform-Specific Flow

The underlying phase scripts remain available for manual macOS or Ubuntu WSL
setup:

```sh
bash /path/to/ClaudeHelper/MacOS/part1.sh --dry-run
bash /path/to/ClaudeHelper/MacOS/part1.sh
bash /path/to/ClaudeHelper/MacOS/part1.sh --fresh-install
bash /path/to/ClaudeHelper/MacOS/part2.sh --dry-run --wire
bash /path/to/ClaudeHelper/MacOS/part2.sh --wire
```

Use the equivalent scripts under `WSL/` inside Ubuntu WSL.

## After Install

Restart Claude Code in the repo root so it reloads `CLAUDE.md` and
`.claude/settings.local.json` .

Run one manual check after setup:

```sh
make -f vibe_scripts/claude/Makefile edited-ai
```

The generated hooks are intentionally simple:

- `PreToolUse` blocks common Git-writing and destructive Bash commands.
- `PreToolUse` also routes eligible read-only/noisy Bash commands (`git
  diff|log|show|status`, `grep` , `ls` ) through `rtk` to reduce token usage,
  using the hook's `updatedInput` support. The routing hook is a no-op when
  `rtk` is not on `PATH` ; install it with `brew install rtk` to activate it.
- `PostToolUse` records edited files, runs the edited-file checker, and returns
  failures to Claude.
- `Stop` rechecks recorded edited files before Claude Code finishes a turn and
  prevents recursive Stop-hook loops.

Project slash commands are created under `.claude/commands/`:

- `/edited-ai`
- `/verify-ai`
- `/wiki-ai`
- `make -f vibe_scripts/claude/Makefile skills-check`
- `make -f vibe_scripts/claude/Makefile skills-update`

## Development Checks

Run the hook regression tests, unified-installer tests, and
disposable-repository installer smoke tests with:

```sh
bash tests/run.sh
```

## Notes

- This setup is intentionally local and optimized for one person rather than
  shared team configuration.
- Claude-specific `.claude/` files and `CLAUDE.md` can coexist with Codex
  configuration; each provider owns its scripts, workflow instructions and
  generated Makefile.
- Generated managed files include a `ClaudeHelper-Version` marker.
- `skills-lock.json` records the desired managed skill/plugin set.
  `make -f vibe_scripts/claude/Makefile skills-check` reports missing or drifted
  skills/plugins; `make -f vibe_scripts/claude/Makefile skills-update` refreshes
  only policy-approved items unless you explicitly force a refresh.
- `.claude/settings.local.json` is ignored by Git and must be trusted by the
  local Claude Code installation.
- Full quality logs are stored under `.cache/ai-quality/` ; AI-facing output is
  capped.

## Generated files and upgrades

Quality commands use `vibe_scripts/claude/Makefile`. Helper scripts live beside
it, and workflow instructions live in `agent/claude/`. The project root
Makefile and existing wiki pages are preserved, including with `--force`.

For an older installation, preview the installer with `--dry-run --force`, then
rerun with `--force` to refresh existing managed instructions and hooks. Restart
the agent and review/trust updated hooks where required. Legacy root Makefile
targets and shared `vibe_scripts/*` files are retained for manual review; the
new
commands do not use them. Existing user instructions may need their command
paths updated manually.

`--cleanup` removes this provider's configuration, top-level instruction file,
`agent/claude/` and `vibe_scripts/claude/`. It preserves the project
Makefile, other provider, shared skills, wiki, notes, caches, skill lock,
`.mcp.json`, `.gitignore` and legacy helpers. Review those shared files manually
if removing VibeHelper entirely. Skill configuration and project memory remain
shared; separating scripts does not add missing language checks.
