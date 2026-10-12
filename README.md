# VibeHelper

VibeHelper adds a small Codex or Claude Code workflow to an existing project.
It supports macOS and Ubuntu WSL.

The generated files tell the assistant what to read, how to check its edits,
and where to keep useful repo notes. Hooks can enforce shell policies, check
edited files, and trim noisy command output before it reaches the assistant.

## What it helps with

- The assistant starts with project instructions instead of guessing how the
  repo works.
- Hooks check edited files after changes and again before the assistant stops.
- Long lint and security output gets trimmed so the assistant can focus on the
  useful part.
- Stable project knowledge can live in `codebase-wiki/` instead of being
  rediscovered every session.
- Session notes can be kept in `notes/`, which makes it easier to trace what
  was asked and what changed.
- Writing help is included through Humanizer, so generated prose can sound less
  stiff.
- Existing helper-managed tools can be refreshed without replacing unrelated
  installations.

## What you gain

You spend less time repeating repo context, and future sessions have a place to
pick up durable knowledge. Each project can use only the workflow pieces that
fit.

This repository is intended for personal workflow bootstrapping across
multiple projects.

## Install it in another repo

Run the installer from the project you want to configure. If it is outside a
Git repository, the installer runs `git init`. Existing repositories and linked
worktrees are reused. Dry runs only report the initialization. No files are
staged or committed. Preview first.

For Codex:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --dry-run
bash /path/to/VibeHelper/CodexHelper/install.sh
```

For Claude Code:

```sh
bash /path/to/VibeHelper/ClaudeHelper/install.sh --dry-run
bash /path/to/VibeHelper/ClaudeHelper/install.sh
```

If you run the command from somewhere else, pass the target repo:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --repo /path/to/project
bash /path/to/VibeHelper/ClaudeHelper/install.sh --repo /path/to/project
```

Both installers also support `--repo-only` when you want project files without
global tool installs.

Use the default command for a first install. On a rerun, versioned managed
files stay in place unless you request an update.

For an existing installation, `--force` is the in-place update path. It
refreshes
managed files and installed helper-managed tools:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --force
bash /path/to/VibeHelper/ClaudeHelper/install.sh --force
```

Refreshes use the detected package owner, such as Homebrew, apt, uv, pipx, npm,
or Composer. Unknown or unrelated installations are reported and left
unchanged. Managed files are backed up before replacement. If ClaudeHelper
finds an existing Codex setup, it refreshes its own scripts, hooks, instructions
and Makefile while preserving shared project files. Ordinary `--force`
updates skip Context7 setup. They also keep the existing code-review-graph MCP
configuration unless the code-review-graph package version changes.

Both installers configure Chrome DevTools MCP through
`npx chrome-devtools-mcp@latest`. A correct existing registration is left
unchanged on normal, force, and fresh runs because `@latest` resolves updates
when the server starts. `--force` repairs stale registrations; use
`--no-chrome-mcp` to skip setup. Chrome itself is not installed: use current
stable Chrome and a package-supported Node.js version (currently
`^20.19.0 || ^22.12.0 || >=23`). Inside WSL, install Linux Chrome or configure
an explicit connection to Chrome running outside WSL.

Both helpers have a non-interactive fresh setup that installs missing tools and
replaces existing helper-managed skill and tool files. Codex fresh installs
explicitly target Codex, including Humanizer, Unlazy, Graphify, Context7,
Chrome DevTools MCP, and Impeccable:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --fresh-install --yes
bash /path/to/VibeHelper/ClaudeHelper/install.sh --fresh-install --yes
```

Fresh installs configure the code-review-graph MCP integration. The generated
Codex and Claude instructions require a separate reviewer agent after
substantive coding to check for breaking changes, scope creep, and drift from
the request.

When RTK is installed, the generated command-routing hook can trim eligible
read-only output before the assistant sees it. Shell expansions and potentially
mutating commands pass through unchanged. RTK records the raw and filtered
output sizes for routed commands and estimates tokens from those sizes. The
result is useful for comparing output reduction, but it is not a model-provider
usage or billing count.

CodexHelper records RTK's project-counter change during a Codex session and
code-review-graph's own context estimate. Run
`make -f vibe_scripts/codex/Makefile savings-ai` for the latest
report. The figures stay separate because they measure different baselines;
VibeHelper does not claim a combined total for features that cannot be measured.

## Stage completed changes

After code changes, agents write `git-add.txt` with the reviewed files for that
task. Review the diffs, then run from the project root:

```sh
git add --pathspec-from-file=git-add.txt
```

Directly below the staging command in their final reply, agents review every
file in `git-add.txt`: its exact path, what changed, and why the task needs it.
List one file per line with its own explanation; never group multiple file paths
on one line.
These are files selected for staging. Excluded files and reasons are reported
separately.

Agents inspect each diff and list only necessary source, tests, configuration,
docs and requested assets. They exclude secrets, local tooling, logs, caches,
backups, installed dependencies, build output and temporary reports. Package
manifests belong in the list for intentional package configuration changes;
lockfiles require an intentional dependency or toolchain change.
The list uses explicit literal file paths; it is never a dump of all Git status
output. Ignored files and the manifest itself are excluded.

Files containing unrelated edits are left out and reported for manual hunk review.
Every entry requires review of its full staged and unstaged diff, or its full
contents for a new file. Agents record why each file belongs and check for
secrets, private data and unintended changes. Required companion files must
also be reviewed or already committed; incomplete dependent changes stay out.
Uncertainty means exclusion, with the reason reported to you.

After formatting, agents check exact paths and preview the nonempty list with
`git add --dry-run --pathspec-from-file=git-add.txt`. The final reviewer checks
the list and evidence. Any later file edits require a fresh review. Existing
staged changes are reported separately; this list does not remove them.

Staging still includes every edit in each listed file. Running
`git add git-add.txt` only stages the manifest.
Agents leave staging and committing to you.

## Free model delegation with OmniRoute

Installers ask whether to install OmniRoute and its dashboard, with no as the
default. Only an explicit yes starts setup; empty input or EOF skips it.
`--yes` does not approve this optional installation. Both helpers share
`~/.local/share/vibehelper/omniroute/`. An existing
compatible global executable is reused; otherwise the installer installs the
verified version privately. Project scripts and instructions are installed for
Codex and Claude on both macOS and WSL. `--force` updates existing projects.

Setup asks you to choose a dashboard password. It preserves an existing
password. Unattended setup needs `VIBEHELPER_OMNIROUTE_PASSWORD` supplied by you;
without it, the runtime installs and configuration stays pending. It never
invents a dashboard password. The dashboard is at <http://127.0.0.1:20130>.
Use `--no-omniroute` to skip runtime setup, or `--repo-only` for project files.
Node.js must satisfy OmniRoute's requirement: `>=22.22.2 <23 || >=24 <27`.

Create an [OpenRouter account and API key](https://openrouter.ai/settings/keys),
then run the matching project helper and enter the key at its hidden prompt:

```sh
python3 vibe_scripts/codex/omniroute.py connect openrouter
# For Claude projects, use vibe_scripts/claude/omniroute.py instead.
```

For a suitable simple task, agents ask whether you want to try OmniRoute to
save tokens, unless you already authorized it. They wait for your reply before
sending task context. If you decline, the current agent continues. After approval,
they try one bounded request. The installer ships no fixed model list: the helper
discovers models for eligible accounts, checks context size, and ranks candidates
for `code`, `edit`, `summary` or `extract` using capability and name hints. Use
`ask --task edit` with the task on stdin; `route --task edit` previews selection
without inference. Ranking is heuristic and every proposal needs main-agent
review and the normal project checks. Token savings are not yet measured.

Add accounts through the dashboard. Use `report` to find connection IDs, then
`confirm-free CONNECTION_ID --billing-disabled` only after verifying free-only
API usage, disabled paid billing and no spendable paid credits. `all` confirms
current accounts only. Revoke with `revoke-free CONNECTION_ID` before changing
credentials or billing; confirmations do not track credential replacements.
OpenRouter uses independently verified zero-price models without confirmation.
Kilo Gateway also requires zero-price catalog entries. Other audited API-key
providers are Gemini, Groq, NVIDIA, Cloudflare AI and SambaNova.

Every `ask` checks cooldowns, model locks and recent OpenRouter free-request
estimates. Other quota observations are advisory because their scope varies.
Discovery uses account caches, fetching model metadata on cache misses. It sends
no inference probes. The single attempt uses a temporary key restricted to its
account and model, deleted afterward and backed by expiry. Managed setup disables
cloud sync. Unavailable routes or rejected output return work to the main agent.

Use `start` after reboot; no login service is installed. `status` checks gateway
policy; successful inference needs a real task. Project cleanup leaves the shared
runtime and accounts intact. Provider privacy terms apply to submitted snippets.

For a manual inventory, run `python3 vibe_scripts/codex/omniroute.py report`
(or the Claude path). Add `--json` for structured output, or `--refresh-models`
to fetch current model metadata for the seven audited API-key providers listed
in the installed OmniRoute guide. Default reporting reads local caches only.
The report includes connections, model IDs, recorded gateway tokens, quota
snapshot times and local rate-limit status. Unreported remaining tokens and
provider limits are unknown. OpenRouter free-request counters are local estimates;
model catalogs do not prove account access or free pricing.

## What gets added

Depending on the helper and options you choose, VibeHelper can add:

- project instructions such as `AGENTS.md` or `CLAUDE.md`
- local hooks for safer assistant runs
- `agent/` workflow guidance
- `codebase-wiki/` repo memory
- `notes/` session logs
- helper scripts for edited-file checks
- Separate Makefiles in `vibe_scripts/codex/` and `vibe_scripts/claude/`, with
  edited-file, lint, verification, wiki and skills commands; Codex also has
  `savings-ai`

## What it can install

Defaults and opt-in tools differ between the two helpers. They can install:

- Codex project files: instructions, hooks, repo notes, and local helper scripts
  for Codex.
- Claude Code project files: instructions, hooks, slash commands, repo notes,
  and local helper scripts for Claude.
- Humanizer: rewrites generated prose so docs, READMEs, and release notes sound
  less stiff.
- Context7: gives agents current library and framework docs when they need API
  details.
- Chrome DevTools MCP: lets Codex or Claude Code inspect and control Chrome for
  browser debugging.
- Ponytail: pushes agents toward smaller changes and less overbuilt code.
- Unlazy: keeps long or multi-part tasks moving until their acceptance checks
  pass.
- Graphify: maps a repo into a graph so agents can understand large codebases
  faster.
- code-review-graph: tracks code relationships so agents can review blast radius
  before changing files.
- Impeccable: helps with frontend design, layout, polish, and UI checks when
  enabled.
- LLM Council: provides optional multi-model review for high-value decisions.
- RTK: trims noisy read-only command output before it reaches the agent.
- Ruff: formats and lints Python.
- Biome: formats and checks JavaScript, TypeScript, CSS, and JSON.
- HTMLHint: checks HTML.
- markdownlint-cli2: checks Markdown files.
- ShellCheck and shfmt: check and format shell scripts.
- PHP_CodeSniffer and PHPStan: check PHP style, syntax, and types.
- Semgrep: runs optional static security checks.

## After install

Restart Codex or Claude Code in the configured repo so it reloads the new files.

For Codex, open `/hooks`, review and trust the project hooks, then start a new
thread. Hook trust cannot be automated. For Claude Code, restart in the repo
root so it reloads `CLAUDE.md` and `.claude/settings.local.json`.

Run one check after setup:

```sh
# Codex
./vibe_scripts/codex/agent-verify.sh edited
# Claude
./vibe_scripts/claude/agent-verify.sh edited
```

Trusted hooks handle normal edited-file checks after that. Pass `lint` to the
provider verifier for broader linting, or `verify` for large, risky,
security-sensitive or cross-project changes. Each verifier selects its own
Makefile. The project root Makefile is preserved.

Existing installs need `--force` to refresh managed instructions and hooks.
Legacy shared scripts and root targets remain available for manual review; new
commands use provider-specific paths. Cleanup preserves shared project data
and the other provider. See each helper README for the retained files.

## More detail

- [CodexHelper README](CodexHelper/README.md)
- [ClaudeHelper README](ClaudeHelper/README.md)
